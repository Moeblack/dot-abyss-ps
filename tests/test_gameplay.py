import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import msgpack
import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import Settings, create_app
from dotabyss_ps.gameplay import BLOCKED_ROUTES, GAMEPLAY_METHODS, Gameplay
from dotabyss_ps.gameplay_catalog import GAMEPLAY_TABLES, GameplayCatalog, GameplayError, JST, available
from dotabyss_ps.masterdata import MasterCatalog
from dotabyss_ps.storage import Store
from dotabyss_ps.server_master_bindings import SERVER_TABLES
from test_localstate import login
from test_newgame import PC_CLIENT, minimal_master

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 31, 14, tzinfo=JST)


@pytest.fixture(scope="module")
def gameplay_master():
    schema_file = ROOT / "artifacts/master-schema-pc-1.15.0.json"
    blob = ROOT / "data-pc-1.15.0/master/blobs/ee9f68157917ee9601c539f950aaf98ae9b446a54dac66707bc297746efac523.msgpack"
    if not schema_file.exists() or not blob.exists():
        pytest.skip("Verified PC master dataset is required")
    bound = json.loads(schema_file.read_text(encoding="utf-8"))
    data = msgpack.unpackb(blob.read_bytes(), raw=False)
    schema, tables = minimal_master(PC_CLIENT)
    schema["compatibility"] = bound["compatibility"]
    for name in GAMEPLAY_TABLES:
        if name in bound["tables"]:
            schema["tables"][name] = bound["tables"][name]
            tables[name] = data[name]
        elif name in SERVER_TABLES:
            tables[name] = data[name]
    return schema, tables


@pytest.fixture
def setup_gameplay(tmp_path, gameplay_master, monkeypatch):
    import dotabyss_ps.gameplay as module

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    monkeypatch.setattr(module, "datetime", Clock)
    schema, tables = gameplay_master
    schema_file = tmp_path / "schema.json"
    schema_file.write_text(json.dumps(schema), encoding="utf-8")
    payload = tmp_path / "master.msgpack"
    payload.write_bytes(msgpack.packb(tables))
    settings = replace(Settings.from_directory(tmp_path / "data"), new_game_schema=schema_file)
    store = Store(settings.data_dir / "local.sqlite3")
    masters = MasterCatalog(settings.data_dir, store)
    record = masters.stage(payload, schema_file, "Gameplay contract verification")
    masters.activate(record["revision"])
    client = TestClient(create_app(settings))
    identity, headers = login(client)
    with client:
        yield SimpleNamespace(client=client, headers=headers, identity=identity, store=store,
                              settings=settings, schema=schema, tables=tables, record=record)


def call(context, route, body=None, *, expected=200, headers=None, method=None):
    route = route.removeprefix("/")
    method = method or GAMEPLAY_METHODS.get(route, "POST")
    response = context.client.request(method, "/" + route, headers=context.headers if headers is None else headers,
                                      **({"json": body or {}} if method == "POST" else {}))
    assert response.status_code == expected, response.text[:1000]
    return response.json()["contents"]


def snapshot(context):
    with context.store.connection() as db:
        state = db.execute("SELECT contents FROM game_states WHERE profile_id=?", (context.identity["user_id"],)).fetchone()
        progress = db.execute("SELECT contents FROM gameplay_progress WHERE profile_id=?", (context.identity["user_id"],)).fetchone()
    return (state[0] if state else None, progress[0] if progress else None)


def test_tutorial_captured_sequence_restart_and_concurrent_updates(setup_gameplay):
    c = setup_gameplay
    for kind in (1, 2, 14, 15, 17, 7):
        contents = call(c, "api/tutorial/feature-exec", {"tutorial_type": kind})
    assert contents["records"]["t_feature_tutorials"]["up"][0]["completed_flags"] == 90179
    before = snapshot(c)
    assert call(c, "api/tutorial/feature-exec", {"tutorial_type": 7})["records"] == {}
    assert snapshot(c) == before
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda kind: call(c, "api/tutorial/feature-exec", {"tutorial_type": kind}), (8, 9, 10, 11)))
    with TestClient(create_app(c.settings)) as restarted:
        result = restarted.get("/api/user", headers=c.headers).json()["contents"]
    flags = result["records"]["t_feature_tutorials"]["up"][0]["completed_flags"]
    assert flags == 90179 | sum(1 << (kind - 1) for kind in (8, 9, 10, 11))


@pytest.mark.parametrize("body", [{"tutorial_type": True}, {"tutorial_type": 0}, {"tutorial_type": 20},
                                  {"tutorial_type": 1, "user_id": 2}, {"tutorial_type": "8"}])
def test_tutorial_rejects_invalid_fields_without_creating_save(setup_gameplay, body):
    c = setup_gameplay
    call(c, "api/tutorial/feature-exec", body, expected=400)
    assert snapshot(c) == (None, None)


def test_gameplay_requires_both_matching_credentials_and_method(setup_gameplay):
    c = setup_gameplay
    _, other = login(c.client, "another")
    for headers in ({}, {"x-olg-session": c.headers["x-olg-session"]}, {"api-token": c.headers["api-token"]},
                    {"x-olg-session": other["x-olg-session"], "api-token": c.headers["api-token"]}):
        call(c, "api/tutorial/feature-exec", {"tutorial_type": 8}, headers=headers, expected=401)
    call(c, "api/tutorial/feature-exec", method="GET", expected=405)
    assert snapshot(c) == (None, None)


def test_messagepack_response_and_request_match_native_types(setup_gameplay):
    c = setup_gameplay
    response = c.client.post("/api/tutorial/feature-exec", content=msgpack.packb({"tutorial_type": 8}),
        headers={**c.headers, "content-type": "application/x-msgpack", "accept": "application/x-msgpack"})
    assert response.status_code == 200
    result = msgpack.unpackb(response.content, raw=False)["contents"]
    assert result["notifies"] is None
    assert result["records"]["t_feature_tutorials"]["up"] == [
        {"id": 1, "user_id": c.identity["user_id"], "completed_flags": 128}]


def test_character_seen_validates_all_owners_before_commit(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    before = snapshot(c)
    call(c, "api/character/new/check", {"t_character_ids": [1, 999]}, expected=409)
    assert snapshot(c) == before
    result = call(c, "api/character/new/check", {"t_character_ids": [1, 2]})
    assert result["new_t_character_ids"] == [1, 2]
    characters = call(c, "api/user")["records"]["t_characters"]["up"]
    assert all(row["is_new"] == 0 for row in characters if row["id"] in (1, 2))


def test_verified_unlock_condition_and_native_boolean_persist(setup_gameplay):
    c = setup_gameplay
    call(c, "api/main-story/update-sequence", {"m_chapter_quest_id": 1010101, "sequence": 1})
    with c.store.connection() as db:
        state = json.loads(snapshot(c)[0])
        state["records"]["t_chapter_quests"]["up"][0]["sequence"] = 3
        db.execute("UPDATE game_states SET contents=? WHERE profile_id=?", (json.dumps(state), c.identity["user_id"]))
    result = call(c, "api/feature-lock/unlock", {"m_feature_lock_id": 8})
    assert result["records"]["t_feature_locks"]["up"] == [{"id": 1, "m_feature_lock_id": 8, "is_unlocked": True}]
    before = snapshot(c)
    assert call(c, "api/feature-lock/unlock", {"m_feature_lock_id": 8})["records"] == {}
    assert snapshot(c) == before


def party_body():
    return {"party_no": 1, "characters": [{"position_no": n, "t_character_id": n,
            "t_weapon_id": 0, "t_armor_id": 0, "t_accessory_id": 0} for n in (1, 2, 3)],
            "mana_gems": [{"slot_no": 1, "m_mana_gem_id": 101}, {"slot_no": 2, "m_mana_gem_id": 102}]}


def test_party_full_replace_and_mana_removal_persist(setup_gameplay):
    c = setup_gameplay
    body = party_body()
    first = call(c, "api/party/exploration/edit", body)["records"]
    assert len(first["t_exploration_party_characters"]["up"]) == 3
    assert call(c, "api/party/exploration/edit", body)["records"] == {}
    removed = first["t_exploration_party_mana_gems"]["up"][1]["id"]
    removed_member = first["t_exploration_party_characters"]["up"][2]["id"]
    body["mana_gems"].pop()
    body["characters"].pop()
    delta = call(c, "api/party/exploration/edit", body)["records"]
    assert delta["t_exploration_party_mana_gems"] == {"up": [], "down": [removed]}
    assert delta["t_exploration_party_characters"] == {"up": [], "down": [removed_member]}
    assert len(call(c, "api/user")["records"]["t_exploration_party_mana_gems"]["up"]) == 1


@pytest.mark.parametrize("damage,status", [
    (lambda b: b["characters"][0].update(t_character_id=999), 409),
    (lambda b: b["characters"][1].update(t_character_id=1), 400),
    (lambda b: b["characters"][0].update(t_weapon_id=999), 409),
    (lambda b: b["characters"][1].update(position_no=1), 400),
    (lambda b: b["mana_gems"][1].update(m_mana_gem_id=101), 409),
    (lambda b: b.update(party_no=2), 400),
])
def test_invalid_party_rolls_back(setup_gameplay, damage, status):
    c = setup_gameplay
    call(c, "api/party/exploration/edit", party_body())
    before, body = snapshot(c), party_body()
    damage(body)
    call(c, "api/party/exploration/edit", body, expected=status)
    assert snapshot(c) == before


def test_story_sequence_prevents_skips_battle_bypass_and_unlock(setup_gameplay):
    c = setup_gameplay
    quest = {"m_chapter_quest_id": 1010101}
    call(c, "api/main-story/update-sequence", {**quest, "sequence": 2}, expected=409)
    for n in (1, 1, 2):
        call(c, "api/main-story/update-sequence", {**quest, "sequence": n})
    before = snapshot(c)
    call(c, "api/main-story/update-sequence", {**quest, "sequence": 3}, expected=409)
    call(c, "api/feature-lock/unlock", {"m_feature_lock_id": 8}, expected=409)
    assert snapshot(c) == before


def test_exchange_reads_are_persistent_and_profile_specific(setup_gameplay):
    c = setup_gameplay
    groups = call(c, "api/exchange/lineup")["categories"]
    identifier = groups[0]["lineups"][0]["m_exchange_lineup_id"]
    call(c, "api/exchange/read/lineup", {"m_exchange_lineup_ids": [identifier]})
    def flag(headers=None):
        return next(r["is_new"] for g in call(c, "api/exchange/lineup", headers=headers)["categories"]
                    for r in g["lineups"] if r["m_exchange_lineup_id"] == identifier)
    assert flag() == 0
    _, other = login(c.client, "another")
    assert flag(other) == 1
    before = snapshot(c)
    call(c, "api/exchange/read/lineup", {"m_exchange_lineup_ids": [999999999]}, expected=409)
    assert snapshot(c) == before


def test_known_rewards_are_atomic_and_replay_safe(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    before = snapshot(c)
    call(c, "api/mission/reward/receive", {"m_mission_ids": [2001, 100901]}, expected=409)
    assert snapshot(c) == before
    call(c, "api/mission/reward/receive", {"m_mission_ids": [2001, 100801]})
    awarded = snapshot(c)
    assert awarded != before
    assert call(c, "api/mission/reward/receive", {"m_mission_ids": [2001, 100801]})["records"] == {}
    assert snapshot(c) == awarded
    listing = call(c, "api/mission/list")
    assert next(m for m in listing["daily"] if m["m_mission_id"] == 2001)["is_received"] == 1
    call(c, "api/special-mission/top", {"m_special_mission_id": 101})
    call(c, "api/special-mission/mission-reward/receive", {"m_special_mission_id": 101, "m_mission_ids": [70001]})
    items = call(c, "api/user")["records"]["t_items"]["up"]
    assert next(i for i in items if i["m_item_id"] == 170005)["amount"] == 3
    before = snapshot(c)
    call(c, "api/special-mission/mission-reward/receive", {"m_special_mission_id": 101, "m_mission_ids": [70002]}, expected=409)
    assert snapshot(c) == before


def test_concurrent_reward_receives_grant_only_once(setup_gameplay):
    c = setup_gameplay
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(lambda _: call(c, "api/mission/reward/receive", {"m_mission_ids": [2001]}), range(4)))
    assert sum(bool(result["records"]) for result in responses) == 1
    assert len(json.loads(snapshot(c)[1])["mission_claims"]) == 1


def test_greeting_home_flags_and_daily_receipts(setup_gameplay, monkeypatch):
    c = setup_gameplay
    monkeypatch.setattr("dotabyss_ps.gameplay_home.secrets.randbelow", lambda n: 0)
    greeting = call(c, "api/home")["daily_greetings"]
    assert [r["m_home_daily_greeting_id"] for r in greeting] == [3, 149, 205]
    assert greeting[0]["is_received"] == 0
    body = {"m_home_daily_greeting_id": 3, "m_home_visitor_daily_greeting_id": 0}
    call(c, "api/home/daily-greeting/receive-reward", body)
    before = snapshot(c)
    assert call(c, "api/home/daily-greeting/receive-reward", body)["records"] == {}
    assert snapshot(c) == before
    assert call(c, "api/home")["daily_greetings"][0]["is_received"] == 1
    state, progress = map(json.loads, snapshot(c))
    game = Gameplay(state, progress, GameplayCatalog(c.tables, c.schema), c.identity["user_id"], NOW + timedelta(days=1))
    game.execute("api/home/daily-greeting/receive-reward", "POST", body)
    item = next(r for r in state["records"]["t_items"]["up"] if r["m_item_id"] == 20000)
    assert item["amount"] == 30


@pytest.mark.parametrize("route", list(BLOCKED_ROUTES))
def test_incomplete_contracts_never_mutate_or_charge(setup_gameplay, route):
    c = setup_gameplay
    call(c, "api/user")
    before = snapshot(c)
    call(c, route, {"anything": 1}, expected=501)
    assert snapshot(c) == before


@pytest.mark.parametrize("route", ["api/shop/top", "api/quest/top", "api/gacha", "api/mission/list"])
def test_catalog_views_use_bound_master_without_capture_placeholders(setup_gameplay, route):
    result = call(setup_gameplay, route)
    encoded = json.dumps(result)
    assert "[redacted]" not in encoded and "[omitted:" not in encoded
    assert result["notifies"] is None


def test_all_sampled_routes_have_explicit_dispatch():
    folder = ROOT / "captures/20261031-025842598-a3a7a9e5c32249308ff906a6c0089b4f/records"
    sampled = {json.loads(p.read_text(encoding="utf-8"))["route"].lstrip("/") for p in folder.glob("*.json")}
    existing = {"api/user", "api/home", "api/mypage/login", "api/terms/fetch-agreements", "api/battle-session/status"}
    assert len(sampled) == 23
    assert sampled <= set(GAMEPLAY_METHODS) | existing


@pytest.mark.parametrize("stamp", ["2026-10-31 4:59:59", "2026-10-31 04:59:59", "2026-10-31T04:59:59+09:00"])
def test_master_availability_accepts_observed_date_formats(stamp):
    assert available({"start_at": stamp}, NOW)
    assert not available({"end_at": stamp}, NOW)


@pytest.mark.parametrize("bad", ["tomorrow", "2026-13-31 04:59:59", 123, "2026-10-31 24:00:00"])
def test_master_availability_rejects_invalid_dates(bad):
    with pytest.raises(GameplayError, match="date is invalid"):
        available({"start_at": bad}, NOW)


def test_unknown_reward_type_rolls_back_prior_grants(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    before = snapshot(c)
    from dotabyss_ps.gameplay_records import grant
    catalog = GameplayCatalog(c.tables, c.schema)
    def operation(state, progress):
        grant(state, [{"content_type": 30, "content_id": 170005, "amount": 3},
                      {"content_type": 999, "content_id": 0, "amount": 1}], catalog, c.identity["user_id"], NOW)
        return {}
    with pytest.raises(GameplayError, match="Reward type"):
        c.store.game_state(c.identity["user_id"], c.record["revision"], lambda *_: None, operation)
    assert snapshot(c) == before


def test_corrupt_receipts_do_not_allow_reward_duplication(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    with c.store.connection() as db:
        progress = json.loads(snapshot(c)[1])
        progress["mission_claims"] = {"2026-10-31:2001": "corrupt"}
        db.execute("UPDATE gameplay_progress SET contents=? WHERE profile_id=?", (json.dumps(progress), c.identity["user_id"]))
    before = snapshot(c)
    call(c, "api/mission/reward/receive", {"m_mission_ids": [2001]}, expected=409)
    assert snapshot(c) == before
