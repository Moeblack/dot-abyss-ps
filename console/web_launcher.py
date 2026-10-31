from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from .client import ConsoleError, service_origin
from .host_runtime import ensure_host


def edge_path() -> Path:
    candidates = [Path(os.environ.get(name, "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe" for name in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
    for path in candidates:
        if path.is_file():
            return path
    raise ConsoleError("找不到 Microsoft Edge。请安装 Edge，或用 --classic 启动备用界面。")


def launch(origin: str | None, data_dir: Path | None, *, demo=False):
    console_home = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "DotAbyssConsole"
    settings_path = console_home / "settings.json"
    settings = {}
    try:
        if settings_path.stat().st_size < 16384:
            settings = json.loads(settings_path.read_text(encoding="utf-8"))
            if not isinstance(settings, dict):
                settings = {}
    except (OSError, ValueError):
        pass
    origin = service_origin(origin or settings.get("origin", "http://127.0.0.1:18083"))
    directory = data_dir or Path(settings.get("data_dir", str(Path(__file__).resolve().parents[1] / "data-pc-1.15.0")))
    executable = edge_path()
    if demo:
        url = (Path(__file__).resolve().parent / "host_web" / "index.html").as_uri() + "?demo=1"
    else:
        host = ensure_host(origin, directory)
        url = host["url"]
        console_home.mkdir(parents=True, exist_ok=True)
        temporary = settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"origin": origin, "data_dir": str(directory.resolve())}, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(settings_path)
    subprocess.Popen([str(executable), "--app=" + url, "--window-size=1440,960", "--no-first-run", "--user-data-dir=" + str(console_home / "Edge")], close_fds=True)
