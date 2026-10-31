import json
from pathlib import Path

import msgpack
import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import Settings, create_app


@pytest.fixture
def setup(tmp_path):
    settings = Settings.from_directory(tmp_path)
    app = create_app(settings)
    with TestClient(app) as client:
        login = client.post("/local/login", json={"profile": "native-test"}).json()
        headers = {"x-olg-session": login["contents"]["token"], "api-token": login["api_token"]}
        yield settings, app.state.store, client, login, headers


def test_native_login_reuses_local_identity_and_session(setup):
    _, store, client, login, headers = setup
    body = {"token": headers["x-olg-session"], "token_status": 0, "token_object": "", "token_key_id": None,
            "terms_agreements": [{"version": 1, "terms_type": 0}]}
    response = client.post("/login", json=body, headers=headers)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "success"
    assert result["contents"] == login["contents"]
    assert result["api_token"] == login["api_token"]
    assert response.headers["x-olg-session"] == headers["x-olg-session"]
    assert response.headers["api-token"] == headers["api-token"]
    assert isinstance(result["server_time"], str)
    assert isinstance(result["timestamp"], int)
    assert result["errors"]["code"] == 0
    assert all(isinstance(value, list) for value in result["versions"].values())
    assert client.post("/login", json=body, headers=headers).json()["contents"] == result["contents"]
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM profiles").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_native_login_fields_match_apk_response_dto(setup):
    _, _, client, _, headers = setup
    contents = client.post("/login", json={}, headers=headers).json()["contents"]
    document = json.loads((Path(__file__).parents[1] / "artifacts/protocol.json").read_text(encoding="utf-8"))
    typedef = next(item for item in document["types"] if item["full_name"] == "Il2CppProject.Api.RootLoginResponseEntity")
    assert set(contents) == {field["name"] for field in typedef["fields"]}
    assert type(contents["user_id"]) is int
    assert type(contents["token_status"]) is int
    assert isinstance(contents["token"], str)
    assert isinstance(contents["uuid"], str)


@pytest.mark.parametrize("token", [None, ""])
def test_native_login_accepts_empty_body_token(setup, token):
    client, headers = setup[2], setup[4]
    assert client.post("/login", json={"token": token}, headers=headers).status_code == 200


@pytest.mark.parametrize("missing", ["both", "x-olg-session", "api-token"])
def test_native_login_requires_both_local_headers(setup, missing):
    client, headers = setup[2], dict(setup[4])
    if missing == "both":
        headers.clear()
    else:
        headers.pop(missing)
    response = client.post("/login", json={}, headers=headers)
    assert response.status_code == 401
    assert response.json()["status"] == "error"


@pytest.mark.parametrize("field", ["x-olg-session", "api-token"])
def test_native_login_rejects_unknown_or_mismatched_header(setup, field):
    client, headers = setup[2], dict(setup[4])
    headers[field] = "not-a-local-credential"
    assert client.post("/login", json={}, headers=headers).status_code == 401


def test_native_login_rejects_cross_session_api_token(setup):
    client, headers = setup[2], dict(setup[4])
    another = client.post("/local/login", json={"profile": "another"}).json()
    headers["api-token"] = another["api_token"]
    assert client.post("/login", json={}, headers=headers).status_code == 401


def test_native_login_rejects_expired_session(setup):
    _, store, client, _, headers = setup
    with store.connection() as db:
        db.execute("UPDATE sessions SET expires_at=0")
    assert client.post("/login", json={}, headers=headers).status_code == 401


@pytest.mark.parametrize("body", [
    {"token": "external-token"}, {"token": 1}, {"token": {}},
    {"token_object": "external-object"}, {"token_key_id": "external-key"}, {"password": "external-password"},
    {"token_object": False}, {"token_key_id": []},
    {"token_status": True}, {"token_status": "0"}, {"token_status": 2 ** 31},
    {"terms_agreements": {}}, {"terms_agreements": [None]},
    {"terms_agreements": [{"version": 1}]}, {"terms_agreements": [{"version": True, "terms_type": 0}]},
])
def test_native_login_rejects_external_credentials_and_invalid_shapes(setup, body):
    _, store, client, _, headers = setup
    response = client.post("/login", json=body, headers=headers)
    assert response.status_code == 400
    assert response.json()["status"] == "error"
    diagnostics = client.get("/diagnostics").text
    for value in ("external-token", "external-object", "external-key", "external-password", *headers.values()):
        assert value not in diagnostics
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 1


def test_native_login_messagepack_uses_same_contract(setup):
    _, _, client, login, headers = setup
    headers = {**headers, "content-type": "application/x-msgpack", "accept": "application/x-msgpack"}
    response = client.post("/login", content=msgpack.packb({"token": headers["x-olg-session"]}), headers=headers)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/x-msgpack"
    assert msgpack.unpackb(response.content, raw=False)["contents"] == login["contents"]


@pytest.mark.parametrize("alias", ["/verified-login", "/login"])
def test_native_login_alias_has_identical_authentication_contract(setup, alias):
    settings, _, _, login, headers = setup
    configured = Settings(settings.data_dir, settings.fixtures_dir, settings.resources_dir, alias)
    app = create_app(configured)
    with TestClient(app) as client:
        assert client.post(alias, json={}).status_code == 401
        assert client.post(alias, json={}, headers=headers).json()["contents"] == login["contents"]
        assert client.post("/login", json={}, headers=headers).json()["contents"] == login["contents"]
    assert sum(route.path == "/login" for route in app.routes) == 1


def test_native_login_does_not_mark_missing_bootstrap_ready(setup):
    _, _, client, _, headers = setup
    assert client.post("/login", json={}, headers=headers).status_code == 200
    for route in ("/api/user", "/api/home", "/api/mypage/login"):
        response = client.post(route, json={}, headers=headers)
        assert response.status_code == 503
        assert response.json()["errors"]["code"] == 5031
    diagnostics = client.get("/diagnostics").json()
    assert diagnostics["device_verified"] is False
    assert diagnostics["bootstrap_ready"] is False
    assert set(diagnostics["fixtures"].values()) == {"missing"}
