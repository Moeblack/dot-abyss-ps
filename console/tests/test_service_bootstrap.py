import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import pytest

from console import service_bootstrap as module
from console.client import ConsoleError


@pytest.mark.parametrize("wrapped", [False, True])
def test_timeout_with_exclusively_free_port_is_offline(monkeypatch, wrapped):
    origin = "http://127.0.0.1:18083"
    checked = []

    def open_request(*args, **kwargs):
        error = TimeoutError("mock timeout")
        raise URLError(error) if wrapped else error

    def available(value):
        checked.append(value)
        return True

    monkeypatch.setattr(module, "build_opener", lambda *_: SimpleNamespace(open=open_request))
    monkeypatch.setattr(module, "port_is_available", available)
    assert module.health(origin) is None
    assert checked == [origin]


@pytest.mark.parametrize("wrapped", [False, True])
def test_timeout_with_occupied_port_rejects_second_service(tmp_path, monkeypatch, wrapped):
    checked = []

    def open_request(*args, **kwargs):
        error = TimeoutError("mock timeout")
        raise URLError(error) if wrapped else error

    def available(value):
        checked.append(value)
        return False

    monkeypatch.setattr(module, "build_opener", lambda *_: SimpleNamespace(open=open_request))
    monkeypatch.setattr(module, "port_is_available", available)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_a, **_kw: pytest.fail("occupied port must not spawn"))
    with pytest.raises(ConsoleError):
        module.ensure_service("http://127.0.0.1:18083", tmp_path, project_root=tmp_path)
    assert checked == ["http://127.0.0.1:18083"]


def environment(tmp_path):
    root = tmp_path / "project"
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"test only")
    schema = root / "artifacts" / "master-schema-pc-1.15.0.json"
    schema.parent.mkdir(parents=True)
    schema.write_text("{}")
    data = root / "data"
    data.mkdir()
    (data / "local.sqlite3").write_bytes(b"test only")
    return root, data


def test_existing_service_is_reused_without_starting_process(tmp_path, monkeypatch):
    root, data = environment(tmp_path)
    client = object()
    monkeypatch.setattr(module, "health", lambda _: {"status": "ok"})
    monkeypatch.setattr(module, "compatible_client", lambda *args: client)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("do not start another service"))
    result = module.ensure_service("http://localhost:18083", data, project_root=root)
    assert not result["started"] and result["client"] is client
    assert result["origin"] == "http://127.0.0.1:18083"


def test_missing_service_starts_known_hidden_module_and_waits(tmp_path, monkeypatch):
    root, data = environment(tmp_path)
    states = iter([None, None, None, {"status": "ok"}])
    commands = []
    client = object()
    monkeypatch.setattr(module, "health", lambda _: next(states))
    monkeypatch.setattr(module, "compatible_client", lambda *args: client)
    monkeypatch.setattr(module.time, "sleep", lambda _: None)
    def spawn(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(pid=22, poll=lambda: None)
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    result = module.ensure_service("http://127.0.0.1:18083", data, project_root=root)
    assert result["started"] and result["client"] is client
    command, options = commands[0]
    assert command[:3] == [str(root / ".venv" / "Scripts" / "python.exe"), "-m", "dotabyss_ps.app"]
    assert command[command.index("--data-dir") + 1] == str(data)
    assert options["stdin"] == subprocess.DEVNULL
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        assert options["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert options["env"]["PYTHONPATH"] == str(root / "server")


def test_old_service_requests_restart_and_does_not_spawn(tmp_path, monkeypatch):
    root, data = environment(tmp_path)
    monkeypatch.setattr(module, "health", lambda _: {"status": "ok"})
    monkeypatch.setattr(module, "read_token", lambda _: "secret")
    class Client:
        def __init__(self, *args, **kwargs):
            pass
        def get(self, path):
            assert path == "/admin/status"
            return {"service": "dotabyss-console", "version": 1}
    monkeypatch.setattr(module, "AdminClient", Client)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("do not replace user service"))
    with pytest.raises(ConsoleError, match="重启一次"):
        module.ensure_service("http://127.0.0.1:18083", data, project_root=root)


def test_failed_child_is_reported_without_killing_other_processes(tmp_path, monkeypatch):
    root, data = environment(tmp_path)
    monkeypatch.setattr(module, "health", lambda _: None)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: SimpleNamespace(pid=33, poll=lambda: 2))
    with pytest.raises(ConsoleError, match="启动失败"):
        module.ensure_service("http://127.0.0.1:18083", data, project_root=root)


def test_wrong_port_service_is_never_replaced(tmp_path, monkeypatch):
    root, data = environment(tmp_path)
    monkeypatch.setattr(module, "health", lambda _: (_ for _ in ()).throw(ConsoleError("wrong service")))
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("never replace other service"))
    with pytest.raises(ConsoleError, match="wrong service"):
        module.ensure_service("http://127.0.0.1:18083", data, project_root=root)


def test_wrong_credentials_are_not_reported_as_old_service(tmp_path, monkeypatch):
    root, data = environment(tmp_path)
    monkeypatch.setattr(module, "health", lambda _: {"status": "ok"})
    monkeypatch.setattr(module, "read_token", lambda _: "wrong-token")
    class Client:
        def __init__(self, *args, **kwargs):
            pass
        def get(self, path):
            raise ConsoleError("凭据无效", 401)
    monkeypatch.setattr(module, "AdminClient", Client)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("do not replace existing service"))
    with pytest.raises(ConsoleError, match="凭据无效") as failure:
        module.ensure_service("http://127.0.0.1:18083", data, project_root=root)
    assert failure.value.status == 401
