import json
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from dotabyss_ps import admin_launcher as module
from dotabyss_ps.admin_launcher import GameLauncher, GameProcess
from dotabyss_ps.admin_store import AdminJournal
from dotabyss_ps.gameplay_catalog import GameplayError
from dotabyss_ps.storage import Store


@pytest.fixture
def launch_case(tmp_path, monkeypatch):
    root = tmp_path / "project"
    release = root / "pc" / "releases" / "pc-1.15.0.json"
    release.parent.mkdir(parents=True)
    release.write_text(json.dumps({"identity": {"company": "LocalGames", "product": "DotAbyssLocalPlayer"}, "executable": "game.exe"}), encoding="utf-8")
    client = tmp_path / "dotabyss_pc" / "local-client"
    plugin = client / "BepInEx" / "plugins" / "DotAbyss.Local.Pc"
    plugin.mkdir(parents=True)
    executable = client / "game.exe"
    executable.write_bytes(b"isolated test; never executed")
    config = plugin / "pc-local.json"
    config.write_text(json.dumps({"Profile": "first", "ServiceOrigin": "http://127.0.0.1:18083/", "ExpectedCompany": "LocalGames",
                                 "ExpectedProduct": "DotAbyssLocalPlayer", "ExpectedPersistentPath": "test-profile", "TimeoutSeconds": 20}, indent=2), encoding="utf-8")
    store = Store(root / "data" / "local.sqlite3")
    AdminJournal(store)
    first, second = store.login("first"), store.login("second")
    with store.connection() as db:
        db.execute("INSERT INTO master_datasets VALUES('test','{}',0)")
        for profile in (first, second):
            db.execute("INSERT INTO game_states VALUES(?,1,'test','{}','now')", (profile["user_id"],))
    local = SimpleNamespace(_active=lambda: {"revision": "test"})
    launcher = GameLauncher(store, local, project_root=root)
    processes, commands = [], []
    monkeypatch.setattr(module, "game_processes", lambda _: list(processes))
    monkeypatch.setattr(launcher, "_preflight", lambda _: {"profile": "test-profile"})
    class Process:
        pid = 1234
        def wait(self, timeout):
            raise subprocess.TimeoutExpired("game", timeout)
        def poll(self):
            return None
    def spawn(command, **kwargs):
        commands.append((command, kwargs))
        processes.append(GameProcess(1234, str(executable), 100.0))
        return Process()
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    return SimpleNamespace(root=root, client=client, config=config, store=store, local=local, launcher=launcher, processes=processes,
                           commands=commands, first=first["user_id"], second=second["user_id"], executable=executable)


def test_launch_sets_selected_profile_once_and_preserves_other_plugins(launch_case):
    c = launch_case
    translation = c.client / "BepInEx" / "plugins" / "AbyssMod" / "AbyssMod.dll"
    translation.parent.mkdir()
    translation.write_bytes(b"keep translator")
    before = c.config.read_bytes()
    result = c.launcher.start(c.second, "http://localhost:18083/")
    assert result["status"] == "started" and result["profile_id"] == c.second
    configured = json.loads(c.config.read_text())
    assert configured["Profile"] == "second" and configured["ServiceOrigin"] == "http://127.0.0.1:18083/"
    assert configured["TimeoutSeconds"] == 20 and translation.read_bytes() == b"keep translator"
    assert c.commands[0][0][0] == str(c.executable)
    assert c.commands[0][1]["env"]["NO_PROXY"] == "127.0.0.1,localhost,::1"
    assert next((c.root / "artifacts" / "launcher").glob("pc-local-*.json")).read_bytes() == before
    assert c.launcher.start(c.second)["status"] == "already_running"
    with pytest.raises(GameplayError, match="另一份存档"):
        c.launcher.start(c.first)
    assert len(c.commands) == 1
    configured["Profile"] = "first"
    c.config.write_text(json.dumps(configured))
    assert c.launcher.status()["profile_id"] == c.second


def test_spawn_failure_restores_exact_configuration(launch_case, monkeypatch):
    c = launch_case
    before = c.config.read_bytes()
    def fail(*args, **kwargs):
        raise OSError("no process")
    monkeypatch.setattr(module.subprocess, "Popen", fail)
    with pytest.raises(GameplayError, match="启动失败"):
        c.launcher.start(c.second)
    assert c.config.read_bytes() == before
    assert not c.launcher.receipt_path.exists()


def test_immediate_process_exit_restores_configuration(launch_case, monkeypatch):
    c = launch_case
    before = c.config.read_bytes()
    monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=1, wait=lambda **k: 5, poll=lambda: 5))
    with pytest.raises(GameplayError, match="初始化前退出"):
        c.launcher.start(c.second)
    assert c.config.read_bytes() == before


@pytest.mark.parametrize("origin", ["https://127.0.0.1:18083", "http://example.com:18083", "http://127.0.0.1:18083/path", "http://user@127.0.0.1:18083"])
def test_launch_cannot_redirect_to_external_origin(launch_case, origin):
    c = launch_case
    before = c.config.read_bytes()
    with pytest.raises(GameplayError):
        c.launcher.start(c.first, origin)
    assert not c.commands and c.config.read_bytes() == before


def test_unknown_or_running_process_is_not_overwritten(launch_case):
    c = launch_case
    before = c.config.read_bytes()
    c.processes.append(GameProcess(99, None))
    assert c.launcher.status()["status"] == "process_unknown"
    with pytest.raises(GameplayError):
        c.launcher.start(c.second)
    assert not c.commands and c.config.read_bytes() == before


def test_recent_heartbeat_from_previous_game_is_not_current_identity(launch_case, monkeypatch):
    c = launch_case
    monkeypatch.setattr(module.time, "time", lambda: 1000)
    c.processes.append(GameProcess(88, str(c.executable), 995.5))
    with c.store.connection() as db:
        db.execute("INSERT INTO admin_clients VALUES(?,?,0,0,'connected','','',?)", (c.first, "old-game", 994))
    assert c.launcher.status()["profile_id"] is None
    with c.store.connection() as db:
        db.execute("INSERT INTO admin_clients VALUES(?,?,0,0,'connected','','',?)", (c.second, "new-game", 996))
    assert c.launcher.status()["profile_id"] == c.second


def test_concurrent_launchers_start_only_one_game(launch_case, monkeypatch):
    c = launch_case
    other = GameLauncher(c.store, c.local, project_root=c.root)
    monkeypatch.setattr(other, "_preflight", lambda _: {"profile": "test-profile"})
    previous = module.subprocess.Popen
    def slow_spawn(*args, **kwargs):
        time.sleep(0.05)
        return previous(*args, **kwargs)
    monkeypatch.setattr(module.subprocess, "Popen", slow_spawn)
    def start(launcher):
        try:
            return launcher.start(c.second)["status"]
        except GameplayError as error:
            assert error.status == 409
            return "busy"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(start, [c.launcher, other]))
    assert "started" in results
    assert len(c.commands) == 1


def test_preflight_rejects_unbound_assets_before_changing_profile(launch_case, monkeypatch):
    c = launch_case
    before = c.config.read_bytes()
    monkeypatch.delattr(c.launcher, "_preflight")
    monkeypatch.setattr(c.local, "_active", lambda: {"revision": "test", "client": {"package": "dotabyss_x_cl", "version": "1.15.0"}})
    release = c.root / "pc" / "releases" / "pc-1.15.0.json"
    release.write_text(json.dumps({**json.loads(release.read_text()), "application_version": "1.15.0"}))
    with pytest.raises(GameplayError, match="资源未就绪"):
        c.launcher.start(c.first)
    assert c.config.read_bytes() == before and not c.commands
