import io
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest

from console import service_bootstrap as bootstrap
from console import service_manager as module
from console.client import ConsoleError


class Process:
    def __init__(self, pid=42):
        self.pid = pid
        self.returncode = None

    def poll(self):
        return self.returncode


@pytest.fixture
def probe(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    token = data / "admin-token.txt"
    token.write_text("a" * 40)
    manager = module.ServiceManager("http://127.0.0.1:18083", data, project_root=tmp_path)
    manager.process, manager.instance = Process(), "worker-one"
    clock = [100.0]
    counts = {"health": 0, "compatibility": 0, "tickets": 0}
    state = {"health": {"status": "ok", "version": "0.1.0", "instance": "worker-one"}, "compatibility_error": None}

    def health(_):
        counts["health"] += 1
        value = state["health"]
        if isinstance(value, Exception):
            raise value
        return value

    def ticket(path, body):
        assert path == "/admin/ui-ticket" and body == {}
        counts["tickets"] += 1
        return {"ticket": "single-use-test-ticket-123456"}

    client = SimpleNamespace(post=ticket)

    def compatibility(*_):
        counts["compatibility"] += 1
        if state["compatibility_error"]:
            raise state["compatibility_error"]
        return client

    monkeypatch.setattr(module, "health", health)
    monkeypatch.setattr(module, "compatible_client", compatibility)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    return SimpleNamespace(manager=manager, clock=clock, counts=counts, state=state, token=token)


@pytest.mark.parametrize("endpoint", ["health", "compatibility"])
def test_temporary_failure_recovers_without_port_conflict_or_losing_instance(probe, endpoint):
    before = probe.manager.status()
    assert before["state"] == "running"
    probe.clock[0] += 31
    if endpoint == "health":
        probe.state["health"] = module.ServiceUnavailable("health timeout", 504)
    else:
        probe.state["compatibility_error"] = module.ServiceUnavailable("admin timeout", 503)
    waiting = probe.manager.status()
    assert waiting["state"] == "recovering" and not waiting["ready"]
    assert waiting["preserve_console"] and not waiting["can_start"]
    assert waiting["can_stop"] and waiting["can_restart"]
    assert waiting["pid"] == before["pid"] and waiting["instance"] == before["instance"]
    probe.clock[0] += 1
    probe.state.update(health={"status": "ok", "version": "0.1.0", "instance": "worker-one"}, compatibility_error=None)
    restored = probe.manager.status()
    assert restored["state"] == "running" and restored["ready"]
    assert restored["instance"] == before["instance"]


def test_repeated_failures_report_unresponsive_and_do_not_reopen_service(probe):
    probe.manager.status()
    probe.state["health"] = module.ServiceUnavailable("still busy", 503)
    for index in range(3):
        probe.clock[0] += 1
        status = probe.manager.status()
        assert status["state"] == ("recovering" if index < 2 else "unresponsive")
        assert status["preserve_console"] and not status["can_start"]
    assert probe.counts["compatibility"] == 1


def test_validated_instance_does_not_repeat_admin_checks_on_every_poll(probe):
    for _ in range(5):
        assert probe.manager.status()["ready"]
        probe.clock[0] += 1
    assert probe.counts == {"health": 5, "compatibility": 1, "tickets": 0}
    probe.clock[0] = 131
    assert probe.manager.status()["ready"]
    assert probe.counts["compatibility"] == 2


def test_token_change_invalidates_compatibility_validation(probe):
    assert probe.manager.status()["ready"]
    previous = probe.token.stat()
    probe.token.write_text("b" * 40)
    os.utime(probe.token, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000_000))
    probe.clock[0] += 1
    assert probe.manager.status()["ready"]
    assert probe.counts["compatibility"] == 2


def test_new_instance_requires_admin_validation_and_is_not_claimed_as_owned(probe):
    first = probe.manager.status()
    probe.clock[0] += 1
    probe.state["health"] = {"status": "ok", "version": "0.1.0", "instance": "another-worker"}
    other = probe.manager.status()
    assert probe.counts["compatibility"] == 2
    assert other["instance"] != first["instance"] and not other["preserve_console"]
    assert other["state"] == "external" and not other["managed"] and not other["can_stop"]


def test_confirmed_wrong_service_is_a_conflict_and_drops_previous_console(probe):
    probe.manager.status()
    probe.clock[0] += 1
    probe.state["health"] = module.ServiceMismatch("different application", 409)
    wrong = probe.manager.status()
    assert wrong["state"] == "conflict" and not wrong["preserve_console"]
    assert not wrong["ready"] and not wrong["can_start"]


def test_credentials_failure_is_configuration_error_not_port_conflict(probe):
    probe.manager.status()
    probe.clock[0] += 31
    probe.state["compatibility_error"] = ConsoleError("wrong token", 401)
    wrong = probe.manager.status()
    assert wrong["state"] == "error" and not wrong["preserve_console"]
    assert wrong["message"] == "wrong token" and not wrong["ready"]


def test_confirmed_exit_clears_preserved_console_and_allows_restart(probe):
    probe.manager.status()
    probe.manager.process.returncode = 0
    probe.state["health"] = None
    stopped = probe.manager.status()
    assert stopped["state"] == "stopped" and not stopped["preserve_console"]
    assert stopped["can_start"] and not stopped["ready"]


def test_unknown_service_timeout_does_not_claim_port_conflict_or_readiness(probe):
    probe.state["health"] = module.ServiceUnavailable("not yet known", 504)
    status = probe.manager.status()
    assert status["state"] == "error" and not status["ready"]
    assert not status["preserve_console"] and not status["can_start"]


def test_external_service_preserves_console_only_until_confirmed_stop(probe):
    probe.manager.process = None
    assert probe.manager.status()["state"] == "external"
    probe.clock[0] += 1
    probe.state["health"] = module.ServiceUnavailable("temporary timeout", 504)
    waiting = probe.manager.status()
    assert waiting["preserve_console"] and waiting["state"] == "recovering"
    assert not waiting["managed"] and not waiting["can_stop"]
    probe.clock[0] += 1
    probe.state["health"] = None
    assert probe.manager.status()["state"] == "stopped"
    assert not probe.manager.status()["preserve_console"]


def test_new_owned_instance_does_not_preserve_previous_console_on_timeout(probe):
    probe.manager.status()
    probe.manager.process = Process(42)
    probe.manager.instance = "new-owned-worker"
    probe.state["health"] = module.ServiceUnavailable("new worker starting", 503)
    current = probe.manager.status()
    assert not current["preserve_console"]
    assert current["instance"] == "new-owned-worker"


def test_simultaneous_windows_share_one_status_probe(probe, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    def health(_):
        probe.counts["health"] += 1
        entered.set()
        assert release.wait(5)
        return probe.state["health"]

    monkeypatch.setattr(module, "health", health)
    with ThreadPoolExecutor(max_workers=6) as pool:
        first = pool.submit(probe.manager.status)
        assert entered.wait(5)
        waiting = [pool.submit(probe.manager.status) for _ in range(5)]
        release.set()
        values = [first.result(), *[pending.result() for pending in waiting]]
    assert all(value["state"] == "running" for value in values)
    assert probe.counts["health"] == probe.counts["compatibility"] == 1


def test_ticket_uses_validated_client_without_six_extra_checks(probe):
    probe.manager.status()
    probe.manager.console_ticket()
    probe.manager.console_ticket()
    assert probe.counts == {"health": 1, "compatibility": 1, "tickets": 2}
    probe.clock[0] += 1
    probe.manager.console_ticket()
    assert probe.counts == {"health": 2, "compatibility": 1, "tickets": 3}


@pytest.mark.parametrize("code", [408, 429, 500, 503])
def test_temporary_http_errors_are_unavailable_not_mismatches(monkeypatch, code):
    def open_request(*_args, **_kwargs):
        raise HTTPError("http://127.0.0.1:18083/healthz", code, "temporary", {}, None)
    monkeypatch.setattr(bootstrap, "build_opener", lambda *_: SimpleNamespace(open=open_request))
    with pytest.raises(bootstrap.ServiceUnavailable):
        bootstrap.health("http://127.0.0.1:18083")


@pytest.mark.parametrize("payload", [b"not JSON", json.dumps({"service": "different", "status": "ok"}).encode()])
def test_wrong_health_protocol_is_an_explicit_mismatch(monkeypatch, payload):
    response = io.BytesIO(payload)
    response.headers = {}
    monkeypatch.setattr(bootstrap, "build_opener", lambda *_: SimpleNamespace(open=lambda *_a, **_kw: response))
    with pytest.raises(bootstrap.ServiceMismatch):
        bootstrap.health("http://127.0.0.1:18083")
