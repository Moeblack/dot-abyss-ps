import json
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from console import host_runtime as module
from console.client import ConsoleError


@pytest.fixture
def context(tmp_path):
    root = tmp_path / "project"
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"test only")
    data = root / "data"
    data.mkdir()
    origin = "http://127.0.0.1:18083"
    state, lock, start_lock = module.runtime_paths(root, origin, data)
    state.parent.mkdir(parents=True)
    record = {"origin": "http://127.0.0.1:19001", "game_origin": origin,
              "data_dir": str(data), "token": "native-host-secret-12345678901234567890"}
    return SimpleNamespace(root=root, data=data, origin=origin, state=state, record=record)


def write_state(context):
    context.state.write_text(json.dumps(context.record), encoding="utf-8")


def test_reopening_window_reuses_existing_host_and_exchanges_ticket(context, monkeypatch):
    write_state(context)
    calls = []

    def request(origin, path, token=None, body=None):
        calls.append((origin, path, token, body))
        if path == "/host/ping":
            return {"service": "dotabyss-launcher", "version": 1}
        return {"ticket": "one-time-browser-ticket"}

    monkeypatch.setattr(module, "native_request", request)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("must reuse host"))
    result = module.ensure_host(context.origin, context.data, project_root=context.root)
    assert result["origin"] == context.record["origin"]
    assert result["url"] == "http://127.0.0.1:19001/#ticket=one-time-browser-ticket"
    assert context.record["token"] not in result["url"]
    assert context.origin not in result["url"]
    assert calls[0][2] is None and calls[1][2] == context.record["token"]


def test_missing_host_starts_fixed_module_without_starting_game_service(context, monkeypatch):
    seen = []

    def request(origin, path, token=None, body=None):
        if path == "/host/ping":
            return {"service": "dotabyss-launcher", "version": 1}
        return {"ticket": "new-host-window-ticket"}

    def spawn(command, **options):
        seen.append((command, options))
        write_state(context)
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(module, "native_request", request)
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    result = module.ensure_host(context.origin, context.data, project_root=context.root)
    command, options = seen[0]
    assert command[1:4] == ["-u", "-m", "console.host_runtime"]
    assert command[command.index("--origin") + 1] == context.origin
    assert context.record["token"] not in " ".join(command)
    assert "console.service_worker" not in command
    assert result["origin"] != context.origin


def test_closed_old_host_record_is_ignored(context, monkeypatch):
    write_state(context)
    monkeypatch.setattr(module, "native_request", lambda *_a, **_kw: (_ for _ in ()).throw(URLError(ConnectionRefusedError())))
    assert module.existing_host(context.state, context.origin, context.data) is None


def test_foreign_service_on_old_host_port_does_not_receive_secret(context, monkeypatch):
    write_state(context)
    calls = []
    def request(origin, path, token=None, body=None):
        calls.append((path, token))
        return {"service": "different-program"}
    monkeypatch.setattr(module, "native_request", request)
    assert module.existing_host(context.state, context.origin, context.data) is None
    assert calls == [("/host/ping", None)]


def test_wrong_data_directory_record_is_rejected_before_network(context, monkeypatch):
    context.record["data_dir"] = str(context.data.parent / "another-save")
    write_state(context)
    monkeypatch.setattr(module, "native_request", lambda *_a, **_kw: pytest.fail("no network for wrong config"))
    with pytest.raises(ConsoleError):
        module.existing_host(context.state, context.origin, context.data)


def test_runtime_locks_distinguish_service_ports_and_save_directories(context):
    original = module.runtime_paths(context.root, context.origin, context.data)
    changed_port = module.runtime_paths(context.root, "http://127.0.0.1:18084", context.data)
    changed_save = module.runtime_paths(context.root, context.origin, context.data.parent / "another")
    assert not set(original) & set(changed_port)
    assert not set(original) & set(changed_save)


def test_native_request_places_credentials_in_headers_and_bypasses_proxy(monkeypatch):
    seen = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            return False
        def read(self, limit):
            assert limit == 65537
            return b'{"ticket":"short-lived-ticket"}'
    class Opener:
        def open(self, request, timeout):
            seen.append(request)
            return Response()
    def build(*handlers):
        assert handlers[0].proxies == {}
        return Opener()
    monkeypatch.setattr(module, "build_opener", build)
    result = module.native_request("http://127.0.0.1:19001", "/host/ticket", "native-secret", {})
    assert result["ticket"] == "short-lived-ticket"
    assert seen[0].full_url == "http://127.0.0.1:19001/host/ticket"
    assert seen[0].get_header("X-host-token") == "native-secret"
    assert seen[0].data == b"{}"
