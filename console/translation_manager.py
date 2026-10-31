from __future__ import annotations

import configparser
import json
import re
import secrets
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path

from dotabyss_ps.admin_launcher import game_processes
from dotabyss_ps.process_lock import ProcessLockBusy, exclusive_process_lock
from .client import ConsoleError
from .service_manager import child_options

PHASE_MESSAGES = {
    "preparing": "正在准备翻译任务…", "preflight": "正在检查游戏与翻译环境…",
    "sources": "正在获取繁体译文…", "plugin": "正在校验翻译插件与兼容补丁…",
    "dependencies": "正在校验字体和插件依赖…", "validate": "正在校验翻译字典…",
    "build": "正在构建翻译插件…", "stage": "正在准备本地翻译缓存…",
    "install": "正在备份并安装翻译…", "complete": "繁体翻译已安装，下次启动游戏生效。",
}
BUSY_MESSAGE = "客户端正在启动或更新，请稍候。"


def read_object(path, limit=4 * 1024 * 1024):
    if not path.is_file() or path.is_symlink() or path.stat().st_size > limit:
        raise ValueError("Invalid translation metadata")
    result = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(result, dict):
        raise ValueError("Invalid translation metadata")
    return result


class TranslationManager:
    def __init__(self, *, project_root=None):
        self.root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
        self.client = self.root.parent / "dotabyss_pc" / "local-client"
        self.target = self.client / "BepInEx" / "plugins" / "AbyssMod"
        self.jobs = self.root / "artifacts" / "translation" / "jobs"
        self.process = None
        self.job = None
        self._lock = threading.RLock()
        try:
            saved = read_object(self.jobs / "latest.json", 16384)
            if re.fullmatch(r"[a-f0-9]{32}", saved.get("job_id", "")):
                self.job = saved
        except (OSError, ValueError, TypeError):
            pass

    def _save(self):
        self.jobs.mkdir(parents=True, exist_ok=True)
        target = self.jobs / "latest.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.job, ensure_ascii=False), encoding="utf-8")
        temporary.replace(target)

    def _log_path(self):
        return self.jobs / (self.job["job_id"] + ".log") if self.job else None

    def _progress(self):
        text = self.logs()["text"]
        for line in reversed(text.splitlines()):
            if not line.startswith("@@translation "):
                continue
            try:
                value = json.loads(line[len("@@translation "):])
                if (isinstance(value, dict) and isinstance(value.get("phase"), str)
                        and isinstance(value.get("message"), str) and type(value.get("percent")) is int):
                    return {"phase": value["phase"][:64], "message": PHASE_MESSAGES.get(value["phase"], value["message"][:1000]),
                            "percent": max(0, min(100, value["percent"]))}
            except (ValueError, TypeError):
                continue
        return {"phase": "preparing", "message": "正在准备翻译任务…", "percent": 0}

    def _installed(self):
        result = {"installed": False, "enabled": False, "language": "zh_Hant", "plugin_version": None,
                  "translation_revision": None}
        try:
            report = read_object(self.target / "integration-manifest.json")
            required = ("AbyssMod.dll", "Utility.dll", "fonts/ttcuyuanj", "cache/translations/manifest/zh_Hant.json")
            result["installed"] = report.get("language") == "zh_Hant" and all((self.target / name).is_file() for name in required)
            revision = report.get("translation_commit", "")
            if isinstance(revision, str) and re.fullmatch(r"[a-f0-9]{40}", revision):
                result["translation_revision"] = revision
            result["plugin_version"] = "1.2.0" if result["installed"] else None
            config = configparser.ConfigParser(interpolation=None, strict=False)
            config.read(self.client / "BepInEx" / "config" / "AbyssMod.cfg", encoding="utf-8-sig")
            result["enabled"] = (result["installed"] and config.getboolean("Translation", "Enabled", fallback=False)
                                 and config.get("Translation", "Language", fallback="") == "zh_Hant")
        except (OSError, ValueError, TypeError, configparser.Error):
            pass
        return result

    def _availability(self):
        try:
            manifest = read_object(self.client / "local-client.json")
            if manifest.get("identity") != {"company": "LocalGames", "product": "DotAbyssLocalPlayer"}:
                return False, "请先准备本地客户端副本。"
            name = manifest.get("executable")
            if not isinstance(name, str) or Path(name).name != name or not name.lower().endswith(".exe"):
                return False, "本地客户端程序配置无效。"
            executable = self.client / name
            if not executable.is_file():
                return False, "缺少本地游戏程序，请先准备客户端。"
            if game_processes(executable):
                return True, "请先退出游戏，再安装或更新翻译。"
        except (OSError, ValueError, TypeError):
            return False, "无法确认本地客户端状态，请先准备客户端并检查运行权限。"
        python = self.root / ".venv" / "Scripts" / "python.exe"
        if not python.is_file() or not (self.root / "pc-translation" / "prepare_translation.py").is_file():
            return False, "缺少本地翻译准备环境。"
        if not shutil.which("git") or not shutil.which("dotnet"):
            return False, "首次安装或更新翻译需要 Git 和 .NET SDK。"
        try:
            with exclusive_process_lock(self.client / ".local-launcher.lock"):
                pass
        except ProcessLockBusy:
            return False, BUSY_MESSAGE
        except OSError:
            return False, "无法访问客户端更新锁，请检查目录权限。"
        return False, ""

    def status(self):
        with self._lock:
            installed = self._installed()
            game_running, reason = self._availability()
            result = {**installed, "state": "blocked" if reason else "ready", "can_start": not reason,
                      "game_running": game_running, "reason": reason, "phase": "idle", "percent": 0,
                      "job_id": self.job["job_id"] if self.job else None, "restart_required": False,
                      "message": reason or ("繁体翻译已安装，可一键更新译文。" if installed["enabled"] else "安装繁体字典、字体和翻译插件。")}
            if self.job:
                progress = self._progress()
                live = self.process is not None and self.process.poll() is None
                terminal = progress["phase"] in ("complete", "failed")
                if live:
                    result.update(progress, state="running", can_start=False, reason="")
                elif terminal:
                    succeeded = progress["phase"] == "complete" and installed["enabled"]
                    if self.process is not None and self.process.returncode != 0:
                        succeeded = False
                    result.update(progress, state="succeeded" if succeeded else "failed",
                                  restart_required=succeeded, message=progress["message"] if succeeded else
                                  progress["message"] if progress["phase"] == "failed" else "翻译安装未通过校验，请查看日志。")
                elif self.process is not None:
                    result.update(state="failed", phase="failed", message="翻译任务未完成，请查看日志后重试。")
                elif self.job.get("state") == "running" and reason == BUSY_MESSAGE:
                    result.update(progress, state="running", can_start=False, reason="")
                elif self.job.get("state") == "succeeded" and installed["enabled"]:
                    result.update(state="succeeded", phase="complete", percent=100,
                                  restart_required=not game_running, message=PHASE_MESSAGES["complete"])
                else:
                    result.update(state="failed", phase="failed", message=self.job.get("message") or
                                  "上次翻译任务尚无完成记录，请查看日志后重试。")
                if not live and result["state"] in ("succeeded", "failed"):
                    summary = {key: result[key] for key in ("state", "phase", "percent", "message")}
                    if any(self.job.get(key) != value for key, value in summary.items()):
                        self.job.update(summary)
                        self._save()
            return result

    def install(self):
        with self._lock:
            current = self.status()
            if current["state"] == "running":
                return current
            if not current["can_start"]:
                raise ConsoleError(current["reason"] or current["message"], 409)
            self.jobs.mkdir(parents=True, exist_ok=True)
            self.job = {"job_id": secrets.token_hex(16), "state": "running",
                        "started_at": datetime.now(timezone.utc).isoformat()}
            self._save()
            command = [str(self.root / ".venv" / "Scripts" / "python.exe"), "-u",
                       str(self.root / "pc-translation" / "prepare_translation.py"),
                       "--client", str(self.client), "--bootstrap", "--fetch", "--apply"]
            try:
                with self._log_path().open("wb", buffering=0) as log:
                    self.process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                                    **child_options(self.root))
            except OSError as error:
                self.process = None
                self.job.update(state="failed", phase="failed", message="无法启动翻译任务：" + str(error))
                self._save()
                raise ConsoleError("无法启动翻译任务：" + str(error), 503) from error
            return self.status()

    def logs(self):
        path = self._log_path()
        if path is None or not path.is_file():
            return {"text": "暂无翻译任务日志。", "path": None}
        try:
            with path.open("rb") as stream:
                stream.seek(max(0, path.stat().st_size - 65536))
                raw = stream.read(65536)
            text = raw.decode("utf-8", errors="replace")
            text = re.sub(r"https://[^\s/@]+:[^\s/@]+@", "https://[凭据]@", text)
            return {"text": text, "path": str(path)}
        except OSError as error:
            raise ConsoleError("无法读取翻译任务日志。", 503) from error
