from types import SimpleNamespace
import subprocess

import pytest

from console import service_manager as module
from console.client import ConsoleError


class Pipe:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, value):
        assert not self.closed
        self.data += value

    def flush(self):
        assert not self.closed

    def close(self):
        self.closed = True


class Process:
    def __init__(self, pid=42, returncode=None):
        self.pid, self.returncode = pid, returncode
        self.stdin = Pipe()
        self.waits = []
        self.timeout = False

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.waits.append(timeout)
        if self.timeout:
            raise subprocess.TimeoutExpired("fixture-worker", timeout)
        self.returncode = 0
        return 0

    def terminate(self):
        pytest.fail("graceful lifecycle must not terminate processes")

    def kill(self):
        pytest.fail("graceful lifecycle must not kill processes")


@pytest.fixture
def context(tmp_path, monkeypatch):
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
    secret = "private-admin-token-12345678901234567890"
    (data / "admin-token.txt").write_text(secret)
    manager = module.ServiceManager("http://localhost:18083", data, project_root=root)
    commands, processes = [], []

    def spawn(command, **options):
        process = Process(42 + len(processes))
        commands.append((command, options))
        processes.append(process)
        return process

    def health(_):
        if manager.process is not None and manager.process.poll() is None:
            return {"status": "ok", "instance": manager.instance}
        return None

    client = SimpleNamespace(post=lambda *_: {"ticket": "single-use-window-ticket-1234"})
    monkeypatch.setattr(module, "health", health)
    monkeypatch.setattr(module, "compatible_client", lambda *_: client)
    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    return SimpleNamespace(manager=manager, root=root, data=data, python=python, schema=schema,
                           commands=commands, processes=processes, secret=secret, client=client)


def test_external_service_is_connected_but_cannot_be_stopped_or_replaced(context, monkeypatch):
    monkeypatch.setattr(module, "health", lambda _: {"status": "ok"})
    manager = context.manager
    status = manager.start()
    assert status["state"] == "external" and status["ready"]
    assert not status["managed"] and not status["can_stop"] and not status["can_restart"]
    with pytest.raises(ConsoleError) as error:
        manager.stop()
    assert error.value.status == 409
    with pytest.raises(ConsoleError):
        manager.restart()
    assert not context.commands


def test_start_uses_fixed_hidden_worker_and_tracks_its_identity(context):
    status = context.manager.start()
    assert status["state"] == "running" and status["managed"] and status["ready"]
    command, options = context.commands[0]
    assert command[:4] == [str(context.python), "-u", "-m", "console.service_worker"]
    assert command[command.index("--origin") + 1] == "http://127.0.0.1:18083"
    assert command[command.index("--data-dir") + 1] == str(context.data)
    assert command[command.index("--schema") + 1] == str(context.schema)
    assert options["stdin"] == subprocess.PIPE
    assert options["stdout"] is options["stderr"]
    assert context.secret not in " ".join(command)
    if hasattr(subprocess, "CREATE_NO_WINDOW"):
        assert options["creationflags"] & subprocess.CREATE_NO_WINDOW
    assert context.manager.start()["pid"] == status["pid"]
    assert len(context.commands) == 1


def test_stop_signals_only_owned_pipe_and_waits_before_reporting_stopped(context):
    manager = context.manager
    manager.start()
    process = context.processes[0]
    result = manager.stop()
    assert process.stdin.data == b"stop\n"
    assert process.waits == [manager.stop_timeout]
    assert process.stdin.closed
    assert result["state"] == "stopped" and not result["ready"]
    assert result["can_start"] and not result["can_stop"]
    assert manager.stop()["state"] == "stopped"


def test_restart_replaces_owned_instance_only_after_graceful_exit(context):
    manager = context.manager
    initial = manager.start()
    result = manager.restart()
    assert result["state"] == "running" and result["ready"]
    assert result["instance"] != initial["instance"]
    assert result["pid"] != initial["pid"]
    assert context.processes[0].stdin.data == b"stop\n"
    assert context.processes[0].returncode == 0
    assert len(context.commands) == 2


def test_start_after_child_crash_closes_old_control_pipe(context):
    manager = context.manager
    manager.start()
    previous = context.processes[0]
    previous.returncode = 2
    assert manager.start()["state"] == "running"
    assert previous.stdin.closed
    assert len(context.commands) == 2


def test_stop_after_child_exit_releases_old_control_pipe(context):
    manager = context.manager
    manager.start()
    previous = context.processes[0]
    previous.returncode = 0
    assert manager.stop()["state"] == "stopped"
    assert previous.stdin.closed
    assert manager.process is None


def test_wrong_port_or_credentials_never_launch_another_process(context, monkeypatch):
    def conflict(_):
        raise module.ServiceMismatch("wrong service", 409)
    monkeypatch.setattr(module, "health", conflict)
    assert context.manager.status()["state"] == "conflict"
    with pytest.raises(ConsoleError, match="wrong service"):
        context.manager.start()
    assert not context.commands


def test_start_failure_reports_child_exit_without_killing(context, monkeypatch):
    process = Process(returncode=2)
    monkeypatch.setattr(module.subprocess, "Popen", lambda *_args, **_kwargs: process)
    with pytest.raises(ConsoleError) as error:
        context.manager.start()
    assert error.value.status == 503
    assert context.manager.status()["state"] == "error"
    assert context.manager.status()["can_start"]
    assert process.stdin.data == b""


def test_start_timeout_keeps_owned_process_available_for_graceful_stop(context, monkeypatch):
    manager = context.manager
    manager.start_timeout = 0
    monkeypatch.setattr(module, "health", lambda _: None)
    with pytest.raises(ConsoleError) as error:
        manager.start()
    assert error.value.status == 504
    status = manager.status()
    assert status["state"] == "error" and status["can_stop"] and not status["can_start"]
    assert context.processes[0].stdin.data == b""
    assert manager.stop()["state"] == "stopped"
    assert context.processes[0].stdin.data == b"stop\n"


def test_stop_timeout_never_force_kills_and_retry_does_not_resend_command(context):
    manager = context.manager
    manager.start()
    process = context.processes[0]
    process.timeout = True
    with pytest.raises(ConsoleError) as error:
        manager.stop()
    assert error.value.status == 504
    assert process.poll() is None and not process.stdin.closed
    assert process.stdin.data == b"stop\n"
    status = manager.status()
    assert status["state"] == "stopping" and not status["ready"]
    assert not any(status[key] for key in ("can_start", "can_stop", "can_restart"))
    process.timeout = False
    assert manager.stop()["state"] == "stopped"
    assert process.stdin.data == b"stop\n"


def test_delayed_graceful_exit_clears_timeout_from_reported_status(context):
    manager = context.manager
    manager.start()
    process = context.processes[0]
    process.timeout = True
    with pytest.raises(ConsoleError):
        manager.stop()
    previous_error = manager.last_error
    assert previous_error
    process.returncode = 0
    status = manager.status()
    assert status["state"] == "stopped" and status["can_start"]
    assert not status["ready"] and previous_error not in status["message"]
    assert manager.stop()["state"] == "stopped"
    assert process.stdin.closed and manager.last_error == ""


def test_other_service_winning_bind_race_only_signals_owned_child(context, monkeypatch):
    manager = context.manager
    monkeypatch.setattr(module, "health", lambda _: {"instance": "another-worker"} if manager.process else None)
    with pytest.raises(ConsoleError) as error:
        manager.start()
    assert error.value.status == 409
    assert context.processes[0].stdin.data == b"stop\n"
    assert manager.status()["state"] == "stopping"


def test_overlapping_lifecycle_actions_are_rejected_without_spawning(context):
    manager = context.manager
    manager._operation.acquire()
    try:
        for action in (manager.start, manager.stop, manager.restart):
            with pytest.raises(ConsoleError) as error:
                action()
            assert error.value.status == 409
    finally:
        manager._operation.release()
    assert not context.commands


def test_log_tail_is_bounded_and_does_not_expose_admin_token(context):
    manager = context.manager
    manager.log_path = context.root / "worker.log"
    manager.log_path.write_text("old\n" * 30000 + "latest " + context.secret + "\n", encoding="utf-8")
    result = manager.logs()
    assert len(result["text"].encode()) <= 65536
    assert result["text"].rstrip().endswith("latest [凭据]")
    assert context.secret not in result["text"]
    assert result["path"] == str(manager.log_path)


def test_console_url_uses_game_port_and_short_lived_ticket_not_admin_secret(context):
    context.manager.start()
    url = context.manager.console_ticket()["url"]
    assert url == "http://127.0.0.1:18083/console/#ticket=single-use-window-ticket-1234"
    assert context.secret not in url


def test_missing_runtime_never_spawns_worker(context):
    context.python.unlink()
    with pytest.raises(ConsoleError) as error:
        context.manager.start()
    assert error.value.status == 409
    assert not context.commands
