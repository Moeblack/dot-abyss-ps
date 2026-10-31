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


def test_first_launch_creates_configuration_from_verified_client(launch_case):
    c = launch_case
    c.config.unlink()
    status = c.launcher.status()
    assert status["available"] and status["can_start"] and not status["running"]
    assert status["configured_profile_id"] is None and not c.config.exists()
    result = c.launcher.start(c.second, "http://localhost:18084/")
    assert result["status"] == "started"
    config = json.loads(c.config.read_text())
    assert config == {"Profile": "second", "ServiceOrigin": "http://127.0.0.1:18084/",
                      "ExpectedCompany": "LocalGames", "ExpectedProduct": "DotAbyssLocalPlayer",
                      "ExpectedPersistentPath": "test-profile", "TimeoutSeconds": 20}
    assert json.loads(c.launcher.receipt_path.read_text())["config_backup"] is None
    assert not list((c.root / "artifacts").rglob("pc-local-*.json"))
    assert c.launcher.start(c.second)["status"] == "already_running"
    assert len(c.commands) == 1


@pytest.mark.parametrize("failure", ["spawn", "exit", "write"])
def test_first_launch_failure_restores_missing_configuration(launch_case, monkeypatch, failure):
    c = launch_case
    c.config.unlink()
    def fail(*args, **kwargs):
        raise OSError("failed")
    if failure == "spawn":
        monkeypatch.setattr(module.subprocess, "Popen", fail)
    elif failure == "exit":
        monkeypatch.setattr(module.subprocess, "Popen", lambda *a, **k: SimpleNamespace(pid=1, wait=lambda **k: 5, poll=lambda: 5))
    else:
        monkeypatch.setattr(module, "atomic_bytes", fail)
    with pytest.raises(GameplayError, match="启动失败"):
        c.launcher.start(c.second)
    assert not c.config.exists() and not c.launcher.receipt_path.exists()


@pytest.mark.parametrize("contents", ["{", "[]", '{"Profile":"first","Profile":"second"}', " " * 65537],
                         ids=["malformed", "array", "duplicate", "oversized"])
def test_invalid_configuration_is_not_replaced(launch_case, contents):
    c = launch_case
    c.config.write_text(contents, encoding="utf-8")
    assert not c.launcher.status()["available"]
    with pytest.raises(GameplayError):
        c.launcher.start(c.second)
    assert c.config.read_text(encoding="utf-8") == contents and not c.commands


@pytest.mark.parametrize("kind", ["directory", "symlink", "broken_symlink"])
def test_nonregular_configuration_is_not_replaced(launch_case, kind):
    c = launch_case
    c.config.unlink()
    target = c.config.parent / "other.json"
    if kind == "directory":
        c.config.mkdir()
    else:
        if kind == "symlink":
            target.write_text("{}", encoding="utf-8")
        try:
            c.config.symlink_to(target)
        except OSError as error:
            pytest.skip("Creating a symlink is unavailable: " + str(error))
    assert not c.launcher.status()["available"]
    with pytest.raises(GameplayError):
        c.launcher.start(c.second)
    assert not c.commands
    if kind == "directory":
        assert c.config.is_dir()
    else:
        assert c.config.is_symlink()
        assert target.read_text(encoding="utf-8") == "{}" if kind == "symlink" else not target.exists()


def test_missing_configuration_does_not_hide_running_game(launch_case):
    c = launch_case
    c.config.unlink()
    c.processes.append(GameProcess(99, None))
    assert c.launcher.status()["status"] == "process_unknown"
    with pytest.raises(GameplayError, match="游戏正在使用"):
        c.launcher.start(c.second)
    assert not c.commands and not c.config.exists()


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


@pytest.mark.parametrize("existing_config", [True, False])
def test_concurrent_launchers_start_only_one_game(launch_case, monkeypatch, existing_config):
    c = launch_case
    if not existing_config:
        c.config.unlink()
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


@pytest.mark.parametrize("existing_config", [True, False])
@pytest.mark.parametrize("catalog", [{}, {"catalog_served": False}])
def test_preflight_requires_catalog_request_mapping(launch_case, monkeypatch, existing_config, catalog):
    c = launch_case
    before = c.config.read_bytes() if existing_config else None
    if not existing_config:
        c.config.unlink()
    monkeypatch.delattr(c.launcher, "_preflight")
    monkeypatch.setattr(c.local, "_active", lambda: {"revision": "test", "client": {"package": "dotabyss_x_cl", "version": "1.15.0"}})
    release = c.root / "pc" / "releases" / "pc-1.15.0.json"
    release.write_text(json.dumps({**json.loads(release.read_text()), "application_version": "1.15.0"}))
    monkeypatch.setattr(module.AssetCatalog, "status", lambda _: {"active": {"revision": "test", **catalog}, "error": ""})
    with pytest.raises(GameplayError, match="catalog 请求路径映射"):
        c.launcher.start(c.first)
    assert (c.config.read_bytes() if c.config.exists() else None) == before and not c.commands


def test_first_launch_creates_nothing_until_full_preflight_succeeds(launch_case, monkeypatch):
    c = launch_case
    c.config.unlink()
    monkeypatch.delattr(c.launcher, "_preflight")
    monkeypatch.setattr(c.local, "_active", lambda: {"revision": "test", "client": {"package": "dotabyss_x_cl", "version": "1.15.0"}})
    release = c.root / "pc" / "releases" / "pc-1.15.0.json"
    release.write_text(json.dumps({**json.loads(release.read_text()), "application_version": "1.15.0"}))
    monkeypatch.setattr(module.AssetCatalog, "status", lambda _: {"active": {"catalog_served": True}, "error": ""})
    monkeypatch.setattr(module.AssetCatalog, "active", lambda _: {
        "prefix": module.PC_CATALOG_PREFIX, "catalog": {"relative_path": module.PC_CATALOG_PATH},
        "catalog_hash": {"relative_path": module.PC_CATALOG_HASH_PATH}})
    def verify(_):
        assert not c.config.exists() and not c.commands
        return {"profile": "test-profile"}
    monkeypatch.setattr(c.launcher, "_verify_client", verify)
    with pytest.raises(GameplayError, match="缺少私服客户端插件"):
        c.launcher.start(c.first)
    assert not c.config.exists() and not c.commands
    (c.config.parent / "DotAbyss.Local.Pc.dll").write_bytes(b"test plugin")
    assert c.launcher.start(c.first)["status"] == "started"
    assert json.loads(c.config.read_text())["ExpectedPersistentPath"] == "test-profile"


@pytest.mark.parametrize("wrong_binding", ["prefix", "catalog", "catalog_hash", "missing"])
def test_preflight_rejects_served_catalog_bound_to_different_paths(launch_case, monkeypatch, wrong_binding):
    c = launch_case
    c.config.unlink()
    monkeypatch.delattr(c.launcher, "_preflight")
    monkeypatch.setattr(c.local, "_active", lambda: {"revision": "test", "client": {"package": "dotabyss_x_cl", "version": "1.15.0"}})
    release = c.root / "pc" / "releases" / "pc-1.15.0.json"
    release.write_text(json.dumps({**json.loads(release.read_text()), "application_version": "1.15.0"}))
    active = {"prefix": module.PC_CATALOG_PREFIX, "catalog": {"relative_path": module.PC_CATALOG_PATH},
              "catalog_hash": {"relative_path": module.PC_CATALOG_HASH_PATH}}
    if wrong_binding == "missing":
        active = None
    elif wrong_binding == "prefix":
        active["prefix"] = "resources/android/r18/aas/0.1.0/aa"
    else:
        active[wrong_binding]["relative_path"] = module.PC_CATALOG_PREFIX + "/1791059991." + ("bin" if wrong_binding == "catalog" else "hash")
    catalogs = []
    def status(catalog):
        catalogs.append(catalog)
        return {"active": {"catalog_served": True}, "error": ""}
    def get_active(catalog):
        assert catalog is catalogs[0]
        return active
    monkeypatch.setattr(module.AssetCatalog, "status", status)
    monkeypatch.setattr(module.AssetCatalog, "active", get_active)
    with pytest.raises(GameplayError, match="catalog 请求路径映射不匹配"):
        c.launcher.start(c.first)
    assert not c.config.exists() and not c.commands
