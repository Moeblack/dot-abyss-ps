import copy
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace

import msgpack
import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import Settings, create_app
from dotabyss_ps.masterdata import MasterCatalog
from dotabyss_ps.storage import Store
from test_newgame import PC_CLIENT, PLAY_CLIENT, minimal_master


@pytest.fixture
def local_setup(tmp_path, request):
    schema, tables = minimal_master(getattr(request, "param", None))
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    settings = replace(Settings.from_directory(tmp_path / "data"), new_game_schema=schema_path)
    store = Store(settings.data_dir / "local.sqlite3")
    catalog = MasterCatalog(settings.data_dir, store)

    def stage(candidate_tables=None, candidate_schema=None):
        payload_path = tmp_path / "input.msgpack"
        payload_path.write_bytes(msgpack.packb(tables if candidate_tables is None else candidate_tables))
        layout = tmp_path / "input-schema.json"
        layout.write_text(json.dumps(schema if candidate_schema is None else candidate_schema), encoding="utf-8")
        return catalog.stage(payload_path, layout, "Synthetic integration fixture")

    record = stage()
    catalog.activate(record["revision"])
    app = create_app(settings)
    with TestClient(app) as client:
        yield SimpleNamespace(settings=settings, store=store, catalog=catalog, app=app, client=client,
                              schema=schema, tables=tables, stage=stage, record=record)


def login(client, profile="local-test"):
    response = client.post("/local/login", json={"profile": profile})
    assert response.status_code == 200
    result = response.json()
    return result["contents"], {"x-olg-session": result["contents"]["token"], "api-token": result["api_token"]}


def game(client, headers, route="/api/user", body=None):
    response = client.post(route, headers=headers, json={} if body is None else body)
    assert response.status_code == 200, response.json().get("errors")
    return response.json()


def saved(store, user_id):
    with store.connection() as db:
        row = db.execute("SELECT * FROM game_states WHERE profile_id=?", (user_id,)).fetchone()
    return dict(row) if row else None


def replace_state(store, user_id, state):
    with store.connection() as db:
        db.execute("UPDATE game_states SET contents=? WHERE profile_id=?", (json.dumps(state), user_id))


def different_tables(context):
    tables = copy.deepcopy(context.tables)
    columns = context.schema["tables"]["m_user_ranks"]
    exp_column = next(index for index, column in enumerate(columns) if column["name"] == "total_exp")
    tables["m_user_ranks"][0][exp_column] += 1
    return tables


def test_existing_identity_gains_one_persistent_save_without_reissuing_content(local_setup):
    context = local_setup
    identity, headers = login(context.client)
    first = game(context.client, headers)
    assert first["versions"]["resource"] == [context.record["revision"]]
    characters = first["contents"]["records"]["t_characters"]["up"]
    assert characters
    state = json.loads(saved(context.store, identity["user_id"])["contents"])
    state["records"]["t_statuses"]["up"][0]["gold"] = 17
    replace_state(context.store, identity["user_id"], state)

    repeated_identity, repeated_headers = login(context.client)
    assert repeated_identity["user_id"] == identity["user_id"]
    after_login = game(context.client, repeated_headers)["contents"]["records"]
    assert after_login["t_statuses"]["up"][0]["gold"] == 17
    assert after_login["t_characters"]["up"] == characters

    with TestClient(create_app(context.settings)) as restarted:
        after_restart = game(restarted, repeated_headers)["contents"]["records"]
        assert after_restart == after_login
    assert context.store.game_state_summary()["profile_count"] == 1


@pytest.mark.parametrize("local_setup", [None, PLAY_CLIENT, PC_CLIENT], indirect=True, ids=["fanza", "play", "pc"])
def test_home_and_user_share_state_and_wire_encodings(local_setup):
    context = local_setup
    identity, headers = login(context.client)
    home = game(context.client, headers, "/api/home")
    user = game(context.client, headers)
    assert home["contents"]["records"] == user["contents"]["records"]
    assert home["versions"]["resource"] == user["versions"]["resource"]
    assert "unresolved_initial_contents" not in home["contents"]
    assert "policy_version" not in user["contents"]
    snapshot = saved(context.store, identity["user_id"])
    assert snapshot
    announcement = context.client.get("/api/mypage/login", headers=headers)
    assert announcement.status_code == 200
    assert announcement.json()["contents"]["records"] == user["contents"]["records"]
    for route, payload in (("/api/user", user), ("/api/home", home), ("/api/mypage/login", announcement.json())):
        assert payload["contents"]["notifies"] is None
        response = context.client.post(route, content=msgpack.packb({}),
                                       headers={**headers, "content-type": "application/x-msgpack", "accept": "application/x-msgpack"})
        assert response.status_code == 200
        packed = msgpack.unpackb(response.content, raw=False)
        assert packed["contents"] == payload["contents"]
        assert packed["contents"]["notifies"] is None
    assert saved(context.store, identity["user_id"]) == snapshot
    diagnostics = context.client.get("/diagnostics").json()
    assert diagnostics["bootstrap_ready"] is False
    assert diagnostics["local_state"]["profile_count"] == 1
    assert diagnostics["local_state"]["device_verified"] is False


def test_profiles_are_isolated_and_request_body_cannot_select_owner(local_setup):
    context = local_setup
    first, first_headers = login(context.client, "first")
    second, second_headers = login(context.client, "second")
    assert first["user_id"] != second["user_id"]
    game(context.client, first_headers)
    game(context.client, second_headers)
    first_state = json.loads(saved(context.store, first["user_id"])["contents"])
    first_state["records"]["t_statuses"]["up"][0]["gold"] = 31
    replace_state(context.store, first["user_id"], first_state)
    response = game(context.client, second_headers, body={"user_id": first["user_id"], "profile": "first", "uuid": first["uuid"]})
    records = response["contents"]["records"]
    status = records["t_statuses"]["up"][0]
    assert status["user_id"] == second["user_id"]
    assert status["name"] == "second"
    assert status["gold"] == 0
    for collection in records.values():
        for row in collection["up"]:
            if "user_id" in row:
                assert row["user_id"] == second["user_id"]
    assert context.store.game_state_summary()["profile_count"] == 2


def test_concurrent_first_requests_create_one_save(local_setup, monkeypatch):
    context = local_setup
    _, headers = login(context.client)
    import dotabyss_ps.localstate as implementation

    original = implementation.build_initial_state
    factory_calls = []
    lock = threading.Lock()

    def counted(*args):
        with lock:
            factory_calls.append(args[2])
        return original(*args)

    monkeypatch.setattr(implementation, "build_initial_state", counted)
    barrier = threading.Barrier(6)

    def request(_):
        with TestClient(context.app) as client:
            barrier.wait(timeout=10)
            return game(client, headers)["contents"]["records"]

    with ThreadPoolExecutor(max_workers=6) as pool:
        records = list(pool.map(request, range(6)))
    assert all(result == records[0] for result in records)
    assert len(factory_calls) == 1
    assert context.store.game_state_summary()["profile_count"] == 1


@pytest.mark.parametrize("invalid_auth", ["missing", "wrong-token", "expired"])
def test_failed_auth_never_creates_state(local_setup, invalid_auth):
    context = local_setup
    _, headers = login(context.client)
    if invalid_auth == "missing":
        headers = {}
    elif invalid_auth == "wrong-token":
        headers["api-token"] = "incorrect"
    else:
        with context.store.connection() as db:
            db.execute("UPDATE sessions SET expires_at=0")
    for route in ("/api/user", "/api/home"):
        assert context.client.post(route, json={}, headers=headers).status_code == 401
    assert context.store.game_state_summary()["profile_count"] == 0


def test_state_routes_cannot_be_overridden_by_fixtures(local_setup):
    context = local_setup
    fixture = context.settings.fixtures_dir / "api/user.json"
    fixture.parent.mkdir(parents=True)
    fixture.write_text('{"contents":{"records":{"foreign":"data"}}}', encoding="utf-8")
    _, headers = login(context.client)
    records = game(context.client, headers)["contents"]["records"]
    assert "foreign" not in records
    assert records["t_characters"]["up"]


def test_missing_active_master_fails_without_creating_save(local_setup):
    context = local_setup
    with context.store.connection() as db:
        db.execute("DELETE FROM settings WHERE name='active_master'")
    _, headers = login(context.client)
    response = context.client.post("/api/home", json={}, headers=headers)
    assert response.status_code == 503
    assert "active master" in response.json()["errors"]["message"]
    assert context.store.game_state_summary()["profile_count"] == 0


def test_invalid_seed_reference_rolls_back_first_save(local_setup):
    context = local_setup
    tables = copy.deepcopy(context.tables)
    tables["m_degrees"] = []
    record = context.stage(tables)
    context.catalog.activate(record["revision"])
    _, headers = login(context.client)
    response = context.client.post("/api/user", json={}, headers=headers)
    assert response.status_code == 503
    assert "missing referenced ID" in response.json()["errors"]["message"]
    assert context.store.game_state_summary()["profile_count"] == 0
    context.catalog.activate(context.record["revision"])
    assert game(context.client, headers)["contents"]["records"]["t_degrees"]["up"]


@pytest.mark.parametrize("options", ['{"intimacy_point":true}', "[", '{"gold":1000}'])
def test_invalid_initial_options_cannot_leave_a_partial_save(local_setup, options):
    context = local_setup
    tables = copy.deepcopy(context.tables)
    options_column = next(index for index, column in enumerate(context.schema["tables"]["m_initial_contents"])
                          if column["name"] == "options")
    tables["m_initial_contents"][0][options_column] = options
    record = context.stage(tables)
    context.catalog.activate(record["revision"])
    _, headers = login(context.client)
    response = context.client.post("/api/user", json={}, headers=headers)
    assert response.status_code == 503
    assert context.store.game_state_summary()["profile_count"] == 0
    context.catalog.activate(context.record["revision"])
    game(context.client, headers)
    assert context.store.game_state_summary()["profile_count"] == 1


def test_corrupt_master_fails_before_save_creation(local_setup):
    context = local_setup
    payload = context.settings.data_dir / context.record["payload_path"]
    payload.write_bytes(b"broken")
    _, headers = login(context.client)
    response = context.client.post("/api/user", json={}, headers=headers)
    assert response.status_code == 503
    assert context.store.game_state_summary()["profile_count"] == 0


def test_revision_change_requires_migration_and_rollback_restores_save(local_setup):
    context = local_setup
    identity, headers = login(context.client)
    before = game(context.client, headers)["contents"]
    persisted = saved(context.store, identity["user_id"])
    replacement = context.stage(different_tables(context))
    context.catalog.activate(replacement["revision"])
    response = context.client.post("/api/user", json={}, headers=headers)
    assert response.status_code == 409
    assert "migration" in response.json()["errors"]["message"]
    assert saved(context.store, identity["user_id"]) == persisted
    context.catalog.activate(context.record["revision"])
    assert game(context.client, headers)["contents"] == before


def test_different_schema_is_rejected_even_with_same_client_identity(local_setup):
    context = local_setup
    schema = copy.deepcopy(context.schema)
    schema["tables"]["m_degrees"].append({"name": "unrelated", "type": "int"})
    tables = copy.deepcopy(context.tables)
    for row in tables["m_degrees"]:
        row.append(0)
    candidate = context.stage(tables, schema)
    context.catalog.activate(candidate["revision"])
    _, headers = login(context.client)
    response = context.client.post("/api/user", json={}, headers=headers)
    assert response.status_code == 503
    assert "schema" in response.json()["errors"]["message"]
    assert context.store.game_state_summary()["profile_count"] == 0


def test_response_keeps_transaction_resource_version_during_concurrent_activation(local_setup, monkeypatch):
    context = local_setup
    _, headers = login(context.client)
    replacement = context.stage(different_tables(context))
    original = Store.game_state

    def activate_after_snapshot(store, *args):
        result = original(store, *args)
        context.catalog.activate(replacement["revision"])
        return result

    monkeypatch.setattr(Store, "game_state", activate_after_snapshot)
    response = game(context.client, headers)
    assert response["versions"]["resource"] == [context.record["revision"]]
    assert context.store.runtime()["resource_version"] == replacement["revision"]


@pytest.mark.parametrize("local_setup", [PLAY_CLIENT, PC_CLIENT], indirect=True, ids=["play", "pc"])
def test_user_and_home_initialize_persist_and_use_matching_resource_version(local_setup):
    context = local_setup
    context.store.update_runtime({"application_version": "1.15.0"})
    identity, headers = login(context.client, "local-player")
    user = game(context.client, headers)
    home = game(context.client, headers, "/api/home")
    assert user["contents"]["records"] == home["contents"]["records"]
    assert user["versions"]["resource"] == [context.record["revision"]]
    version_field = "ClientVersionStandaloneDmmR18" if context.schema["client"] == PC_CLIENT else "ClientVersionAndroidGooglePlayGeneral"
    assert user["versions"][version_field] == ["1.15.0"]
    assert context.store.master()["client"] == context.schema["client"]
    assert len(user["contents"]["records"]["t_characters"]["up"]) == 3
    persisted = saved(context.store, identity["user_id"])
    with TestClient(create_app(context.settings)) as restarted:
        assert game(restarted, headers)["contents"] == user["contents"]
    assert saved(context.store, identity["user_id"]) == persisted


@pytest.mark.parametrize("local_setup,other_client", [
    (None, PLAY_CLIENT), (None, PC_CLIENT), (PLAY_CLIENT, None),
    (PLAY_CLIENT, PC_CLIENT), (PC_CLIENT, None), (PC_CLIENT, PLAY_CLIENT),
], indirect=["local_setup"], ids=["fanza-play", "fanza-pc", "play-fanza", "play-pc", "pc-fanza", "pc-play"])
def test_active_dataset_from_other_supported_client_is_rejected(local_setup, other_client):
    context = local_setup
    identity, headers = login(context.client)
    original = game(context.client, headers)["contents"]
    existing = saved(context.store, identity["user_id"])
    other_schema, _ = minimal_master(other_client)
    candidate = context.stage(candidate_schema=other_schema)
    context.catalog.activate(candidate["revision"])
    response = context.client.post("/api/user", json={}, headers=headers)
    assert response.status_code == 503
    assert "configured new game schema" in response.json()["errors"]["message"]
    assert saved(context.store, identity["user_id"]) == existing
    context.catalog.activate(context.record["revision"])
    assert game(context.client, headers)["contents"] == original


@pytest.mark.parametrize("field,value", [
    ("package", "com.unverified.abyss"), ("version", "1.15.1"), ("metadata_sha256", "0" * 64),
])
@pytest.mark.parametrize("client", [PLAY_CLIENT, PC_CLIENT], ids=["play", "pc"])
def test_service_rejects_unverified_schema_before_serving(local_setup, client, field, value):
    context = local_setup
    schema, _ = minimal_master(client)
    schema["client"][field] = value
    context.settings.new_game_schema.write_text(json.dumps(schema), encoding="utf-8")
    with pytest.raises(ValueError, match="verified client"):
        create_app(context.settings)


@pytest.mark.parametrize("damage", ["schema", "policy", "json", "shape", "missing-status", "invalid-up", "foreign-owner"])
def test_incompatible_or_corrupt_save_fails_without_reset(local_setup, damage):
    context = local_setup
    identity, headers = login(context.client)
    game(context.client, headers)
    state = json.loads(saved(context.store, identity["user_id"])["contents"])
    with context.store.connection() as db:
        if damage == "schema":
            db.execute("UPDATE game_states SET schema_version=999")
        else:
            if damage == "policy":
                state["policy_version"] = 999
            elif damage == "missing-status":
                del state["records"]["t_statuses"]
            elif damage == "invalid-up":
                state["records"]["t_characters"]["up"] = {}
            elif damage == "foreign-owner":
                for container in state["records"].values():
                    for row in container["up"]:
                        if "user_id" in row:
                            row["user_id"] = identity["user_id"] + 1
            encoded = "{" if damage == "json" else json.dumps([] if damage == "shape" else state)
            db.execute("UPDATE game_states SET contents=?", (encoded,))
    damaged = saved(context.store, identity["user_id"])
    with TestClient(context.app, raise_server_exceptions=False) as client:
        response = client.post("/api/user", json={}, headers=headers)
    assert response.status_code in (409, 503)
    assert saved(context.store, identity["user_id"]) == damaged


@pytest.mark.parametrize("local_setup", [None, PLAY_CLIENT, PC_CLIENT], indirect=True, ids=["fanza", "play", "pc"])
@pytest.mark.parametrize("accept", ["application/json", "application/x-msgpack"])
def test_get_login_announcement_initializes_once_with_nonnull_nested_fields(local_setup, accept):
    context = local_setup
    identity, headers = login(context.client, "announcement-player")
    assert saved(context.store, identity["user_id"]) is None
    response = context.client.get("/api/mypage/login", headers={**headers, "accept": accept})
    assert response.status_code == 200
    result = msgpack.unpackb(response.content, raw=False) if accept.endswith("msgpack") else response.json()
    contents = result["contents"]
    assert contents["login_bonus_list"] == {"daily": [], "special": [], "special_condition": []}
    assert contents["friend_point_data"] == {"follow_count": 0, "get_friend_point": 0}
    assert type(contents["has_new_shop"]) is int and contents["has_new_shop"] == 0
    for key in ("announcement_ids", "expired_limited_items", "expires_soon_t_limited_item_ids", "t_login_thirty_days_bonuses", "t_login_limited_bonuses"):
        assert contents[key] == []
    assert result["versions"]["resource"] == [context.record["revision"]]
    first_save = saved(context.store, identity["user_id"])
    assert game(context.client, headers, "/api/mypage/login")["contents"] == contents
    for route in ("/api/user", "/api/home"):
        assert game(context.client, headers, route)["contents"]["records"] == contents["records"]
    assert saved(context.store, identity["user_id"]) == first_save
    assert context.store.game_state_summary()["profile_count"] == 1


@pytest.mark.parametrize("local_setup", [None, PLAY_CLIENT, PC_CLIENT], indirect=True, ids=["fanza", "play", "pc"])
def test_login_announcements_preserve_existing_balances_inventory_and_save(local_setup):
    context = local_setup
    identity, headers = login(context.client)
    game(context.client, headers)
    state = json.loads(saved(context.store, identity["user_id"])["contents"])
    state["records"]["t_statuses"]["up"][0].update(gold=123, friend_point=456)
    replace_state(context.store, identity["user_id"], state)
    before = saved(context.store, identity["user_id"])
    for _ in range(3):
        response = context.client.get("/api/mypage/login", headers=headers)
        assert response.status_code == 200
        contents = response.json()["contents"]
        assert contents["records"] == state["records"]
        assert contents["friend_point_data"]["get_friend_point"] == 0
        assert saved(context.store, identity["user_id"]) == before


@pytest.mark.parametrize("local_setup", [None, PLAY_CLIENT, PC_CLIENT], indirect=True, ids=["fanza", "play", "pc"])
def test_get_login_announcements_are_bound_to_authenticated_owner(local_setup):
    context = local_setup
    first, first_headers = login(context.client, "first-announcement")
    second, second_headers = login(context.client, "second-announcement")
    first_response = context.client.get("/api/mypage/login", headers=first_headers)
    assert first_response.status_code == 200
    response = context.client.get("/api/mypage/login", params={"user_id": first["user_id"], "profile": "first-announcement"}, headers=second_headers)
    assert response.status_code == 200
    records = response.json()["contents"]["records"]
    assert records["t_statuses"]["up"][0]["name"] == "second-announcement"
    assert all(row["user_id"] == second["user_id"] for collection in records.values() for row in collection["up"] if "user_id" in row)
    assert saved(context.store, first["user_id"])["contents"] != saved(context.store, second["user_id"])["contents"]


@pytest.mark.parametrize("local_setup", [None, PLAY_CLIENT, PC_CLIENT], indirect=True, ids=["fanza", "play", "pc"])
@pytest.mark.parametrize("invalid_auth", ["missing", "wrong-token", "expired"])
def test_get_login_announcements_reject_invalid_sessions_without_initializing_state(local_setup, invalid_auth):
    context = local_setup
    _, headers = login(context.client)
    if invalid_auth == "missing":
        headers = {}
    elif invalid_auth == "wrong-token":
        headers["api-token"] = "invalid"
    else:
        with context.store.connection() as db:
            db.execute("UPDATE sessions SET expires_at=0")
    assert context.client.get("/api/mypage/login", headers=headers).status_code == 401
    assert context.store.game_state_summary()["profile_count"] == 0
