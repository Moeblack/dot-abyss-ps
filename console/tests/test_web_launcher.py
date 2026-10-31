import json
from pathlib import Path

from console import web_launcher


def test_demo_launcher_never_reads_token_or_connects(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(web_launcher, "edge_path", lambda: Path("C:/Edge/msedge.exe"))
    monkeypatch.setattr(web_launcher, "ensure_host", lambda *args: (_ for _ in ()).throw(AssertionError("no host in demo")))
    monkeypatch.setattr(web_launcher.subprocess, "Popen", lambda command, **kwargs: commands.append(command))
    web_launcher.launch(None, None, demo=True)
    assert "?demo=1" in commands[0][1]
    assert commands[0][1].startswith("--app=file:")
    assert not (tmp_path / "DotAbyssConsole" / "settings.json").exists()


def test_normal_launcher_opens_independent_host_ticket(tmp_path, monkeypatch):
    commands, requests = [], []
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(web_launcher, "edge_path", lambda: Path("C:/Edge/msedge.exe"))
    def ensure(origin, data):
        requests.append((origin, data))
        return {"url": "http://127.0.0.1:19000/#ticket=single-use-ticket-123456789"}
    monkeypatch.setattr(web_launcher, "ensure_host", ensure)
    monkeypatch.setattr(web_launcher.subprocess, "Popen", lambda command, **kwargs: commands.append(command))
    web_launcher.launch("http://127.0.0.1:18083", tmp_path / "data")
    assert requests == [("http://127.0.0.1:18083", tmp_path / "data")]
    assert "private-token" not in " ".join(commands[0])
    assert "#ticket=single-use-ticket-123456789" in commands[0][1]
    settings = json.loads((tmp_path / "DotAbyssConsole" / "settings.json").read_text(encoding="utf-8"))
    assert set(settings) == {"origin","data_dir"}
