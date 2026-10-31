from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from dotabyss_ps.process_lock import ProcessLockBusy, exclusive_process_lock

from .client import AdminClient, ConsoleError, NoRedirect, read_token, service_origin


def canonical_origin(value):
    parsed = urlsplit(service_origin(value))
    port = parsed.port
    if parsed.hostname not in ("127.0.0.1", "localhost") or port is None or not 1024 <= port <= 65535:
        raise ConsoleError("启动器需要本机 IPv4 服务地址，例如 http://127.0.0.1:18083")
    return f"http://127.0.0.1:{port}"


def port_is_available(origin):
    parsed = urlsplit(canonical_origin(origin))
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", parsed.port))
        return True
    except OSError:
        return False


def health(origin):
    opener = build_opener(ProxyHandler({}), NoRedirect())
    try:
        with opener.open(Request(origin + "/healthz", headers={"Accept": "application/json"}), timeout=2) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ConsoleError("该端口返回了异常内容，请检查服务地址。")
            result = json.loads(raw)
            if isinstance(result, dict) and response.headers.get("X-DotAbyss-Service-Instance"):
                result["instance"] = response.headers["X-DotAbyss-Service-Instance"]
    except HTTPError as error:
        raise ConsoleError("该端口已有服务，但没有正确响应游戏服务检查。", error.code) from error
    except URLError as error:
        reason = error.reason
        if isinstance(reason, ConnectionRefusedError) or getattr(reason, "winerror", None) == 10061 or getattr(reason, "errno", None) in (61, 111, 10061):
            return None
        if isinstance(reason, (TimeoutError, socket.timeout)) and port_is_available(origin):
            return None
        raise ConsoleError("本地服务没有及时响应，请检查现有服务窗口；启动器不会另外启动冲突进程。") from error
    except (TimeoutError, socket.timeout) as error:
        if port_is_available(origin):
            return None
        raise ConsoleError("本地服务响应超时，请检查现有服务窗口。") from error
    except (ValueError, UnicodeError) as error:
        raise ConsoleError("该端口运行的不是兼容游戏服务。") from error
    if not isinstance(result, dict) or result.get("service") != "dotabyss-local" or result.get("status") != "ok":
        raise ConsoleError("该端口运行的不是兼容游戏服务。")
    return result


def compatible_client(origin, data_dir):
    client = AdminClient(origin, read_token(data_dir), timeout=4)
    status = client.get("/admin/status")
    capabilities = status.get("capabilities") if isinstance(status, dict) else None
    if not isinstance(capabilities, dict) or capabilities.get("launcher") is not True:
        raise ConsoleError("现有服务还没有启动器接口。请在原服务窗口重启一次，再打开启动器。", 409)
    try:
        result = client.get("/admin/launcher/status")
    except ConsoleError as error:
        if error.status == 404:
            raise ConsoleError("现有服务还没有启动器接口。请在原服务窗口重启一次，再打开启动器。", 409) from error
        raise
    if not isinstance(result, dict) or "running" not in result or "available" not in result:
        raise ConsoleError("现有服务版本不兼容，请重启更新后的本地服务。")
    return client


def wait_until_ready(origin, data_dir, timeout, process=None, log_path=None):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise ConsoleError("本地服务启动失败，请查看日志：" + str(log_path))
        if health(origin) is not None:
            return compatible_client(origin, data_dir)
        time.sleep(0.2)
    raise ConsoleError("等待服务启动超时，进程仍保留；请查看日志后重开启动器：" + str(log_path or "服务窗口"))


def ensure_service(origin, data_dir, *, project_root=None, timeout=30):
    root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
    directory = Path(data_dir).resolve()
    normalized = canonical_origin(origin)
    if health(normalized) is not None:
        return {"origin": normalized, "started": False, "client": compatible_client(normalized, directory)}
    python = root / ".venv" / "Scripts" / "python.exe"
    schema = root / "artifacts" / "master-schema-pc-1.15.0.json"
    if not python.is_file() or not schema.is_file() or not (directory / "local.sqlite3").is_file():
        raise ConsoleError("缺少游戏服务环境、主数据结构或存档数据库，请检查项目和数据目录。")
    port = urlsplit(normalized).port
    lock_path = root / "artifacts" / "launcher" / f"service-{port}.lock"
    try:
        with exclusive_process_lock(lock_path):
            if health(normalized) is not None:
                return {"origin": normalized, "started": False, "client": compatible_client(normalized, directory)}
            log_path = root / "artifacts" / "launcher" / f"server-{port}-{datetime.now():%Y%m%d-%H%M%S-%f}.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            command = [str(python), "-m", "dotabyss_ps.app", "--data-dir", str(directory), "--host", "127.0.0.1",
                       "--port", str(port), "--new-game-schema", str(schema)]
            environment = dict(os.environ)
            environment["PYTHONPATH"] = str(root / "server")
            environment["NO_PROXY"] = "127.0.0.1,localhost,::1"
            flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0
            try:
                with log_path.open("ab", buffering=0) as log:
                    process = subprocess.Popen(command, cwd=root, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                               creationflags=flags, close_fds=True)
            except OSError as error:
                raise ConsoleError("无法启动本地服务：" + str(error)) from error
            client = wait_until_ready(normalized, directory, timeout, process, log_path)
            return {"origin": normalized, "started": True, "client": client, "pid": process.pid, "log_path": str(log_path)}
    except ProcessLockBusy:
        client = wait_until_ready(normalized, directory, timeout)
        return {"origin": normalized, "started": False, "client": client}
