import json
from pathlib import Path

import msgpack
import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import MAX_BODY_BYTES, Settings, create_app


@pytest.fixture
def setup(tmp_path):
    settings = Settings.from_directory(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        yield settings, app.state.store, client


def session(client):
    login = client.post("/local/login", json={"profile": "maintenance-test"}).json()
    return {"x-olg-session": login["contents"]["token"], "api-token": login["api_token"]}


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_maintenance_is_public_and_matches_native_schema(setup, method):
    client = setup[2]
    response = client.request(method, "/maintenance?app_version=1.14.4")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "success"
    assert payload["api_token"] == ""
    assert isinstance(payload["server_time"], str)
    assert isinstance(payload["timestamp"], int)
    assert payload["errors"]["code"] == 0
    assert payload["contents"] == {"maintenance_type": "", "message": {"title": "", "message": ""}, "is_debug_user": False, "configs": []}
    document = json.loads((Path(__file__).parents[1] / "artifacts/protocol.json").read_text(encoding="utf-8"))
    types = {item["name"]: item for item in document["types"] if item["namespace"] == "Il2CppProject.Api"}
    assert set(payload["contents"]) == {field["name"] for field in types["RootMaintenanceResponseEntity"]["fields"]}
    assert set(payload["contents"]["message"]) == {field["name"] for field in types["MaintenanceMessageEntity"]["fields"]}


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_maintenance_keeps_verified_session_token_and_runtime_versions(setup, method):
    client = setup[2]
    headers = session(client)
    runtime = {"application_version": "1.14.4", "app_version_code": "1", "bundle_version": "observed-assets", "server_version": "observed-server", "resource_version": "observed-master"}
    assert client.post("/local/runtime", json=runtime).status_code == 200
    response = client.request(method, "/maintenance?app_version=untrusted-query", headers=headers)
    assert response.status_code == 200
    payload = response.json()
    assert payload["api_token"] == headers["api-token"]
    assert response.headers["api-token"] == headers["api-token"]
    assert response.headers["x-olg-session"] == headers["x-olg-session"]
    assert payload["versions"]["ClientVersionAndroidDmmR18"] == ["1.14.4"]
    assert payload["versions"]["AssetVersionAndroidDmmR18"] == ["observed-assets"]
    assert payload["versions"]["resource"] == ["observed-master"]


@pytest.mark.parametrize("credentials", ["missing-api", "missing-session", "unknown-session", "wrong-api", "expired"])
def test_maintenance_never_echoes_unverified_tokens(setup, credentials):
    _, store, client = setup
    headers = session(client)
    if credentials == "missing-api":
        headers.pop("api-token")
    elif credentials == "missing-session":
        headers.pop("x-olg-session")
    elif credentials == "unknown-session":
        headers["x-olg-session"] = "unknown-session-secret"
    elif credentials == "wrong-api":
        headers["api-token"] = "unknown-api-secret"
    else:
        with store.connection() as db:
            db.execute("UPDATE sessions SET expires_at=0")
    response = client.get("/maintenance", headers=headers)
    assert response.status_code == 200
    assert response.json()["api_token"] == ""
    assert "api-token" not in response.headers
    assert "x-olg-session" not in response.headers


def test_maintenance_audit_omits_query_and_body_values(setup):
    client = setup[2]
    headers = session(client)
    url = "/maintenance?app_version=private-app-value&token=private-query-token&hash=private-query-hash"
    assert client.post(url, json={"token": "private-body-token", "nested": {"value": "private-nested-value"}}, headers=headers).status_code == 200
    diagnostics = client.get("/diagnostics")
    for value in ("private-app-value", "private-query-token", "private-query-hash", "private-body-token", "private-nested-value", *headers.values()):
        assert value not in diagnostics.text
    observation = diagnostics.json()["observations"][0]
    assert observation["path"] == "/maintenance"
    assert observation["request_keys"] == ["nested", "token"]


@pytest.mark.parametrize("body,headers,status", [
    (b"[", {"content-type": "application/json"}, 400),
    (b"[]", {"content-type": "application/json"}, 400),
    (b" " * (MAX_BODY_BYTES + 1), {}, 413),
    (b"compressed", {"content-encoding": "gzip"}, 415),
], ids=["invalid-json", "array-body", "oversized", "compressed"])
def test_maintenance_post_checks_transport(setup, body, headers, status):
    response = setup[2].post("/maintenance", content=body, headers=headers)
    assert response.status_code == status
    assert response.json()["status"] == "error"


def test_maintenance_messagepack_transport(setup):
    headers = {"content-type": "application/x-msgpack", "accept": "application/x-msgpack"}
    response = setup[2].post("/maintenance", content=msgpack.packb({}), headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-msgpack"
    assert msgpack.unpackb(response.content, raw=False)["contents"]["maintenance_type"] == ""


def test_maintenance_does_not_enable_unknown_or_missing_game_data(setup):
    client = setup[2]
    assert client.get("/maintenance").status_code == 200
    assert client.get("/maintenance-extra").status_code == 401
    headers = session(client)
    assert client.get("/maintenance-extra", headers=headers).status_code == 404
    assert client.post("/api/home", json={}, headers=headers).status_code == 503
    assert client.get("/resources/android/missing.bundle", headers=headers).status_code == 404
    diagnostics = client.get("/diagnostics").json()
    assert diagnostics["bootstrap_ready"] is False
    assert diagnostics["fixtures"]["/api/home"] == "missing"


def test_login_alias_cannot_replace_maintenance(setup):
    settings = setup[0]
    with pytest.raises(ValueError, match="non-reserved"):
        create_app(Settings(settings.data_dir, settings.fixtures_dir, settings.resources_dir, "/maintenance"))
