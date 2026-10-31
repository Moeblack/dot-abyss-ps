from __future__ import annotations

import argparse
from contextlib import ExitStack, nullcontext
import json
import os
import sys
from pathlib import Path

from dotabyss_ps.admin_launcher import atomic_bytes, game_processes, read_object
from dotabyss_ps.asset_index import PC_CATALOG_HASH_PATH, PC_CATALOG_PATH, build_index
from dotabyss_ps.assets import AssetCatalog, read_bounded
from dotabyss_ps.masterdata import MasterCatalog, canonical_json, inspect_payload
from dotabyss_ps.process_lock import ProcessLockBusy, exclusive_process_lock
from dotabyss_ps.storage import Store
from pc.prepare_client import prepare, verify_client
from pc.setup_client import install_plugin, resolve_seeds


class _PendingMaster:
    def __init__(self, record):
        self.record = record

    def master(self):
        return self.record


def _regular_output(path):
    if path.is_symlink() or path.exists() and not path.is_file():
        raise ValueError("Output must be a regular file: " + str(path))


def _cache(release, source, supplied):
    if supplied is not None:
        path = Path(supplied).resolve()
        return path, resolve_seeds(release, path)
    local_low = Path(os.environ["USERPROFILE"]) / "AppData/LocalLow"
    choices = ([source.parent / "resource-cache"] if source else []) + [
        local_low / "EXNOA LLC_/ドットアビスX",
        local_low / "LocalGames/DotAbyssLocalPlayer",
    ]
    errors = []
    for path in choices:
        if not path.is_dir():
            continue
        try:
            return path.resolve(), resolve_seeds(release, path)
        except ValueError as error:
            errors.append(str(error))
    detail = "\n" + "\n".join(errors) if errors else ""
    raise ValueError("Matching catalog and master cache not found; provide -CacheDirectory." + detail)


def _shared(source, cache, supplied, index, executable):
    if supplied is not None:
        path = Path(supplied).resolve()
        if not path.is_dir():
            raise ValueError("Resource cache directory is missing: " + str(path))
        return path
    choices = ([source / (Path(executable).stem + "_Data") / "Caches"] if source else []) + [
        cache / "Shared", cache / "UnityCache/Shared", cache / "Caches",
    ]
    for path in choices:
        if path.is_dir() and any((path / item["cache_key"] / item["hash128"] / "__info").is_file()
                                 for item in index["records"]):
            return path.resolve()
    raise ValueError("Resource bundles not found; provide -SharedDirectory pointing to the Caches or Shared folder.")


def inspect_inputs(project_root, *, source=None, cache_root=None, shared_root=None):
    root = Path(project_root).resolve()
    client = root.parent / "dotabyss_pc/local-client"
    release_path = root / "pc/releases/pc-1.15.0.json"
    release = read_object(release_path if release_path.exists() or release_path.is_symlink()
                          else root / "pc/releases/pc-1.15.0.example.json")
    if (release.get("identity") != {"company": "LocalGames", "product": "DotAbyssLocalPlayer"}
            or release.get("application_version") != "1.15.0" or release.get("launcher_product_id") != "dotabyss_x_cl"):
        raise ValueError("PC setup requires the supported 1.15.0 release definition")
    if source is None and (client / "local-client.json").is_file():
        previous = read_object(client / "local-client.json").get("source_root")
        source = previous if isinstance(previous, str) and Path(previous).is_dir() else None
    source = Path(source).resolve() if source is not None else None
    if source is not None and not source.is_dir():
        raise ValueError("Source game directory is missing: " + str(source))
    if source is None and not (client / "local-client.json").is_file():
        raise ValueError("Provide -Source with the original game directory for the first setup")
    if source is not None and (source == client or source.is_relative_to(client) or client.is_relative_to(source)):
        raise ValueError("Original and prepared clients must be separate directories")
    cache, release = _cache(release, source, cache_root)
    seeds = release["profile_seeds"]

    def seed(folder, suffix):
        selected = [Path(item["source"]) for item in seeds
                    if Path(item["relative"]).parent.as_posix() == folder and Path(item["relative"]).suffix == suffix]
        if len(selected) != 1:
            raise ValueError("Release must define exactly one " + folder + " " + suffix + " seed")
        return selected[0]

    catalog, catalog_hash = seed("com.unity.addressables", ".bin"), seed("com.unity.addressables", ".hash")
    master = seed("DownloadCache", ".dat")
    index = build_index(catalog, catalog_hash)
    shared = _shared(source, cache, shared_root, index, release["executable"])
    schema = root / "artifacts/master-schema-pc-1.15.0.json"
    report = inspect_payload(read_bounded(master, 64 * 1024 * 1024), read_object(schema))
    metadata = next((value for key, value in release["source_fingerprints"].items()
                     if key.endswith("/il2cpp_data/Metadata/global-metadata.dat")), None)
    if (report["client"].get("package") != release["launcher_product_id"]
            or report["client"].get("version") != release["application_version"]
            or report["client"].get("metadata_sha256") != metadata):
        raise ValueError("Master schema does not match the selected PC client")
    return {"root": root, "client": client, "release_path": release_path, "release": release,
            "source": source, "cache": cache, "shared": shared, "catalog": catalog, "catalog_hash": catalog_hash,
            "master": master, "schema": schema, "index": index, "master_report": report}


def _check_active(store, report):
    active = store.master()
    if active is not None and active["revision"] != report["revision"]:
        raise ValueError("Existing data uses another master revision; setup will not migrate or reset saves")
    with store.connection() as db:
        other = db.execute("SELECT 1 FROM game_states WHERE master_revision<>? LIMIT 1", (report["revision"],)).fetchone()
    if other:
        raise ValueError("Existing saves use another master revision; use a separate data directory")


def import_resources(inputs, data_dir, *, allow_partial=False, progress=print):
    data_dir = Path(data_dir).resolve()
    index_path = data_dir / "setup/resource-url-index.json"
    report_path = data_dir / "setup/resource-report.json"
    _regular_output(index_path)
    _regular_output(report_path)
    store = Store(data_dir / "local.sqlite3")
    _check_active(store, inputs["master_report"])
    masters = MasterCatalog(data_dir, store)
    master = masters.stage(inputs["master"], inputs["schema"], "PC setup: verified local cache")
    if master["revision"] != inputs["master_report"]["revision"]:
        raise ValueError("Master data or schema changed during setup; check the cache and retry")
    assets = AssetCatalog(data_dir, store)
    active = assets.active()
    if active is not None:
        reusable = (active["master"]["revision"] == master["revision"] and active.get("complete")
                    and active["catalog"]["sha256"] == inputs["index"]["catalog_sha256"]
                    and active["catalog_hash_value"] == inputs["index"]["catalog_version_hash"]
                    and active["catalog"].get("relative_path") == PC_CATALOG_PATH
                    and active["catalog_hash"].get("relative_path") == PC_CATALOG_HASH_PATH)
        if reusable:
            progress("Validating the existing complete resource manifest...")
            summary = assets.activate(active["revision"])
            return {"master_revision": master["revision"], "assets": summary, "reused": True}
    atomic_bytes(index_path, canonical_json(inputs["index"]))
    progress("Validating and importing cached resources; large caches may take several minutes...")
    staged = AssetCatalog(data_dir, _PendingMaster(master)).stage(
        index_path, inputs["catalog"], inputs["catalog_hash"], inputs["shared"], "PC setup: verified local cache",
        allow_partial=True, catalog_relative_path=PC_CATALOG_PATH, catalog_hash_relative_path=PC_CATALOG_HASH_PATH)
    report = {"indexed_count": staged["indexed_count"], "verified_count": staged["verified_count"],
              "missing": staged["missing"], "complete": staged["complete"], "revision": staged["revision"]}
    atomic_bytes(report_path, canonical_json(report))
    if not staged["verified_count"]:
        raise ValueError("No resource files matched the catalog; check -SharedDirectory. Report: " + str(report_path))
    if not staged["complete"] and not allow_partial:
        raise ValueError(f"Missing {len(staged['missing'])} resources; existing activation is unchanged. "
                         "Download the missing resources or explicitly use -AllowPartial. Report: " + str(report_path))
    _check_active(store, inputs["master_report"])
    if store.master() is None:
        masters.activate(master["revision"])
    summary = assets.activate(staged["revision"])
    return {"master_revision": master["revision"], "assets": summary, "reused": False, "report": str(report_path)}


def initialize(project_root, *, source=None, cache_root=None, shared_root=None, data_dir=None,
               allow_partial=False, check=False, progress=print):
    inputs = inspect_inputs(project_root, source=source, cache_root=cache_root, shared_root=shared_root)
    root, client = inputs["root"], inputs["client"]
    data_dir = Path(data_dir).resolve() if data_dir is not None else root / "data-pc-1.15.0"
    result = {"client": str(client), "cache": str(inputs["cache"]), "shared": str(inputs["shared"]),
              "data_dir": str(data_dir), "indexed_count": len(inputs["index"]["records"]),
              "master_revision": inputs["master_report"]["revision"]}
    if check:
        return {**result, "checked_inputs_only": True}
    with ExitStack() as locks:
        locks.enter_context(exclusive_process_lock(client.parent / ".local-client-setup.lock"))
        locks.enter_context(exclusive_process_lock(root / "artifacts/setup/initialize.lock"))
        client_lock = exclusive_process_lock(client / ".local-launcher.lock") if (client / "local-client.json").is_file() else nullcontext()
        with client_lock:
            if game_processes(client / inputs["release"]["executable"]):
                raise ValueError("Exit the local game before running PC setup")
            if (client / "local-client.json").is_file():
                progress("Validating the prepared client...")
                verify_client(client, inputs["release"])
            else:
                progress("Preparing an isolated local client...")
                identity = inputs["release"]["identity"]
                profile = Path(os.environ["USERPROFILE"]) / "AppData/LocalLow" / identity["company"] / identity["product"]
                prepare(inputs["source"], client, inputs["release"], profile)
            release_path = inputs["release_path"]
            _regular_output(release_path)
            if not release_path.exists():
                atomic_bytes(release_path, canonical_json(inputs["release"]))
            result.update(import_resources(inputs, data_dir, allow_partial=allow_partial, progress=progress))
        if (client / "BepInEx/interop/Project.dll").is_file():
            progress("Building and installing the local plugin...")
            result["plugin"] = install_plugin(root, client)
            result["ready"] = True
        else:
            result["ready"] = False
            result["next"] = ("Install the matching BepInEx IL2CPP x64 loader in the prepared client, generate interop once, "
                              "exit the game, then run .\\setup-pc.ps1 -PluginOnly. See docs/first-start.md.")
    return result


def main():
    parser = argparse.ArgumentParser(description="Initialize a local PC game from an existing installation and cache")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--shared-dir", type=Path)
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--allow-partial", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--plugin-only", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        if args.plugin_only:
            if args.check or args.allow_partial or args.source or args.cache_dir or args.shared_dir or args.data_dir:
                parser.error("--plugin-only cannot be combined with initialization options")
            release_path = root / "pc/releases/pc-1.15.0.json"
            client = root.parent / "dotabyss_pc/local-client"
            verify_client(client, read_object(release_path))
            result = {"plugin": install_plugin(root, client), "ready": True}
        else:
            result = initialize(root, source=args.source, cache_root=args.cache_dir, shared_root=args.shared_dir,
                                data_dir=args.data_dir, allow_partial=args.allow_partial, check=args.check,
                                progress=lambda message: print(message, file=sys.stderr, flush=True))
    except (OSError, ValueError, ProcessLockBusy) as error:
        parser.exit(1, "PC setup failed: " + str(error) + "\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
