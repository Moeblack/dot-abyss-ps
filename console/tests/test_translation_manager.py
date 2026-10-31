import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from console import translation_manager as module
from console.client import ConsoleError
from dotabyss_ps.admin_launcher import GameProcess
from dotabyss_ps.process_lock import exclusive_process_lock


class Process:
    def __init__(self, pid=42):
        self.pid = pid
        self.returncode = None

    def poll(self):
        return self.returncode


@pytest.fixture
def context(tmp_path, monkeypatch):
    root = tmp_path / "project"
    python = root / ".venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture")
    entry = root / "pc-translation" / "prepare_translation.py"
    entry.parent.mkdir()
    entry.write_text("raise AssertionError('not executed')", encoding="utf-8")
    manager = module.TranslationManager(project_root=root)
    manager.client.mkdir(parents=True)
    (manager.client / "local-client.json").write_text(json.dumps({
        "identity": {"company": "LocalGames", "product": "DotAbyssLocalPlayer"},
        "executable": "game.exe",
    }), encoding="utf-8")
    (manager.client / "game.exe").write_bytes(b"fixture")
    commands, processes = [], []

    def spawn(command, **options):
        commands.append((command, options))
        process = Process(42 + len(processes))
        processes.append(process)
        return process

    monkeypatch.setattr(module, "game_processes", lambda _: [])
    monkeypatch.setattr(module.shutil, "which", lambda name: "C:/fixture/" + name + ".exe")
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    return SimpleNamespace(manager=manager, root=root, python=python, entry=entry, commands=commands, processes=processes)


def installed(context, *, enabled=True, language="zh_Hant"):
    target = context.manager.target
    for name in ("AbyssMod.dll", "Utility.dll", "fonts/ttcuyuanj", "cache/translations/manifest/zh_Hant.json"):
        path = target / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    (target / "integration-manifest.json").write_text(json.dumps({
        "language": "zh_Hant", "translation_commit": "a" * 40,
    }), encoding="utf-8")
    config = context.manager.client / "BepInEx" / "config" / "AbyssMod.cfg"
    config.parent.mkdir(parents=True, exist_ok=True)
    config.write_text(f"[Translation]\nEnabled = {str(enabled).lower()}\nLanguage = {language}\n", encoding="utf-8")


def log(context, phase, message="fixture phase", percent=50):
    path = context.manager._log_path()
    path.write_text("build output\n@@translation " + json.dumps({
        "phase": phase, "message": message, "percent": percent,
    }) + "\n", encoding="utf-8")


def test_unprepared_client_cannot_spawn_installation(context):
    (context.manager.client / "local-client.json").unlink()
    state = context.manager.status()
    assert state["state"] == "blocked" and not state["can_start"]
    with pytest.raises(ConsoleError) as error:
        context.manager.install()
    assert error.value.status == 409
    assert context.commands == []


@pytest.mark.parametrize("process", [GameProcess(10, "C:/fixture/game.exe"), GameProcess(10, None)])
def test_active_or_unidentified_game_blocks_installation(context, monkeypatch, process):
    monkeypatch.setattr(module, "game_processes", lambda _: [process])
    result = context.manager.status()
    assert result["game_running"] and not result["can_start"]
    with pytest.raises(ConsoleError):
        context.manager.install()
    assert context.commands == []


def test_failed_process_inspection_never_assumes_game_is_closed(context, monkeypatch):
    def denied(_):
        raise OSError("permission denied")
    monkeypatch.setattr(module, "game_processes", denied)
    assert not context.manager.status()["can_start"]
    with pytest.raises(ConsoleError):
        context.manager.install()
    assert context.commands == []


@pytest.mark.parametrize("tool", ["git", "dotnet"])
def test_missing_build_tool_blocks_before_job_creation(context, monkeypatch, tool):
    monkeypatch.setattr(module.shutil, "which", lambda name: None if name == tool else "fixture")
    with pytest.raises(ConsoleError):
        context.manager.install()
    assert context.manager.job is None and context.commands == []


def test_client_launch_lock_is_shared_with_translation(context):
    with exclusive_process_lock(context.manager.client / ".local-launcher.lock"):
        assert not context.manager.status()["can_start"]
        with pytest.raises(ConsoleError):
            context.manager.install()
    assert context.manager.status()["can_start"]


def test_installation_uses_fixed_background_command_and_durable_logs(context):
    result = context.manager.install()
    assert result["state"] == "running" and not result["can_start"]
    command, options = context.commands[0]
    assert command == [str(context.python), "-u", str(context.entry), "--client", str(context.manager.client),
                       "--bootstrap", "--fetch", "--apply"]
    assert options["stdin"] == subprocess.DEVNULL
    assert options["stdout"] is options["stderr"]
    assert options["cwd"] == context.root
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        assert options["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert (context.manager.jobs / "latest.json").is_file()
    assert context.manager._log_path().is_file()


def test_concurrent_and_repeated_clicks_share_one_job(context):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: context.manager.install(), range(4)))
    assert len({result["job_id"] for result in results}) == 1
    assert len(context.commands) == 1
    assert context.manager.install()["job_id"] == results[0]["job_id"]


def test_progress_is_reported_without_claiming_success_before_exit(context):
    context.manager.install()
    installed(context)
    log(context, "build", "正在构建", 70)
    current = context.manager.status()
    assert current["state"] == "running" and current["phase"] == "build" and current["percent"] == 70
    log(context, "complete", percent=100)
    assert context.manager.status()["state"] == "running"
    context.processes[0].returncode = 0
    completed = context.manager.status()
    assert completed["state"] == "succeeded" and completed["restart_required"]
    assert completed["installed"] and completed["enabled"]


@pytest.mark.parametrize("exit_code,enabled,marker", [(1, True, "complete"), (0, False, "complete"), (0, True, "build"), (1, True, "failed")])
def test_incomplete_or_failed_run_never_reports_success(context, exit_code, enabled, marker):
    context.manager.install()
    installed(context, enabled=enabled)
    log(context, marker)
    context.processes[0].returncode = exit_code
    current = context.manager.status()
    assert current["state"] == "failed" and not current["restart_required"]


def test_completed_job_survives_controller_recreation(context):
    context.manager.install()
    installed(context)
    log(context, "complete", percent=100)
    context.processes[0].returncode = 0
    context.manager.status()
    recovered = module.TranslationManager(project_root=context.root).status()
    assert recovered["state"] == "succeeded" and recovered["translation_revision"] == "a" * 40


def test_interrupted_job_failure_remains_visible_on_repeated_status(context):
    context.manager.install()
    recovered = module.TranslationManager(project_root=context.root)
    assert recovered.status()["state"] == "failed"
    assert recovered.status()["state"] == "failed"


def test_recovered_active_job_waits_for_shared_client_lock(context):
    started = context.manager.install()
    log(context, "sources", "fetching", 10)
    recovered = module.TranslationManager(project_root=context.root)
    with exclusive_process_lock(context.manager.client / ".local-launcher.lock"):
        result = recovered.status()
        assert result["state"] == "running" and not result["can_start"]
        assert result["job_id"] == started["job_id"]
    assert recovered.status()["state"] == "failed"
    assert recovered.status()["state"] == "failed"


def test_spawn_failure_remains_visible_after_error_response(context, monkeypatch):
    def fail(*args, **kwargs):
        raise OSError("fixture launch failure")
    monkeypatch.setattr(module.subprocess, "Popen", fail)
    with pytest.raises(ConsoleError) as error:
        context.manager.install()
    assert error.value.status == 503
    assert context.manager.status()["state"] == "failed"


def test_logs_are_bounded_and_strip_url_credentials(context):
    context.manager.install()
    context.manager._log_path().write_text("x" * 70000 + "\nhttps://user:private-password@example.com/repo\n", encoding="utf-8")
    result = context.manager.logs()
    assert "private-password" not in result["text"]
    assert len(result["text"].encode("utf-8")) <= 65536
    assert str(context.manager.jobs) in result["path"]


@pytest.mark.parametrize("enabled,language", [(False, "zh_Hant"), (True, "ja")])
def test_installed_files_do_not_imply_translation_enabled(context, enabled, language):
    installed(context, enabled=enabled, language=language)
    result = context.manager.status()
    assert result["installed"] and not result["enabled"] and result["can_start"]


def test_missing_plugin_file_is_not_a_complete_installation(context):
    installed(context)
    (context.manager.target / "Utility.dll").unlink()
    result = context.manager.status()
    assert not result["installed"] and not result["enabled"]
