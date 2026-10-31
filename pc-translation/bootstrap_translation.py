from __future__ import annotations

import hashlib
import os
import shutil
import stat
import subprocess
import tempfile
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

PLUGIN_URL = "https://github.com/s88037zz/AbyssMod.git"
TRANSLATION_URL = "https://github.com/s88037zz/dotabyss-translation.git"
PLUGIN_COMMIT = "88357b9757414cc2a07913be90a6f7b2c3f28d08"
RELEASE_URL = "https://github.com/s88037zz/AbyssMod/releases/download/v1.2.0/AbyssMod-v1.2.0.zip"
RELEASE_SHA256 = "ade341d959e06eb72db899d2f79ace967b211a9b4a03108d2a0cfffa37a80e2d"
RELEASE_SIZE = 56795892
DEPENDENCIES = {
    "Utility.dll": ("BepInEx/plugins/AbyssMod/Utility.dll", "5a89ee076f09aa5e5888ce0fd0caa867c68cce9be70d839134d0983ff3267fb1"),
    "ttcuyuanj": ("BepInEx/plugins/AbyssMod/fonts/ttcuyuanj", "24564ecd227b6d1d300e7e2b0ff7984729fd984e323cf69a60cb6e3d51ba5820"),
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def git(directory, *arguments):
    environment = dict(os.environ, GIT_TERMINAL_PROMPT="0")
    command = ["git", "-C", str(directory), *arguments] if directory else ["git", *arguments]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace",
                            env=environment, timeout=180)
    if result.returncode:
        raise ValueError(f"Git {arguments[0]} failed (exit {result.returncode}); check the repository and network connection")
    return result.stdout.strip()


def verified_repository(directory, expected_url):
    if directory.is_symlink() or not (directory / ".git").exists():
        raise ValueError("Translation dependency path is not a regular Git checkout: " + str(directory))
    if Path(git(directory, "rev-parse", "--show-toplevel")).resolve() != directory.resolve():
        raise ValueError("Translation dependency is not its own Git checkout")
    actual = git(directory, "remote", "get-url", "origin")
    if actual.removesuffix(".git") != expected_url.removesuffix(".git"):
        raise ValueError("Translation dependency origin does not match the expected GitHub repository")


def normalized_patch(value):
    lines = value.replace("\r\n", "\n").splitlines()
    return "\n".join(line for line in lines if not line.startswith("index ")).strip()


def ensure_plugin(directory, patch):
    if not directory.exists():
        directory.parent.mkdir(parents=True, exist_ok=True)
        git(None, "clone", "--no-checkout", PLUGIN_URL, str(directory))
        git(directory, "checkout", "--detach", PLUGIN_COMMIT)
    verified_repository(directory, PLUGIN_URL)
    if git(directory, "rev-parse", "HEAD") != PLUGIN_COMMIT:
        if git(directory, "status", "--porcelain", "--untracked-files=all"):
            raise ValueError("AbyssMod has local changes at another revision; preserve them before installing")
        git(directory, "fetch", "origin", PLUGIN_COMMIT)
        git(directory, "checkout", "--detach", PLUGIN_COMMIT)
    status = git(directory, "status", "--porcelain", "--untracked-files=all")
    actual = git(directory, "diff", "--no-ext-diff", "--binary", "HEAD")
    expected = patch.read_text(encoding="utf-8-sig")
    if status:
        if any(line.startswith("?? ") for line in status.splitlines()) or normalized_patch(actual) != normalized_patch(expected):
            raise ValueError("AbyssMod contains local changes beyond the required input compatibility patch")
        git(directory, "apply", "--reverse", "--check", str(patch.resolve()))
    else:
        git(directory, "apply", "--check", str(patch.resolve()))
        git(directory, "apply", str(patch.resolve()))
    if normalized_patch(git(directory, "diff", "--no-ext-diff", "--binary", "HEAD")) != normalized_patch(expected):
        raise ValueError("AbyssMod compatibility patch verification failed")


def ensure_translations(directory, fetch):
    if not directory.exists():
        directory.parent.mkdir(parents=True, exist_ok=True)
        git(None, "clone", "--branch", "main", "--single-branch", TRANSLATION_URL, str(directory))
    verified_repository(directory, TRANSLATION_URL)
    if not fetch:
        return
    if git(directory, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("The translation repository has local changes; preserve them before updating")
    if git(directory, "branch", "--show-current") != "main":
        raise ValueError("The translation repository must be on its main branch before updating")
    git(directory, "fetch", "origin", "main")
    git(directory, "merge", "--ff-only", "origin/main")


class ReleaseRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        parsed = urlsplit(newurl)
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or not (parsed.hostname == "github.com" or (parsed.hostname or "").endswith(".githubusercontent.com"))):
            raise ValueError("Unexpected translation release redirect")
        return super().redirect_request(request, fp, code, message, headers, newurl)


def download_release(target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix="release-", suffix=".partial", delete=False) as stream:
            temporary = Path(stream.name)
            request = Request(RELEASE_URL, headers={"User-Agent": "dot-abyss-ps-translation"})
            with build_opener(ReleaseRedirect()).open(request, timeout=30) as response:
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > RELEASE_SIZE:
                        raise ValueError("Translation release exceeds its verified size")
                    stream.write(chunk)
        if temporary.stat().st_size != RELEASE_SIZE or digest(temporary) != RELEASE_SHA256:
            raise ValueError("Translation release fingerprint does not match the pinned release")
        temporary.replace(target)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def ensure_dependencies(artifacts):
    folder = artifacts / "dependencies"
    if folder.is_symlink() or not folder.resolve().is_relative_to(artifacts.resolve()):
        raise ValueError("Translation dependency cache leaves its artifact directory")
    if all((folder / name).is_file() and digest(folder / name) == expected for name, (_, expected) in DEPENDENCIES.items()):
        return
    archive = artifacts / "AbyssMod-v1.2.0.zip"
    if not archive.is_file() or archive.stat().st_size != RELEASE_SIZE or digest(archive) != RELEASE_SHA256:
        download_release(archive)
    payloads = {}
    with zipfile.ZipFile(archive) as source:
        for name, (member, expected) in DEPENDENCIES.items():
            entries = [entry for entry in source.infolist() if entry.filename == member]
            if len(entries) != 1 or entries[0].is_dir() or stat.S_ISLNK(entries[0].external_attr >> 16):
                raise ValueError("Pinned translation dependency is missing or duplicated: " + name)
            if entries[0].file_size > 32 * 1024 * 1024:
                raise ValueError("Pinned translation dependency is too large: " + name)
            payload = source.read(entries[0])
            if hashlib.sha256(payload).hexdigest() != expected:
                raise ValueError("Translation dependency fingerprint mismatch: " + name)
            payloads[name] = payload
    folder.mkdir(parents=True, exist_ok=True)
    for name, payload in payloads.items():
        path = folder / name
        if path.is_symlink() or path.resolve().parent != folder.resolve():
            raise ValueError("Translation dependency path is linked outside its cache")
        temporary = path.with_suffix(path.suffix + ".partial")
        temporary.write_bytes(payload)
        temporary.replace(path)


def bootstrap(source, plugin_source, artifacts, patch, *, fetch=False, progress=lambda *args: None):
    if shutil.which("git") is None:
        raise ValueError("Git is required to prepare the translation sources")
    progress("sources", "Preparing translation dictionaries", 10)
    ensure_translations(source, fetch)
    progress("plugin", "Verifying the pinned translation plugin and compatibility patch", 25)
    ensure_plugin(plugin_source, patch)
    progress("dependencies", "Verifying the translation font and plugin dependency", 40)
    ensure_dependencies(artifacts)
