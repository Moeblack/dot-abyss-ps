from __future__ import annotations

import ctypes
import importlib.util
import json
import math
import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

from .asset_index import PC_CATALOG_HASH_PATH, PC_CATALOG_PATH, PC_CATALOG_PREFIX
from .assets import AssetCatalog, AssetDataError
from .gameplay_catalog import GameplayError
from .process_lock import ProcessLockBusy, exclusive_process_lock


@dataclass(frozen=True)
class GameProcess:
    pid: int
    path: str | None
    started_at: float | None = None


def game_processes(executable):
    if os.name != "nt":
        raise GameplayError("游戏启动器需要 Windows 环境", 503, 5038)
    from ctypes import wintypes

    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD), ("th32ProcessID", wintypes.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                    ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", wintypes.LONG),
                    ("dwFlags", wintypes.DWORD), ("szExeFile", wintypes.WCHAR * 260)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(Entry)]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise GameplayError("无法检查当前游戏进程，请使用相同用户权限运行启动器", 503, 5038)
    result = []
    try:
        entry = Entry()
        entry.dwSize = ctypes.sizeof(entry)
        present = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while present:
            if entry.szExeFile.casefold() == executable.name.casefold():
                handle = kernel.OpenProcess(0x1000, False, entry.th32ProcessID)
                path, started_at = None, None
                if handle:
                    try:
                        length = wintypes.DWORD(32768)
                        buffer = ctypes.create_unicode_buffer(length.value)
                        if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                            path = buffer.value
                        created, exited, system, user = (wintypes.FILETIME() for _ in range(4))
                        if kernel.GetProcessTimes(handle, ctypes.byref(created), ctypes.byref(exited), ctypes.byref(system), ctypes.byref(user)):
                            started_at = ((created.dwHighDateTime << 32) | created.dwLowDateTime) / 10000000 - 11644473600
                    finally:
                        kernel.CloseHandle(handle)
                if path is None or Path(path).resolve() == executable.resolve():
                    result.append(GameProcess(entry.th32ProcessID, path, started_at))
            present = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    return result


def read_object(path, limit=2 * 1024 * 1024, *, missing_ok=False):
    if missing_ok and not path.exists() and not path.is_symlink():
        return None
    if not path.is_file() or path.is_symlink() or path.stat().st_size > limit:
        raise ValueError("启动配置不存在、过大或不是普通文件：" + path.name)
    def unique(items):
        data = {}
        for key, value in items:
            if key in data:
                raise ValueError("启动配置包含重复字段：" + key)
            data[key] = value
        return data
    result = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique)
    if not isinstance(result, dict):
        raise ValueError("启动配置必须是对象：" + path.name)
    return result


def atomic_bytes(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".partial", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


class GameLauncher:
    def __init__(self, store, local_state, *, project_root=None):
        self.store, self.local = store, local_state
        self.project_root = Path(project_root or Path(__file__).resolve().parents[2]).resolve()
        self.client_root = (self.project_root.parent / "dotabyss_pc" / "local-client").resolve()
        self.release_path = self.project_root / "pc" / "releases" / "pc-1.15.0.json"
        self.receipt_path = self.client_root / "local-launcher-session.json"

    def _paths(self):
        release = read_object(self.release_path)
        if release.get("identity") != {"company": "LocalGames", "product": "DotAbyssLocalPlayer"}:
            raise ValueError("启动器版本绑定不匹配")
        name = release.get("executable")
        if not isinstance(name, str) or Path(name).name != name or not name.lower().endswith(".exe"):
            raise ValueError("本地游戏可执行文件配置无效")
        executable = (self.client_root / name).resolve()
        config = self.client_root / "BepInEx" / "plugins" / "DotAbyss.Local.Pc" / "pc-local.json"
        if not executable.is_relative_to(self.client_root) or not config.resolve().is_relative_to(self.client_root):
            raise ValueError("游戏路径离开已准备的客户端目录")
        return release, executable, config

    def _profile(self, identifier):
        if type(identifier) is not int or identifier <= 0:
            raise GameplayError("请选择有效的存档", 400, 4007)
        with self.store.connection() as db:
            row = db.execute("SELECT p.id,p.name,g.master_revision FROM profiles p LEFT JOIN game_states g ON g.profile_id=p.id WHERE p.id=?", (identifier,)).fetchone()
        if row is None or row["master_revision"] is None:
            raise GameplayError("该存档尚未初始化，请先新建存档", 409, 4098)
        if self.local is None or self.local._active()["revision"] != row["master_revision"]:
            raise GameplayError("该存档与当前服务主数据版本不匹配", 409, 4098)
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", row["name"]):
            raise GameplayError("存档内部标识无效", 409, 4098)
        return dict(row)

    def _profile_id(self, name):
        with self.store.connection() as db:
            row = db.execute("SELECT id FROM profiles WHERE name=?", (name,)).fetchone()
        return row[0] if row else None

    def _running_profile(self, process):
        try:
            receipt = read_object(self.receipt_path)
            if (receipt.get("pid") == process.pid and process.started_at is not None
                    and abs(receipt.get("process_started_at", -1) - process.started_at) < 0.1
                    and type(receipt.get("profile_id")) is int
                    and self._profile_id(receipt.get("profile_name")) == receipt["profile_id"]):
                return receipt.get("profile_id"), receipt.get("profile_name")
        except (ValueError, OSError, TypeError):
            pass
        if process.started_at is None:
            return None, None
        since = max(int(time.time()) - 20, math.ceil(process.started_at))
        with self.store.connection() as db:
            rows = db.execute("SELECT DISTINCT p.id,p.name FROM admin_clients a JOIN profiles p ON p.id=a.profile_id WHERE a.at>=?", (since,)).fetchall()
        return (rows[0]["id"], rows[0]["name"]) if len(rows) == 1 else (None, None)

    def status(self):
        result = {"available": False, "running": False, "status": "unavailable", "pid": None,
                  "profile_id": None, "profile_name": None, "active_profile_id": None, "active_profile_name": None,
                  "configured_profile_id": None, "configured_profile_name": None, "can_start": False, "reason": ""}
        try:
            _, executable, config_path = self._paths()
            if not executable.is_file():
                raise ValueError("尚未准备本地游戏客户端")
            config = read_object(config_path, 65536, missing_ok=True)
            name = config.get("Profile") if config is not None else None
            result.update(available=True, status="ready", configured_profile_name=name,
                          configured_profile_id=self._profile_id(name), can_start=True,
                          reason="首次启动时自动生成游戏配置。" if config is None else "")
            running = game_processes(executable)
            if running:
                process = running[0]
                profile_id, name = self._running_profile(process) if len(running) == 1 and process.path else (None, None)
                result.update(running=True, can_start=False, status="running", pid=process.pid,
                              profile_id=profile_id, profile_name=name, active_profile_id=profile_id, active_profile_name=name,
                              reason="游戏已运行；切换游戏存档前请先退出游戏。")
                if len(running) > 1 or process.path is None:
                    result.update(status="process_unknown", reason="发现无法确认归属的同名游戏进程，请先退出该进程或使用相同权限运行启动器。")
        except (OSError, ValueError, GameplayError) as error:
            result.update(available=False, can_start=False, status="unavailable", reason=str(error))
        return result

    def _verify_client(self, release):
        module_path = self.project_root / "pc" / "prepare_client.py"
        spec = importlib.util.spec_from_file_location("dotabyss_launcher_verifier", module_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module.verify_client(self.client_root, release)

    def _preflight(self, release):
        if self.local is None:
            raise GameplayError("本地游戏服务尚未配置主数据", 503, 5038)
        master = self.local._active()
        if master["client"]["package"] != "dotabyss_x_cl" or master["client"]["version"] != release["application_version"]:
            raise GameplayError("本地客户端与服务端版本不一致", 409, 4098)
        catalog = AssetCatalog(self.store.path.parent, self.store)
        assets = catalog.status()
        if not assets.get("active") or assets.get("error"):
            raise GameplayError("本地游戏资源未就绪：" + assets.get("error", "缺少资源清单"), 503, 5038)
        if assets["active"].get("catalog_served") is not True:
            raise GameplayError("本地游戏资源未就绪：缺少 PC catalog 请求路径映射，请重新导入资源。", 503, 5038)
        try:
            active_assets = catalog.active()
        except AssetDataError as error:
            raise GameplayError("本地游戏资源未就绪：" + str(error), 503, 5038) from error
        if (not active_assets or active_assets.get("prefix") != PC_CATALOG_PREFIX
                or active_assets.get("catalog", {}).get("relative_path") != PC_CATALOG_PATH
                or active_assets.get("catalog_hash", {}).get("relative_path") != PC_CATALOG_HASH_PATH):
            raise GameplayError("本地游戏资源未就绪：PC catalog 请求路径映射不匹配，请重新导入资源。", 503, 5038)
        manifest = self._verify_client(release)
        plugin = self.client_root / "BepInEx" / "plugins" / "DotAbyss.Local.Pc" / "DotAbyss.Local.Pc.dll"
        if not plugin.is_file():
            raise GameplayError("缺少私服客户端插件", 503, 5038)
        return manifest

    @staticmethod
    def _origin(value):
        if not isinstance(value, str):
            raise GameplayError("本地服务地址无效", 400, 4007)
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as error:
            raise GameplayError("本地服务地址无效", 400, 4007) from error
        if (parsed.scheme != "http" or parsed.hostname not in ("127.0.0.1", "localhost") or port is None or not 1024 <= port <= 65535
                or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment):
            raise GameplayError("游戏只连接本机服务端口", 400, 4007)
        return f"http://127.0.0.1:{port}/"

    def start(self, profile_id, origin="http://127.0.0.1:18083"):
        normalized = self._origin(origin)
        try:
            with exclusive_process_lock(self.client_root / ".local-launcher.lock"):
                return self._start(profile_id, normalized)
        except ProcessLockBusy as error:
            raise GameplayError("另一个启动请求正在处理，请稍后查看游戏状态", 409, 4098) from error

    def _start(self, profile_id, origin):
        profile = self._profile(profile_id)
        current = self.status()
        if current["running"]:
            if current["profile_id"] == profile_id:
                return {**current, "status": "already_running", "launched": False, "already_running": True}
            raise GameplayError("游戏正在使用另一份存档或尚未完成连接；请先退出游戏再启动所选存档", 409, 4098)
        if not current["available"]:
            raise GameplayError(current["reason"], 503, 5038)
        release, executable, config_path = self._paths()
        manifest = self._preflight(release)
        if game_processes(executable):
            raise GameplayError("检查期间已有游戏启动，请刷新状态后再试", 409, 4098)
        config = read_object(config_path, 65536, missing_ok=True)
        original = config_path.read_bytes() if config is not None else None
        if config is None:
            config = {"ExpectedCompany": release["identity"]["company"], "ExpectedProduct": release["identity"]["product"],
                      "ExpectedPersistentPath": manifest["profile"], "TimeoutSeconds": 20}
        if (config.get("ExpectedCompany") != "LocalGames" or config.get("ExpectedProduct") != "DotAbyssLocalPlayer"
                or config.get("ExpectedPersistentPath") != manifest["profile"]):
            raise GameplayError("客户端隔离身份配置不匹配", 409, 4098)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup = None
        if original is not None:
            backup = self.project_root / "artifacts" / "launcher" / ("pc-local-" + stamp + ".json")
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_bytes(original)
        log_path = self.project_root / "artifacts" / "pc-run" / ("Player-" + stamp + ".log")
        log_path.parent.mkdir(parents=True, exist_ok=True)
        config.update(Profile=profile["name"], ServiceOrigin=origin)
        replacement = (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        process = None
        try:
            atomic_bytes(config_path, replacement)
            environment = dict(os.environ)
            environment["NO_PROXY"] = "127.0.0.1,localhost,::1"
            process = subprocess.Popen([str(executable), "-logFile", str(log_path), "-screen-fullscreen", "0"],
                                       cwd=self.client_root, env=environment, close_fds=True)
            try:
                code = process.wait(timeout=0.35)
            except subprocess.TimeoutExpired:
                code = None
            if code is not None:
                raise OSError("游戏进程在初始化前退出，代码 " + str(code))
            found = next((item for item in game_processes(executable) if item.pid == process.pid), None)
            receipt = {"pid": process.pid, "profile_id": profile_id, "profile_name": profile["name"], "launched_at": time.time(),
                       "process_started_at": found.started_at if found else None, "log_path": str(log_path),
                       "config_backup": str(backup) if backup else None}
            atomic_bytes(self.receipt_path, json.dumps(receipt, ensure_ascii=False).encode("utf-8"))
        except (OSError, ValueError) as error:
            if process is None or process.poll() is not None:
                if config_path.is_file() and not config_path.is_symlink() and config_path.read_bytes() == replacement:
                    if original is None:
                        config_path.unlink()
                    else:
                        atomic_bytes(config_path, original)
            raise GameplayError("游戏启动失败：" + str(error), 503, 5038) from error
        return {"available": True, "running": True, "status": "started", "launched": True, "already_running": False,
                "pid": process.pid, "profile_id": profile_id, "profile_name": profile["name"], "active_profile_id": profile_id,
                "active_profile_name": profile["name"], "configured_profile_id": profile_id, "configured_profile_name": profile["name"],
                "can_start": False, "reason": "游戏已启动，正在连接所选存档。", "log_path": str(log_path)}
