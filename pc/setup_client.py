from __future__ import annotations

import copy
import hashlib
import re
import subprocess
from datetime import datetime
from pathlib import Path

from dotabyss_ps.admin_launcher import atomic_bytes, game_processes, read_object
from dotabyss_ps.process_lock import exclusive_process_lock


MAX_CACHE_FILES = 4096
PLUGIN_NAME = "DotAbyss.Local.Pc.dll"


def _inside(root: Path, relative: str | Path) -> Path:
    relative = Path(relative)
    if relative.is_absolute() or relative.drive or ".." in relative.parts or not relative.parts:
        raise ValueError("Expected a relative path inside the selected directory")
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root):
        raise ValueError("Path leaves the selected directory or refers to a symbolic link: " + str(relative))
    return path


def _file(root: Path, relative: str | Path) -> Path:
    path = _inside(root, relative)
    if not path.is_file():
        raise ValueError("Required regular file is missing: " + str(path))
    return path


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def resolve_seeds(release: dict, cache_root: Path) -> dict:
    cache_root = Path(cache_root).resolve()
    if not cache_root.is_dir():
        raise ValueError("Game cache directory is missing: " + str(cache_root))
    result = copy.deepcopy(release)
    for seed in result["profile_seeds"]:
        expected = seed["sha256"]
        if not isinstance(expected, str) or re.fullmatch(r"[0-9a-f]{64}", expected) is None:
            raise ValueError("Invalid resource seed SHA-256")
        preferred = _inside(cache_root, seed["relative"])
        if preferred.exists() and not preferred.is_file():
            raise ValueError("Resource seed must be a regular file: " + str(preferred))
        if preferred.is_file() and _digest(preferred) == expected:
            seed["source"] = str(preferred)
            continue
        candidates = []
        if preferred.parent.is_dir():
            for index, path in enumerate(preferred.parent.iterdir()):
                if index >= MAX_CACHE_FILES:
                    raise ValueError("Resource seed directory contains too many files: " + str(preferred.parent))
                if path.suffix.casefold() == preferred.suffix.casefold():
                    candidate = _inside(cache_root, path.relative_to(cache_root))
                    if candidate.is_file():
                        candidates.append(candidate)
        match = next((path for path in sorted(candidates) if _digest(path) == expected), None)
        if match is None:
            raise ValueError("Required resource seed is missing or belongs to another version: " + seed["relative"])
        seed["source"] = str(match)
    return result


def _require_idle(executable: Path) -> None:
    if game_processes(executable):
        raise ValueError("Exit the local game before building or installing its plugin")


def install_plugin(project_root: Path, client_root: Path) -> dict:
    project_root, client_root = Path(project_root).resolve(), Path(client_root).resolve()
    project = _file(project_root, "pc/DotAbyss.Local.Pc.csproj")
    manifest = read_object(_file(client_root, "local-client.json"))
    if manifest.get("identity") != {"company": "LocalGames", "product": "DotAbyssLocalPlayer"}:
        raise ValueError("The client has not been prepared with the local game identity")
    name = manifest.get("executable")
    if not isinstance(name, str) or Path(name).name != name or not name.lower().endswith(".exe"):
        raise ValueError("Invalid prepared game executable")
    executable = _file(client_root, name)
    if not _inside(client_root, "BepInEx/core").is_dir():
        raise ValueError("Install the matching BepInEx IL2CPP x64 loader before building the plugin")
    _file(client_root, "BepInEx/interop/Project.dll")
    target = _inside(client_root, "BepInEx/plugins/DotAbyss.Local.Pc/" + PLUGIN_NAME)
    if target.exists() and not target.is_file():
        raise ValueError("Plugin destination must be a regular file")
    with exclusive_process_lock(client_root / ".local-launcher.lock"):
        _require_idle(executable)
        result = subprocess.run(["dotnet", "build", str(project), "-c", "Release", "--nologo", "-v:minimal",
                                 "-p:ClientDir=" + str(client_root)], cwd=project_root, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=600)
        if result.returncode:
            raise ValueError("Client plugin build failed:\n" + (result.stdout + result.stderr)[-16384:])
        output = _file(project_root, "pc/bin/Release/net6.0/" + PLUGIN_NAME)
        payload = output.read_bytes()
        if not payload:
            raise ValueError("Client plugin build produced an empty assembly")
        _require_idle(executable)
        target = _inside(client_root, "BepInEx/plugins/DotAbyss.Local.Pc/" + PLUGIN_NAME)
        original = target.read_bytes() if target.exists() else None
        checksum = hashlib.sha256(payload).hexdigest()
        if original == payload:
            return {"plugin": str(target), "sha256": checksum, "backup": None, "changed": False}
        backup = None
        if original is not None:
            relative = "artifacts/setup/plugin-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + "/" + PLUGIN_NAME
            backup = _inside(project_root, relative)
            atomic_bytes(backup, original)
        atomic_bytes(target, payload)
        return {"plugin": str(target), "sha256": checksum, "backup": str(backup) if backup else None, "changed": True}
