from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import socket
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import ProxyHandler, Request, build_opener

import uvicorn

from dotabyss_ps.process_lock import ProcessLockBusy, exclusive_process_lock
from .client import ConsoleError, NoRedirect
from .service_bootstrap import canonical_origin
from .service_manager import ServiceManager, child_options
from .translation_manager import TranslationManager


def runtime_paths(root, origin, directory):
    identity = hashlib.sha256(f"{root}\n{origin}\n{directory}".encode("utf-8")).hexdigest()[:20]
    folder = root / "artifacts" / "launcher"
    return folder / f"host-{identity}.json", folder / f"host-{identity}.lock", folder / f"host-start-{identity}.lock"


def native_request(origin, path, token=None, body=None):
    headers = {"Accept": "application/json"}
    if token:
        headers["X-Host-Token"] = token
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = Request(origin + path, headers=headers, data=json.dumps(body).encode() if body is not None else None)
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=3) as response:
            payload = response.read(65537)
        if len(payload) > 65536:
            raise ConsoleError("启动器响应超过大小限制。")
        return json.loads(payload)
    except HTTPError as error:
        raise ConsoleError("启动器拒绝连接，请重新打开启动器。", error.code) from error
    except (ValueError, UnicodeError) as error:
        raise ConsoleError("启动器返回的内容无效。") from error


def existing_host(path, origin, directory):
    try:
        if path.stat().st_size > 16384:
            raise ConsoleError("启动器运行记录过大。")
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (ValueError, UnicodeError) as error:
        raise ConsoleError("启动器运行记录损坏，请检查：" + str(path)) from error
    if (not isinstance(data, dict) or data.get("game_origin") != origin or data.get("data_dir") != str(directory)
            or not isinstance(data.get("origin"), str)
            or not isinstance(data.get("token"), str) or not 32 <= len(data["token"]) <= 128):
        raise ConsoleError("启动器运行记录与当前配置不匹配。")
    address = canonical_origin(data["origin"])
    try:
        ping = native_request(address, "/host/ping")
    except (ConsoleError, URLError, TimeoutError, OSError):
        return None
    if ping != {"service": "dotabyss-launcher", "version": 1}:
        return None
    try:
        ticket = native_request(address, "/host/ticket", data["token"], {})
    except (URLError, TimeoutError, OSError):
        return None
    if not isinstance(ticket, dict) or not isinstance(ticket.get("ticket"), str):
        raise ConsoleError("启动器没有返回窗口票据。")
    return {"origin": address, "url": address + "/#ticket=" + quote(ticket["ticket"], safe="")}


def ensure_host(origin, data_dir, *, project_root=None, timeout=20):
    root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
    directory = Path(data_dir).resolve()
    origin = canonical_origin(origin)
    state_file, _, start_lock = runtime_paths(root, origin, directory)
    try:
        with exclusive_process_lock(start_lock):
            existing = existing_host(state_file, origin, directory)
            if existing:
                return existing
            python = root / ".venv" / "Scripts" / "python.exe"
            if not python.is_file():
                raise ConsoleError("启动器缺少项目 Python 环境。")
            command = [str(python), "-u", "-m", "console.host_runtime", "--origin", origin,
                       "--data-dir", str(directory)]
            log_path = state_file.with_suffix(".log")
            with log_path.open("ab", buffering=0) as log:
                process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                           **child_options(root))
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise ConsoleError("启动器后台未能启动，请查看：" + str(log_path))
                existing = existing_host(state_file, origin, directory)
                if existing:
                    return existing
                time.sleep(0.15)
            raise ConsoleError("等待启动器就绪超时，请查看：" + str(log_path))
    except ProcessLockBusy:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            existing = existing_host(state_file, origin, directory)
            if existing:
                return existing
            time.sleep(0.15)
        raise ConsoleError("另一个窗口正在启动控制台，请稍后重试。")


def main():
    from .host_api import create_host_app

    parser = argparse.ArgumentParser()
    parser.add_argument("--origin", required=True)
    parser.add_argument("--data-dir", required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    origin, directory = canonical_origin(args.origin), args.data_dir.resolve()
    state_file, lock, _ = runtime_paths(root, origin, directory)
    with exclusive_process_lock(lock):
        manager = ServiceManager(origin, directory, project_root=root)
        secret = secrets.token_urlsafe(36)
        app = create_host_app(manager, secret, TranslationManager(project_root=root))
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(128)
            app.state.origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
            record = {"origin": app.state.origin, "game_origin": origin, "data_dir": str(directory),
                      "pid": os.getpid(), "token": secret}
            temporary = state_file.with_suffix(".tmp")
            temporary.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
            temporary.replace(state_file)
            try:
                server = uvicorn.Server(uvicorn.Config(app, access_log=False, timeout_graceful_shutdown=25))
                server.run(sockets=[listener])
            finally:
                manager.close()
                state_file.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
