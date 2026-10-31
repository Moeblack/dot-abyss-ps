import asyncio

import pytest
from fastapi.testclient import TestClient

from console import host_api
from console.client import ConsoleError

ORIGIN = "http://127.0.0.1:19000"
SECRET = "local-host-test-" + "x" * 40
NATIVE = {"X-Host-Token": SECRET}
BROWSER = {"Origin": ORIGIN, "X-Console-Request": "1", "Sec-Fetch-Site": "same-origin"}


class Manager:
    origin = "http://127.0.0.1:18083"

    def __init__(self):
        self.calls = []
        self.state = "stopped"

    def status(self):
        self.calls.append("status")
        with pytest.raises(RuntimeError):
            asyncio.get_running_loop()
        return {"state": self.state, "ready": self.state == "running", "managed": True,
                "can_start": self.state == "stopped", "can_stop": self.state == "running",
                "can_restart": self.state == "running", "origin": self.origin, "data_dir": "E:/fixture-data",
                "pid": 123 if self.state == "running" else None, "log_path": None, "message": "", "instance": None}

    def start(self):
        self.calls.append("start")
        self.state = "running"
        return self.status()

    def stop(self):
        self.calls.append("stop")
        self.state = "stopped"
        return self.status()

    def restart(self):
        self.calls.append("restart")
        self.state = "running"
        return self.status()

    def logs(self):
        self.calls.append("logs")
        return {"text": "fixture log", "path": None}

    def console_ticket(self):
        self.calls.append("console_ticket")
        return {"url": self.origin + "/console/#ticket=one-time-game-ticket"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    root = tmp_path / "host"
    demo = tmp_path / "demo"
    root.mkdir()
    demo.mkdir()
    (root / "index.html").write_text("<html>offline launcher</html>", encoding="utf-8")
    (root / "app.js").write_text("'use strict';", encoding="utf-8")
    (demo / "index.html").write_text("<html>demo console</html>", encoding="utf-8")
    monkeypatch.setattr(host_api, "HOST_WEB_DIR", root)
    monkeypatch.setattr(host_api, "DEMO_WEB_DIR", demo)
    manager = Manager()
    app = host_api.create_host_app(manager, SECRET)
    app.state.origin = ORIGIN
    with TestClient(app, base_url=ORIGIN, client=("127.0.0.1", 10001)) as result:
        result.manager = manager
        yield result


def authenticate(client):
    value = client.post("/host/ticket", headers=NATIVE, json={})
    assert value.status_code == 200
    response = client.post("/host/auth", headers=BROWSER, json={"ticket": value.json()["ticket"]})
    assert response.status_code == 200
    return value.json()["ticket"], response


def test_offline_shell_ping_and_demo_do_not_require_a_game_service(client):
    assert client.get("/").text == "<html>offline launcher</html>"
    assert client.get("/app.js").status_code == 200
    assert client.get("/demo-console/").text == "<html>demo console</html>"
    assert client.get("/host/ping").json() == {"service": "dotabyss-launcher", "version": 1}
    assert client.manager.calls == []
    response = client.get("/host/status", headers=NATIVE)
    assert response.status_code == 200 and response.json()["state"] == "stopped"


def test_security_headers_allow_only_expected_frames_without_inline_code(client):
    response = client.get("/")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-src 'self' http://127.0.0.1:18083" in response.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "unsafe-inline" not in response.headers["content-security-policy"]
    demo = client.get("/demo-console/")
    assert demo.headers["x-frame-options"] == "SAMEORIGIN"
    assert "frame-ancestors 'self'" in demo.headers["content-security-policy"]


@pytest.mark.parametrize("host", ["localhost:19000", "127.0.0.1:19001", "evil.example:19000", "127.0.0.1"])
def test_host_header_is_pinned_even_for_ping(client, host):
    assert client.get("/host/ping", headers={"Host": host}).status_code == 403


def test_non_loopback_connections_are_rejected(client):
    with TestClient(client.app, base_url=ORIGIN, client=("192.0.2.10", 20000)) as remote:
        assert remote.get("/host/ping").status_code == 403


def test_native_credential_never_authorizes_browser_context(client):
    assert client.post("/host/ticket", json={}).status_code == 401
    assert client.get("/host/status", headers={"X-Host-Token": "wrong"}).status_code == 401
    for extra in ({"Origin": ORIGIN}, {"Sec-Fetch-Site": "same-origin"}, {"Sec-Fetch-Mode": "cors"}):
        assert client.post("/host/ticket", headers={**NATIVE, **extra}, json={}).status_code == 403
    assert client.manager.calls == []


def test_one_time_handoff_creates_a_scoped_http_only_session(client):
    ticket, response = authenticate(client)
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/host" in cookie
    assert SECRET not in cookie
    assert client.post("/host/auth", headers=BROWSER, json={"ticket": ticket}).status_code == 401
    assert client.get("/host/status", headers=BROWSER).status_code == 200
    assert client.get("/host/status", headers={"X-Console-Request": "1"}).status_code == 200
    assert client.get("/host/status").status_code == 403
    assert client.get("/host/status", headers={**BROWSER, "Origin": "http://127.0.0.1:19001"}).status_code == 403
    assert client.get("/host/status", headers={**BROWSER, "Sec-Fetch-Site": "same-site"}).status_code == 403


def test_auth_requires_exact_origin_and_expired_tickets_are_rejected(client, monkeypatch):
    ticket = client.post("/host/ticket", headers=NATIVE, json={}).json()["ticket"]
    assert client.post("/host/auth", headers={"X-Console-Request": "1"}, json={"ticket": ticket}).status_code == 403
    monkeypatch.setattr(host_api, "TICKET_TTL", -1)
    expired = client.post("/host/ticket", headers=NATIVE, json={}).json()["ticket"]
    assert client.post("/host/auth", headers=BROWSER, json={"ticket": expired}).status_code == 401
    assert client.post("/host/auth", headers=BROWSER, json={"ticket": ticket}).status_code == 200


def test_ticket_and_session_counts_are_bounded(client, monkeypatch):
    monkeypatch.setattr(host_api, "AUTH_LIMIT", 2)
    first = client.post("/host/ticket", headers=NATIVE, json={}).json()["ticket"]
    second = client.post("/host/ticket", headers=NATIVE, json={}).json()["ticket"]
    assert client.post("/host/ticket", headers=NATIVE, json={}).status_code == 429
    assert client.post("/host/auth", headers=BROWSER, json={"ticket": first}).status_code == 200
    assert client.post("/host/auth", headers=BROWSER, json={"ticket": second}).status_code == 200
    third = client.post("/host/ticket", headers=NATIVE, json={}).json()["ticket"]
    assert client.post("/host/auth", headers=BROWSER, json={"ticket": third}).status_code == 429


def test_expired_browser_session_is_not_authorized(client, monkeypatch):
    monkeypatch.setattr(host_api, "SESSION_TTL", -1)
    authenticate(client)
    assert client.get("/host/status", headers=BROWSER).status_code == 401


def test_manager_actions_logs_and_console_ticket_use_authorized_calls(client):
    authenticate(client)
    for action, state in (("start", "running"), ("stop", "stopped"), ("restart", "running")):
        response = client.post("/host/service/" + action, headers=BROWSER, json={})
        assert response.status_code == 200 and response.json()["state"] == state
    assert client.get("/host/logs", headers=BROWSER).json() == {"text": "fixture log", "path": None}
    assert client.post("/host/console-ticket", headers=BROWSER, json={}).json()["url"].startswith(client.manager.origin + "/console/#ticket=")
    assert [value for value in client.manager.calls if value != "status"] == ["start", "stop", "restart", "logs", "console_ticket"]


@pytest.mark.parametrize("payload", [b'[]', b'null', b'{"command":"anything"}', b'{"x":1,"x":2}', b'{"x":NaN}', b'{invalid'])
def test_mutations_reject_nonempty_or_invalid_json_without_manager_calls(client, payload):
    response = client.post("/host/service/start", headers=NATIVE, content=payload)
    assert response.status_code == 400
    assert client.manager.calls == []


def test_body_limits_and_compression_are_checked_before_actions(client):
    assert client.post("/host/service/start", headers=NATIVE, content=b" " * 4097).status_code == 413
    assert client.post("/host/service/start", headers={**NATIVE, "Content-Encoding": "gzip"}, content=b"{}").status_code == 415
    assert client.manager.calls == []


def test_manager_errors_preserve_status_and_redact_native_secret(client, monkeypatch):
    def fail():
        raise ConsoleError("Cannot stop " + SECRET, 409)
    monkeypatch.setattr(client.manager, "stop", fail)
    response = client.post("/host/service/stop", headers=NATIVE, json={})
    assert response.status_code == 409
    assert SECRET not in response.text and "Cannot stop" in response.json()["error"]["message"]


def test_unexpected_manager_failure_keeps_trace_in_logs_only(client, monkeypatch, caplog):
    def fail():
        raise RuntimeError("private implementation fault")
    monkeypatch.setattr(client.manager, "logs", fail)
    response = client.get("/host/logs", headers=NATIVE)
    assert response.status_code == 500
    assert "private implementation fault" not in response.text
    assert any(record.exc_info is not None and record.message == "Launcher operation failed" for record in caplog.records)


def test_controller_does_not_proxy_game_or_admin_credentials(client):
    assert client.get("/admin/status", headers=NATIVE).status_code == 404
    assert client.post("/api/user", headers=NATIVE, json={}).status_code in (404, 405)
