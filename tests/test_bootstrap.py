import json
from pathlib import Path

import msgpack
import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import MAX_BODY_BYTES, RequestFailure, Settings, contained_path, create_app
from dotabyss_ps.protocol import REDACTED, sanitize, version_fields


@pytest.fixture
def setup(tmp_path):
    settings = Settings.from_directory(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        yield settings, app.state.store, client


def session(client):
    response = client.post("/local/login", json={"profile": "test"})
    assert response.status_code == 200
    body = response.json()
    return body, {"x-olg-session": body["contents"]["token"], "api-token": body["api_token"]}


def test_login_contract_and_persistent_identity(setup):
    settings, store, client = setup
    first, first_headers = session(client)
    second, _ = session(client)
    assert first["status"] == "success"
    assert isinstance(first["server_time"], str)
    assert isinstance(first["timestamp"], int)
    assert isinstance(first["errors"], dict)
    assert first["contents"]["user_id"] == second["contents"]["user_id"]
    assert first["contents"]["uuid"] == second["contents"]["uuid"]
    assert first["contents"]["token"] != second["contents"]["token"]
    with TestClient(create_app(settings)) as restarted:
        third, _ = session(restarted)
        assert first["contents"]["uuid"] == third["contents"]["uuid"]
    with store.connection() as db:
        persisted = str(db.execute("SELECT * FROM sessions").fetchall())
    assert first_headers["x-olg-session"] not in persisted


def test_external_credentials_rejected(setup):
    client = setup[2]
    response = client.post("/local/login", json={"token": "official-secret"})
    assert response.status_code == 400
    assert "official-secret" not in client.get("/diagnostics").text


def test_game_login_alias_is_explicit(setup):
    settings, _, client = setup
    assert client.post("/login", json={}).status_code == 401
    body, headers = session(client)
    assert client.post("/login", headers=headers, json={}).json()["contents"]["user_id"] == body["contents"]["user_id"]
    aliased = Settings(settings.data_dir, settings.fixtures_dir, settings.resources_dir, "/verified-login")
    with TestClient(create_app(aliased)) as configured:
        assert configured.post("/verified-login", json={}).status_code == 401
        assert configured.post("/verified-login", headers=headers, json={}).json()["status"] == "success"


def test_session_required_and_api_token_validated(setup):
    client = setup[2]
    assert client.post("/api/home", json={}).status_code == 401
    _, headers = session(client)
    headers["api-token"] = "wrong"
    assert client.post("/api/home", headers=headers, json={}).status_code == 401


def test_expired_session_cannot_read_fixture(setup):
    _, store, client = setup
    _, headers = session(client)
    with store.connection() as db:
        db.execute("UPDATE sessions SET expires_at=0")
    assert client.post("/api/home", headers=headers, json={}).status_code == 401


def test_missing_bootstrap_is_not_reported_as_success(setup):
    client = setup[2]
    _, headers = session(client)
    response = client.post("/api/home", json={}, headers=headers)
    assert response.status_code == 503
    assert response.json()["errors"]["code"] == 5031
    diagnostics = client.get("/diagnostics").json()
    assert diagnostics["fixtures"]["/api/home"] == "missing"
    assert diagnostics["bootstrap_ready"] is False
    assert diagnostics["observations"][0]["path"] == "/api/home"


def test_exact_route_fixture_and_no_basename_collision(setup):
    settings, _, client = setup
    fixture = settings.fixtures_dir / "api/home.json"
    fixture.parent.mkdir()
    fixture.write_text(json.dumps({"contents": {"records": {"items": []}, "daily_greetings": []}}), encoding="utf-8")
    _, headers = session(client)
    response = client.post("/api/home", headers=headers, json={"token": "should-not-be-recorded"})
    assert response.json()["contents"]["records"] == {"items": []}
    assert client.post("/another/home", headers=headers, json={}).status_code == 404
    diagnostics = client.get("/diagnostics")
    assert "should-not-be-recorded" not in diagnostics.text
    assert headers["x-olg-session"] not in diagnostics.text
    assert headers["api-token"] not in diagnostics.text


@pytest.mark.parametrize("data", ["{", "[]", '{"contents":null}', '{"status":"success"}', '{"contents":{"value":NaN}}', '{"contents":{"value":1e999}}', '{"contents":{"value":"\\ud800"}}'])
def test_invalid_fixtures_fail_explicitly(setup, data):
    settings, _, client = setup
    fixture = settings.fixtures_dir / "api/home.json"
    fixture.parent.mkdir()
    fixture.write_text(data, encoding="utf-8")
    _, headers = session(client)
    assert client.post("/api/home", headers=headers, json={}).status_code == 503
    assert client.get("/diagnostics").json()["fixtures"]["/api/home"] == "invalid"


def test_runtime_registration_uses_observed_versions(setup):
    client = setup[2]
    values = {"application_version": "1.14.4", "app_version_code": "1", "bundle_version": "observed-bundle", "server_version": "observed-server", "resource_version": "observed-master"}
    assert client.post("/local/runtime", json=values).status_code == 200
    body, _ = session(client)
    assert body["versions"]["ClientVersionAndroidDmmR18"] == ["1.14.4"]
    assert body["versions"]["AssetVersionAndroidDmmR18"] == ["observed-bundle"]
    assert body["versions"]["resource"] == ["observed-master"]
    assert client.post("/local/runtime", json={"unknown": "x"}).status_code == 400


def test_manifest_version_is_not_inferred_from_runtime_code():
    versions = version_fields({"app_version_code": "1", "bundle_version": "0.1.0"})
    assert versions["ClientVersionAndroidDmmR18"] == []


def test_version_fields_match_exported_android_contract():
    artifact = Path(__file__).parents[1] / "artifacts/protocol.json"
    document = json.loads(artifact.read_text(encoding="utf-8"))
    typedef = next(item for item in document["types"] if item["full_name"] == "Il2CppAbsf.Api.VersionEntity")
    assert set(version_fields({})) == {field["name"] for field in typedef["fields"]}
    assert all(isinstance(value, list) for value in version_fields({}).values())


def test_messagepack_request_and_response(setup):
    client = setup[2]
    response = client.post("/local/login", content=msgpack.packb({"profile": "msgpack"}), headers={"content-type": "application/x-msgpack", "accept": "application/x-msgpack"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-msgpack"
    assert msgpack.unpackb(response.content, raw=False)["contents"]["uuid"]


@pytest.mark.parametrize("payload,content_type", [(b"[", "application/json"), (b"[]", "application/json"), (b"{\"x\":NaN}", "application/json"), (b"\xc1", "application/x-msgpack"), (b"raw", "application/octet-stream")])
def test_malformed_or_unsupported_transport(setup, payload, content_type):
    response = setup[2].post("/local/login", content=payload, headers={"content-type": content_type})
    assert response.status_code in (400, 415)
    assert response.json()["status"] == "error"


def test_body_limit_and_compressed_transport(setup):
    client = setup[2]
    assert client.post("/local/login", content=b" " * (MAX_BODY_BYTES + 1)).status_code == 413
    assert client.post("/local/login", content=b"compressed", headers={"content-encoding": "gzip"}).status_code == 415


@pytest.mark.parametrize("path", ["../secret", "api/../../secret", "api\\secret", "C:/secret", "api//home", "api/./home"])
def test_paths_cannot_escape_data_directory(tmp_path, path):
    with pytest.raises(RequestFailure):
        contained_path(tmp_path, path)


@pytest.mark.parametrize("relative", ["resources/android/r18/catalog_1.bin", "data/first.dat", "master/first.dat"])
def test_resources_preserve_full_android_path(setup, relative):
    settings, _, client = setup
    target = settings.resources_dir / relative
    target.parent.mkdir(parents=True)
    target.write_bytes(b"catalog-test")
    response = client.get("/" + relative)
    assert response.content == b"catalog-test"
    assert client.get("/resources/windows/r18/catalog_1.bin").status_code == 404


def test_recursive_sanitization():
    assert sanitize({"list": [{"api-token": "secret", "value": 2}], "Authorization": "secret", "bytes": b"payload"}) == {"list": [{"api-token": REDACTED, "value": 2}], "Authorization": REDACTED, "bytes": {"binary_bytes": 7}}


def test_boolean_comparisons_are_visible_without_exposing_credentials():
    assert sanitize({"api_token_matches": True, "native_session_matches": False, "encrypted_token_populated": True,
                     "api_token": "secret", "api_token_matches_text": "secret", "api_token_matches_fake": 1234}) == {
        "api_token_matches": True, "native_session_matches": False, "encrypted_token_populated": True,
        "api_token": REDACTED, "api_token_matches_text": REDACTED, "api_token_matches_fake": REDACTED}


@pytest.mark.parametrize("path,payload", [("/local/runtime", b'{"app_version_code":"\\ud800"}'), ("/local/login", b'{"\\ud800":1}'), ("/local/login", b'{"nested":[{"value":"\\udfff"}]}')])
def test_invalid_unicode_does_not_poison_persistence(setup, path, payload):
    _, store, client = setup
    response = client.post(path, content=payload, headers={"content-type": "application/json"})
    assert response.status_code == 400
    assert store.runtime() == {}
    assert client.get("/diagnostics").status_code == 200
    assert client.post("/local/login", json={}).status_code == 200


def test_messagepack_nonfinite_and_deep_objects_rejected(setup):
    client = setup[2]
    response = client.post("/local/login", content=msgpack.packb({"value": float("nan")}), headers={"content-type": "application/x-msgpack"})
    assert response.status_code == 400
    payload = b'{"nested":' * 70 + b'{}' + b'}' * 70
    assert client.post("/local/login", content=payload, headers={"content-type": "application/json"}).status_code == 400


def test_client_diagnostics_require_session_and_redact_secrets(setup):
    client = setup[2]
    event = {"component": "android-adapter", "event": "native_login_blocked", "details": {"method": "LoginDmmAndroidAsync", "nested": {"api_token": "sensitive"}}}
    assert client.post("/local/diagnostics", json=event).status_code == 401
    _, headers = session(client)
    assert client.post("/local/diagnostics", json=event, headers=headers).status_code == 200
    diagnostics = client.get("/diagnostics")
    assert "sensitive" not in diagnostics.text
    captured = diagnostics.json()["client_events"][0]
    assert captured["event"] == "native_login_blocked"
    assert captured["details"]["nested"]["api_token"] == REDACTED


@pytest.mark.parametrize("alias", ["/api/home", "/api/health-check", "/healthz", "/local/login", "/a/../login", "login", "//login", "/api/present/list/1/0", "/api/gacha/wishlist/1", "/api/exchange/execute"])
def test_login_alias_cannot_shadow_existing_routes(tmp_path, alias):
    settings = Settings.from_directory(tmp_path)
    with pytest.raises(ValueError):
        create_app(Settings(settings.data_dir, settings.fixtures_dir, settings.resources_dir, alias))
