from __future__ import annotations

import os
import secrets
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlsplit

from dotabyss_ps.process_lock import ProcessLockBusy, exclusive_process_lock
from .client import ConsoleError, read_token
from .service_bootstrap import canonical_origin, compatible_client, health


def child_options(root):
    environment = dict(os.environ)
    environment.update(PYTHONPATH=os.pathsep.join((str(root), str(root / "server"))),
                       PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8", NO_PROXY="127.0.0.1,localhost,::1")
    flags = (getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0
    return {"cwd": root, "env": environment, "creationflags": flags, "close_fds": True}


class ServiceManager:
    def __init__(self, origin, data_dir, *, project_root=None, start_timeout=30, stop_timeout=20):
        self.origin = canonical_origin(origin)
        self.data_dir = Path(data_dir).resolve()
        self.root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
        self.start_timeout, self.stop_timeout = start_timeout, stop_timeout
        self.process = None
        self.instance = None
        self.log_path = None
        self.phase = ""
        self.last_error = ""
        self.stop_requested = False
        self._operation = threading.Lock()

    def status(self):
        process = self.process
        live = process is not None and process.poll() is None
        result = {"state": "stopped", "ready": False, "managed": live, "can_start": not live,
                  "can_stop": live, "can_restart": live, "origin": self.origin, "data_dir": str(self.data_dir),
                  "pid": process.pid if live else None, "log_path": str(self.log_path) if self.log_path else None,
                  "message": "服务已停止，点击启动服务后进入工作台。", "instance": self.instance if live else None}
        if self.phase or live and self.stop_requested:
            result.update(state=self.phase or "stopping", can_start=False, can_stop=False, can_restart=False,
                          message="正在启动服务…" if self.phase == "starting" else self.last_error or "正在结束请求并停止服务…")
            return result
        try:
            current = health(self.origin)
            if current is not None:
                compatible_client(self.origin, self.data_dir)
                owned = live and current.get("instance") == self.instance
                result.update(state="running" if owned else "external", ready=True,
                              managed=bool(owned), can_start=False, can_stop=bool(owned), can_restart=bool(owned),
                              pid=process.pid if owned else None, instance=current.get("instance") or "external",
                              message="服务运行中，工作台与游戏可以连接。" if owned else
                              "服务从其他窗口启动。请先在原 PowerShell 窗口按 Ctrl+C，再从这里启动，即可管理启停。")
                return result
        except ConsoleError as error:
            result.update(state="conflict", can_start=False, can_restart=False, message=str(error))
            return result
        if live:
            result.update(state="error", can_restart=False, message=self.last_error or "服务进程仍在运行，但尚未就绪。请查看日志。")
        elif not (self.stop_requested and process is not None and process.returncode == 0) and (
                self.last_error or process is not None and process.returncode not in (None, 0)):
            result.update(state="error", message=self.last_error or f"服务意外退出（{process.returncode}），请查看日志。")
        return result

    def _validate(self):
        python = self.root / ".venv" / "Scripts" / "python.exe"
        schema = self.root / "artifacts" / "master-schema-pc-1.15.0.json"
        if not python.is_file() or not schema.is_file() or not (self.data_dir / "local.sqlite3").is_file():
            raise ConsoleError("缺少服务环境、主数据结构或存档数据库，请检查项目目录。", 409)
        return python, schema

    def _start(self):
        current = self.status()
        if current["ready"]:
            return current
        if not current["can_start"]:
            raise ConsoleError(current["message"], 409)
        python, schema = self._validate()
        port = urlsplit(self.origin).port
        try:
            with exclusive_process_lock(self.root / "artifacts" / "launcher" / f"service-{port}.lock"):
                if health(self.origin) is not None:
                    return self.status()
                self.phase, self.last_error = "starting", ""
                self.instance, self.stop_requested = secrets.token_hex(16), False
                self.log_path = self.root / "artifacts" / "launcher" / f"server-{port}-{datetime.now():%Y%m%d-%H%M%S-%f}.log"
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                command = [str(python), "-u", "-m", "console.service_worker", "--origin", self.origin,
                           "--data-dir", str(self.data_dir), "--schema", str(schema), "--instance", self.instance]
                if self.process is not None and self.process.stdin is not None:
                    self.process.stdin.close()
                with self.log_path.open("ab", buffering=0) as log:
                    self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=log, stderr=log,
                                                    **child_options(self.root))
                deadline = time.monotonic() + self.start_timeout
                while time.monotonic() < deadline:
                    if self.process.poll() is not None:
                        raise ConsoleError("服务启动失败，请查看下方日志。", 503)
                    current = health(self.origin)
                    if current is not None:
                        if current.get("instance") != self.instance:
                            self._signal_stop()
                            raise ConsoleError("端口已被另一个服务占用，本次服务已请求退出。", 409)
                        compatible_client(self.origin, self.data_dir)
                        return None
                    time.sleep(0.15)
                raise ConsoleError("等待服务就绪超时，请查看日志；可以停止此进程后重试。", 504)
        except ProcessLockBusy as error:
            raise ConsoleError("另一个窗口正在启动此服务，请稍后刷新。", 409) from error
        except OSError as error:
            raise ConsoleError("无法启动服务：" + str(error), 503) from error
        finally:
            self.phase = ""

    def _signal_stop(self):
        if not self.stop_requested and self.process and self.process.poll() is None:
            self.stop_requested = True
            try:
                self.process.stdin.write(b"stop\n")
                self.process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass

    def _stop(self):
        process = self.process
        if process is None or process.poll() is not None:
            if process is not None and process.stdin is not None:
                process.stdin.close()
            self.process, self.instance, self.stop_requested, self.last_error = None, None, False, ""
            if self.status()["ready"]:
                raise ConsoleError("此服务不是本启动器启动的，请先在原 PowerShell 窗口按 Ctrl+C。", 409)
            return
        self.phase = "stopping"
        try:
            self._signal_stop()
            process.wait(timeout=self.stop_timeout)
            process.stdin.close()
            self.process, self.instance, self.last_error, self.stop_requested = None, None, "", False
        except subprocess.TimeoutExpired as error:
            raise ConsoleError("服务仍在结束请求，尚未停止；未强制终止进程，请稍后刷新。", 504) from error
        except OSError as error:
            raise ConsoleError("停止服务时发生错误：" + str(error), 503) from error
        finally:
            self.phase = ""

    def _run(self, action):
        if not self._operation.acquire(blocking=False):
            raise ConsoleError("服务操作正在进行，请稍候。", 409)
        try:
            action()
            return self.status()
        except ConsoleError as error:
            self.last_error = str(error)
            raise
        finally:
            self._operation.release()

    def start(self):
        return self._run(self._start)

    def stop(self):
        return self._run(self._stop)

    def restart(self):
        def restart_owned():
            if self.process is None or self.process.poll() is not None:
                raise ConsoleError("请先由本启动器启动服务，再使用重启。", 409)
            self._stop()
            self._start()
        return self._run(restart_owned)

    def logs(self):
        if self.log_path is None:
            return {"text": "暂无本启动器的服务日志。外部启动的服务请查看原 PowerShell 窗口。", "path": None}
        try:
            with self.log_path.open("rb") as stream:
                stream.seek(max(0, self.log_path.stat().st_size - 65536))
                text = stream.read(65536).decode("utf-8", errors="replace")
            try:
                text = text.replace(read_token(self.data_dir), "[凭据]")
            except ConsoleError:
                pass
            return {"text": text or "日志尚无输出。", "path": str(self.log_path)}
        except OSError as error:
            raise ConsoleError("无法读取服务日志：" + str(error), 503) from error

    def console_ticket(self):
        if not self.status()["ready"]:
            raise ConsoleError("请先启动服务。", 409)
        result = compatible_client(self.origin, self.data_dir).post("/admin/ui-ticket", {})
        ticket = result.get("ticket")
        if not isinstance(ticket, str) or not 16 <= len(ticket) <= 128:
            raise ConsoleError("服务没有返回有效的工作台票据。", 503)
        return {"url": self.origin + "/console/#ticket=" + quote(ticket, safe="")}

    def close(self):
        if self.process and self.process.poll() is None:
            try:
                self._run(self._stop)
            except ConsoleError:
                self.process.stdin.close()
