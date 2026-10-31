from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
ARTIFACTS = ROOT / "artifacts" / "translation"
LANGUAGE = "zh_Hant"
DEPENDENCIES = {
    "Utility.dll": "5a89ee076f09aa5e5888ce0fd0caa867c68cce9be70d839134d0983ff3267fb1",
    "ttcuyuanj": "24564ecd227b6d1d300e7e2b0ff7984729fd984e323cf69a60cb6e3d51ba5820",
}
CONFIG = """[General]
DynamicMosaic = true
SoundCaution = true
VoiceInterruption = true
TitleMovie = true

[Translation]
Enabled = true
CDN =
Language = zh_Hant

[Translation.Font]
AssetBundlePath = AbyssMod/fonts/ttcuyuanj

[Collector]
CollectText = false
ClassifyText = false

[MachineTranslation]
Enabled = false
ApiKey =
"""


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    def pairs(items):
        value = {}
        for key, entry in items:
            if key in value:
                raise ValueError(f"Duplicate JSON key in {path.name}")
            value[key] = entry
        return value
    return json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=pairs)


def checked(path, root):
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()) or resolved == root.resolve():
        raise ValueError("Translation path leaves its expected directory")
    return resolved


def revision(path):
    result = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True)
    return result.stdout.strip()


def dictionary_hash(data):
    text = "".join(key + "\0" + data[key] + "\0" for key in sorted(data, key=lambda value: value.encode("utf-16-be")))
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def rebuild_manifest(cache):
    manifest = {"novels": {}, "add_on": {}, "other": {}}
    for path in sorted(cache.rglob(f"{LANGUAGE}.json")):
        parts = path.relative_to(cache).parts
        if parts[0] in ("manifest", "legacy"):
            continue
        file_hash = dictionary_hash(read_json(path))
        if parts[0] in ("novels", "add-on", "other"):
            manifest["add_on" if parts[0] == "add-on" else parts[0]][parts[1]] = file_hash
        elif len(parts) == 2:
            manifest[parts[0]] = file_hash
    manifest["hash"] = hashlib.md5(json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    target = cache / "manifest" / f"{LANGUAGE}.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")


def progress(phase, message, percent):
    print("@@translation " + json.dumps({"phase": phase, "message": message, "percent": percent}, ensure_ascii=False), flush=True)


def prepare(source, plugin_source, client=None, *, notify=progress):
    from translation_overlays import extend_mapping, merge_overrides

    integration = ROOT / "pc-translation"
    notify("validate", "Checking translation dictionaries", 50)
    tree = source / "translations"
    manifest = read_json(tree / "manifest" / f"{LANGUAGE}.json")
    if not isinstance(manifest, dict):
        raise ValueError("Translation manifest must be an object")
    inputs = sorted(tree.rglob(f"{LANGUAGE}.json"))
    counts = {"files": 0, "entries": 0, "novels": 0, "master_files": 0}
    for path in inputs:
        checked(path, tree)
        value = read_json(path)
        if path.parent.name != "manifest":
            if not isinstance(value, dict) or any(not isinstance(key, str) or not isinstance(text, str) for key, text in value.items()):
                raise ValueError(f"Translation must be a string dictionary: {path.relative_to(tree)}")
            counts["entries"] += len(value)
        counts["files"] += 1
        relative = path.relative_to(tree)
        counts["novels"] += relative.parts[0] == "novels"
        counts["master_files"] += relative.parts[0].startswith("m_")
    for name, expected in DEPENDENCIES.items():
        if digest(ARTIFACTS / "dependencies" / name) != expected:
            raise ValueError("Translation dependency fingerprint changed: " + name)
    mapping = read_json(plugin_source / "AbyssMod" / "AbyssMod.master_mapping.json")
    mapping["tables"].pop("MNgWords", None)
    mapping["dict_types"] = [name for name in mapping["dict_types"] if name != "m_ng_words"]
    mapping = extend_mapping(mapping, read_json(integration / "mapping-overrides.json"))
    (ARTIFACTS / "master_mapping.json").write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
    notify("build", "Building the translation plugin", 60)
    build_source = Path(tempfile.mkdtemp(prefix="build-source-", dir=ARTIFACTS))
    shutil.copytree(plugin_source / "AbyssMod", build_source / "AbyssMod", ignore=shutil.ignore_patterns("bin", "obj", ".git"))
    patch = integration / "general-text-compat.patch"
    patch_command = ["git", "-C", str(ROOT), "apply", "--directory=" + build_source.relative_to(ROOT).as_posix()]
    subprocess.run([*patch_command, "--check", str(patch)], check=True)
    subprocess.run([*patch_command, str(patch)], check=True)
    runtime = client if client and (client / "BepInEx" / "interop" / "Project.dll").is_file() else WORKSPACE / "dotabyss_pc" / "toolchain" / "runtime"
    subprocess.run(["dotnet", "build", str(ROOT / "pc-translation" / "AbyssMod.Local.csproj"), "-c", "Release", "--nologo",
                    "-p:SourceDir=" + str(build_source / "AbyssMod"), "-p:ClientDir=" + str(runtime),
                    "-p:DependencyDir=" + str(ARTIFACTS / "dependencies"), "-p:MappingPath=" + str(ARTIFACTS / "master_mapping.json")], check=True)
    notify("stage", "Preparing the local translation cache", 75)
    stage = Path(tempfile.mkdtemp(prefix="prepared-", dir=ARTIFACTS))
    cache = stage / "cache" / "translations"
    for path in inputs:
        target = cache / path.relative_to(tree)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    legacy = cache / "legacy" / "add-on" / "ui_misc" / f"{LANGUAGE}.json"
    fallback = cache / "add-on" / "ui_misc" / f"{LANGUAGE}.json"
    if legacy.exists():
        merged = read_json(legacy)
        if fallback.exists():
            merged.update(read_json(fallback))
        fallback.parent.mkdir(parents=True, exist_ok=True)
        fallback.write_text(json.dumps(merged, ensure_ascii=False), encoding="utf-8")
    shutil.copy2(tree / "manifest" / f"{LANGUAGE}.json", stage / f"source-manifest.{LANGUAGE}.json")
    supplements = merge_overrides(cache, integration / "overrides", integration / "corrections.json")
    rebuild_manifest(cache)
    counts["supplement_entries"] = supplements["added"]
    counts["corrected_entries"] = supplements["corrected"]
    counts["installed_entries"] = sum(len(read_json(path)) for path in cache.rglob(f"{LANGUAGE}.json")
                                      if path.parent.name != "manifest")
    shutil.copy2(ROOT / "pc-translation" / "bin" / "Release" / "net6.0" / "AbyssMod.dll", stage / "AbyssMod.dll")
    shutil.copy2(ARTIFACTS / "dependencies" / "Utility.dll", stage / "Utility.dll")
    (stage / "fonts").mkdir()
    shutil.copy2(ARTIFACTS / "dependencies" / "ttcuyuanj", stage / "fonts" / "ttcuyuanj")
    shutil.copy2(plugin_source / "LICENSE", stage / "LICENSE.AbyssMod")
    report = {"format_version": 1, "language": LANGUAGE, "offline": True,
              "plugin_source": "https://github.com/s88037zz/AbyssMod", "plugin_commit": revision(plugin_source),
              "translation_source": "https://github.com/s88037zz/dotabyss-translation", "translation_commit": revision(source),
              "source_hashes": {path.relative_to(build_source).as_posix(): digest(path) for path in (build_source / "AbyssMod").rglob("*.cs")},
              "runtime_hashes": {path.name: digest(path) for path in (integration / "runtime").glob("*.cs")},
              "supplements": supplements,
              "integration_hashes": {path.relative_to(integration).as_posix(): digest(path) for path in integration.rglob("*")
                                     if path.is_file() and (path.suffix == ".patch" or path.name in ("mapping-overrides.json", "corrections.json")
                                                            or path.is_relative_to(integration / "overrides"))},
              "excluded_mappings": ["MNgWords"], "counts": counts,
              "files": {str(path.relative_to(stage)).replace("\\", "/"): digest(path) for path in stage.rglob("*") if path.is_file()}}
    (stage / "integration-manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return stage, report


def translation_config(original):
    if original is None:
        return CONFIG.encode("utf-8")
    wanted = {
        "Translation": {"Enabled": "true", "CDN": "", "Language": LANGUAGE},
        "Translation.Font": {"AssetBundlePath": "AbyssMod/fonts/ttcuyuanj"},
        "Collector": {"CollectText": "false", "ClassifyText": "false"},
        "MachineTranslation": {"Enabled": "false"},
    }
    pending = {section: dict(values) for section, values in wanted.items()}
    sections = {name.casefold(): name for name in wanted}
    result, current, seen = [], None, set()

    def finish():
        if current:
            result.extend(key + " = " + value for key, value in pending[current].items())
            pending[current].clear()

    for line in original.decode("utf-8-sig").splitlines():
        heading = re.fullmatch(r"\s*\[([^]]+)\]\s*(?:[;#].*)?", line)
        if heading:
            finish()
            current = sections.get(heading[1].casefold())
            if current:
                seen.add(current)
        elif current and "=" in line and not line.lstrip().startswith(("#", ";")):
            supplied = line.split("=", 1)[0].strip().casefold()
            key = next((name for name in wanted[current] if name.casefold() == supplied), None)
            if key:
                line = key + " = " + wanted[current][key]
                pending[current].pop(key, None)
        result.append(line)
    finish()
    for section in wanted:
        if section in seen:
            continue
        result.extend(["", "[" + section + "]"])
        result.extend(key + " = " + value for key, value in wanted[section].items())
    return ("\n".join(result).rstrip() + "\n").encode("utf-8")


def client_executable(client):
    identity = read_json(client / "local-client.json")
    if identity.get("identity") != {"company": "LocalGames", "product": "DotAbyssLocalPlayer"}:
        raise ValueError("Select the prepared local client, not the official client")
    name = identity.get("executable")
    if not isinstance(name, str) or Path(name).name != name or not name.casefold().endswith(".exe"):
        raise ValueError("The prepared client executable is invalid")
    executable = checked(client / name, client)
    if not executable.is_file():
        raise ValueError("The prepared client executable is missing")
    return executable


def require_game_stopped(client):
    from dotabyss_ps.admin_launcher import game_processes

    if game_processes(client_executable(client)):
        raise ValueError("Please exit the local game before installing or updating translation")


def merge_local_dictionaries(target, stage):
    if not target.exists():
        return []
    prior = read_json(target / "integration-manifest.json") if (target / "integration-manifest.json").exists() else {"files": {}}
    user_files = set(prior.get("user_files", []))
    retained = []
    for local in (target / "cache" / "translations").rglob("*.json"):
        checked(local, target)
        relative = local.relative_to(target)
        key = relative.as_posix()
        changed = key not in prior["files"] or digest(local) != prior["files"][key]
        destination = stage / relative
        legacy_extra = "user_files" not in prior and not destination.exists()
        if not changed and key not in user_files and not legacy_extra:
            continue
        if destination.exists():
            if destination.read_bytes() != local.read_bytes():
                raise ValueError("A local translation conflicts with the update; preserve it first: " + key)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(local, destination)
        retained.append(key)
    return sorted(retained)


def install(stage, client, report):
    client = client.resolve()
    identity = read_json(client / "local-client.json")["identity"]
    if identity != {"company": "LocalGames", "product": "DotAbyssLocalPlayer"}:
        raise ValueError("Select the prepared local client, not the official client")
    target = checked(client / "BepInEx" / "plugins" / "AbyssMod", client)
    config = checked(client / "BepInEx" / "config" / "AbyssMod.cfg", client)
    actual_files = {path.relative_to(stage).as_posix() for path in stage.rglob("*") if path.is_file()}
    if actual_files - set(report["files"]) - {"integration-manifest.json"}:
        raise ValueError("The prepared translation contains unexpected files")
    for relative, expected in report["files"].items():
        if digest(checked(stage / relative, stage)) != expected:
            raise ValueError("Prepared translation file failed verification: " + relative)
    user_files = merge_local_dictionaries(target, stage)
    rebuild_manifest(stage / "cache" / "translations")
    report = {**report, "user_files": user_files, "files": {path.relative_to(stage).as_posix(): digest(path) for path in stage.rglob("*")
                                 if path.is_file() and path != stage / "integration-manifest.json"}}
    (stage / "integration-manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    backup = ARTIFACTS / "backups" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup.mkdir(parents=True)
    old_config = config.read_bytes() if config.exists() else None
    installed = ARTIFACTS / "installed.json"
    old_report = installed.read_bytes() if installed.exists() else None
    old_target = backup / "AbyssMod"
    if old_config is not None:
        (backup / "AbyssMod.cfg").write_bytes(old_config)
    moved = published = False
    try:
        if target.exists():
            target.rename(old_target)
            moved = True
        target.parent.mkdir(parents=True, exist_ok=True)
        stage.rename(target)
        published = True
        config.parent.mkdir(parents=True, exist_ok=True)
        temporary = config.with_suffix(".cfg.partial")
        temporary.write_bytes(translation_config(old_config))
        temporary.replace(config)
        for relative, expected in report["files"].items():
            if digest(checked(target / relative, target)) != expected:
                raise ValueError("Installed translation file failed verification: " + relative)
        installed_report = {**report, "installed_at": datetime.now().isoformat(), "client": str(client), "backup": str(backup), "config_sha256": digest(config)}
        report_temp = installed.with_suffix(".json.partial")
        report_temp.write_text(json.dumps(installed_report, ensure_ascii=False, indent=2), encoding="utf-8")
        report_temp.replace(installed)
    except BaseException:
        if published:
            target.rename(backup / "failed-install")
        if moved:
            old_target.rename(target)
        if old_config is None:
            config.unlink(missing_ok=True)
        else:
            config.write_bytes(old_config)
        if old_report is None:
            installed.unlink(missing_ok=True)
        else:
            installed.write_bytes(old_report)
        raise
    return backup


def run(client, *, apply=False, fetch=False, bootstrap=False, notify=progress):
    from dotabyss_ps.process_lock import ProcessLockBusy, exclusive_process_lock

    client = client.resolve()
    client_executable(client)
    try:
        with exclusive_process_lock(client / ".local-launcher.lock"):
            notify("preflight", "Checking the local game and translation environment", 5)
            require_game_stopped(client)
            if shutil.which("dotnet") is None:
                raise ValueError("The .NET SDK is required to build the translation plugin")
            source, plugin_source = WORKSPACE / "reference" / "dotabyss-translation", WORKSPACE / "reference" / "AbyssMod-translator"
            ARTIFACTS.mkdir(parents=True, exist_ok=True)
            if bootstrap or fetch:
                from bootstrap_translation import bootstrap as prepare_dependencies
                prepare_dependencies(source, plugin_source, ARTIFACTS, ROOT / "pc-translation" / "input-text-compat.patch", fetch=fetch, progress=notify)
            stage, report = prepare(source, plugin_source, client, notify=notify)
            require_game_stopped(client)
            notify("install", "Installing and verifying the translation files", 90)
            backup = install(stage, client, report) if apply else None
            result = {"prepared": str(stage), "installed": apply, "backup": str(backup) if backup else None, "counts": report["counts"]}
            notify("complete", "Translation is installed; restart the game to use it" if apply else "Translation preparation is complete", 100)
            return result
    except ProcessLockBusy as error:
        raise ValueError("The game launcher or another translation operation is busy; try again when it finishes") from error


def main():
    parser = argparse.ArgumentParser(description="Prepare or install the local Traditional Chinese translation")
    parser.add_argument("--client", type=Path, default=WORKSPACE / "dotabyss_pc" / "local-client")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--fetch", action="store_true")
    parser.add_argument("--bootstrap", action="store_true")
    args = parser.parse_args()
    try:
        result = run(args.client, apply=args.apply, fetch=args.fetch, bootstrap=args.bootstrap)
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        progress("failed", str(error), 0)
        traceback.print_exc(file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
