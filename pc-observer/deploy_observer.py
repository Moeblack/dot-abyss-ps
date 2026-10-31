from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import secrets
from functools import wraps
from pathlib import Path, PurePosixPath

PROJECT = Path(__file__).resolve().parents[1]
WORKSPACE = PROJECT.parent
MANIFEST = "observer-payload.json"
INSTALL_RECORD = "dotabyss-observer.install.json"
PLUGIN = "BepInEx/plugins/DotAbyss.Observer.Pc/"
RUNTIME_HASH = "9bd74dc51718bfb4ba7b92d36db6104d651d7083adca9d6dd378847eb74f0bcb"
HARMONY_HASH = "bd77755f1525cb46fc408e2f2ac3df962e657c82dd01feac6a48f08bf3c754d8"
INTEROP_HASH = "e318a24fbb11793e504bb9d2510b3937b6329b7f0054d11314bde508d18de7f6"
ROOT_FILES = {"winhttp.dll", "doorstop_config.ini", ".doorstop_version"}
LOADER_NAMES = ROOT_FILES | {"version.dll", "winmm.dll", "dinput8.dll", "dxgi.dll", "wininet.dll"}
PLUGIN_FILES = {PLUGIN + name for name in ("DotAbyss.Observer.Pc.dll", "DotAbyss.Observer.Core.dll", "observer.json")}
REQUIRED_FILES = ROOT_FILES | PLUGIN_FILES | {
    "BepInEx/config/BepInEx.cfg", "BepInEx/core/BepInEx.Unity.IL2CPP.dll",
    "BepInEx/core/Il2CppInterop.Runtime.dll", "BepInEx/core/Il2CppInterop.HarmonySupport.dll",
    "BepInEx/interop/Project.dll", "dotnet/coreclr.dll",
}


class DeploymentError(ValueError):
    pass


def encoded(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def package_hash(document: dict) -> str:
    return hashlib.sha256(encoded(document)).hexdigest()


def digest(path: Path) -> str:
    regular(path)
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def no_links(path: Path) -> None:
    for part in reversed((path, *path.parents)):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise DeploymentError("Symlink or junction in deployment path")


def root_path(path: Path) -> Path:
    result = Path(os.path.abspath(path))
    no_links(result)
    return result


def regular(path: Path) -> None:
    no_links(path)
    if not path.is_file():
        raise DeploymentError("Expected a regular deployment file: " + str(path))


def relative_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or value.startswith("/"):
        raise DeploymentError("Invalid relative deployment path")
    parts = value.split("/")
    if any(not part or part in (".", "..") or part.endswith((".", " "))
           or any(character in part for character in ':<>|?*\x00')
           or part.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
           for part in parts):
        raise DeploymentError("Invalid relative deployment path")
    return value


def within(root: Path, relative: str) -> Path:
    result = root / relative_path(relative)
    no_links(result)
    if not result.resolve().is_relative_to(root.resolve()):
        raise DeploymentError("Deployment path leaves its root")
    return result


def disjoint(first: Path, second: Path) -> None:
    if first.is_relative_to(second) or second.is_relative_to(first):
        raise DeploymentError("Deployment directories must be separate")


def output_path(output: Path, workspace: Path, *, client: Path | None = None, protected: tuple[Path, ...] = ()) -> Path:
    if not output.is_absolute():
        raise DeploymentError("Capture output must be an absolute workspace path")
    output, workspace = root_path(output), root_path(workspace)
    if output == workspace or not output.is_relative_to(workspace):
        raise DeploymentError("Capture output leaves the workspace")
    for blocked in (*protected, *((client,) if client else ())):
        disjoint(output, root_path(blocked))
    return output


def _load(path: Path) -> dict:
    regular(path)
    if path.stat().st_size > 4 * 1024 * 1024:
        raise DeploymentError("Deployment manifest exceeds its size limit")

    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise DeploymentError("Duplicate manifest key")
            result[key] = value
        return result

    value = json.loads(path.read_bytes(), object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise DeploymentError("Deployment manifest must be an object")
    return value


def _write_once(path: Path, payload: bytes) -> None:
    no_links(path)
    if path.exists():
        regular(path)
        if path.read_bytes() != payload:
            raise DeploymentError("An existing deployment record differs")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    no_links(temporary)
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.link(temporary, path)
    temporary.unlink()


def _allowed_payload(relative: str) -> bool:
    return relative in ROOT_FILES | PLUGIN_FILES | {"BepInEx/config/BepInEx.cfg"} or relative.startswith(
        ("BepInEx/core/", "BepInEx/interop/", "dotnet/"))


def _inventory(root: Path) -> dict[str, Path]:
    root = root_path(root)
    if not root.is_dir():
        raise DeploymentError("Missing payload source directory")
    result = {}
    for directory, folders, files in os.walk(root, followlinks=False):
        for name in (*folders, *files):
            no_links(Path(directory) / name)
        for name in files:
            path = Path(directory) / name
            regular(path)
            result[path.relative_to(root).as_posix()] = path
    return result


def _validate_files(files: dict, release: dict) -> None:
    if not isinstance(files, dict) or not REQUIRED_FILES <= set(files):
        raise DeploymentError("Observer payload is incomplete")
    folded = set()
    originals = {name.casefold() for name in release["source_fingerprints"]}
    for name, value in files.items():
        relative_path(name)
        if not _allowed_payload(name) or name.casefold() in folded or name.casefold() in originals:
            raise DeploymentError("Payload path is not exclusively owned by the observer")
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise DeploymentError("Invalid payload fingerprint")
        folded.add(name.casefold())


def collect_payload(output: Path) -> dict[str, Path | bytes]:
    runtime = WORKSPACE / "dotabyss_pc/toolchain/runtime"
    local = WORKSPACE / "dotabyss_pc/local-client/BepInEx"
    interop = _inventory(local / "interop")
    fingerprints = {name: digest(path) for name, path in interop.items()}
    aggregate = hashlib.sha256(json.dumps(fingerprints, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if len(interop) != 195 or aggregate != INTEROP_HASH:
        raise DeploymentError("Interop files are not the verified annotated PC build")
    result = {name: runtime / name for name in ROOT_FILES}
    for subdirectory in ("BepInEx/core", "dotnet"):
        result.update({subdirectory + "/" + name: path for name, path in _inventory(runtime / subdirectory).items()})
    result.update({"BepInEx/interop/" + name: path for name, path in interop.items()})
    for name, expected in (("Il2CppInterop.Runtime.dll", RUNTIME_HASH), ("Il2CppInterop.HarmonySupport.dll", HARMONY_HASH)):
        source = local / "core" / name
        if digest(source) != expected:
            raise DeploymentError("Patched runtime fingerprint changed: " + name)
        result["BepInEx/core/" + name] = source
    build = PROJECT / "pc-observer/bin/Release/net6.0"
    for name in ("DotAbyss.Observer.Pc.dll", "DotAbyss.Observer.Core.dll"):
        result[PLUGIN + name] = build / name
    result["BepInEx/config/BepInEx.cfg"] = WORKSPACE / "dotabyss_pc/toolchain/bepinex-minimal.cfg"
    result[PLUGIN + "observer.json"] = encoded({"outputRoot": str(output), "captureRequests": True})
    return result


def _copy_verified(source: Path | bytes, destination: Path, expected: str, temporary: Path) -> None:
    no_links(destination)
    no_links(temporary)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if digest(destination) != expected:
            raise DeploymentError("An owned file changed: " + str(destination))
        return
    if temporary.exists():
        if digest(temporary) != expected:
            raise DeploymentError("An interrupted copy needs manual review: " + str(temporary))
    else:
        with temporary.open("xb") as target:
            if isinstance(source, bytes):
                target.write(source)
            else:
                regular(source)
                with source.open("rb") as stream:
                    shutil.copyfileobj(stream, target)
            target.flush()
            os.fsync(target.fileno())
        if digest(temporary) != expected:
            raise DeploymentError("Payload changed while copying")
    os.link(temporary, destination)
    temporary.unlink()


def _temporary(relative: str) -> str:
    path = PurePosixPath(relative)
    return (path.parent / (".observer-" + hashlib.sha256(relative.encode()).hexdigest()[:24] + ".partial")).as_posix()


def stage_package(package: Path, release: dict, sources: dict[str, Path | bytes], output: Path, workspace: Path,
                  *, protected: tuple[Path, ...] = ()) -> dict:
    package, workspace = root_path(package), root_path(workspace)
    if not package.is_relative_to(workspace) or package == workspace:
        raise DeploymentError("Staging directory must be inside the workspace")
    for blocked in protected:
        disjoint(package, root_path(blocked))
    output = output_path(output, workspace, protected=protected)
    disjoint(package, output)
    files = {name: hashlib.sha256(source).hexdigest() if isinstance(source, bytes) else digest(source) for name, source in sources.items()}
    _validate_files(files, release)
    config = sources[PLUGIN + "observer.json"]
    config = json.loads(config if isinstance(config, bytes) else config.read_bytes())
    if config != {"outputRoot": str(output), "captureRequests": True}:
        raise DeploymentError("Observer capture configuration differs from the staging request")
    document = {"format_version": 1, "release": release["release"], "original_fingerprints": release["source_fingerprints"],
                "workspace": str(workspace), "output_root": str(output), "files": files}
    if package.exists() and any(package.iterdir()):
        if not (package / MANIFEST).is_file() or _load(package / MANIFEST) != document:
            raise DeploymentError("Staging directory contains a different package")
    package.mkdir(parents=True, exist_ok=True)
    _write_once(package / MANIFEST, encoded(document))
    for name in sorted(files):
        _copy_verified(sources[name], within(package / "payload", name), files[name], within(package / "payload", _temporary(name)))
    return verify_package(package, release, workspace, protected=protected)


def verify_package(package: Path, release: dict, workspace: Path, *, protected: tuple[Path, ...] = ()) -> dict:
    package, workspace = root_path(package), root_path(workspace)
    if package == workspace or not package.is_relative_to(workspace):
        raise DeploymentError("Package leaves the workspace")
    for blocked in protected:
        disjoint(package, root_path(blocked))
    document = _load(package / MANIFEST)
    expected_keys = {"format_version", "release", "original_fingerprints", "workspace", "output_root", "files"}
    if set(document) != expected_keys or document["format_version"] != 1 or document["release"] != release["release"] \
            or document["original_fingerprints"] != release["source_fingerprints"] or document["workspace"] != str(workspace):
        raise DeploymentError("Package does not match the reviewed release")
    _validate_files(document["files"], release)
    output = output_path(Path(document["output_root"]), workspace, protected=protected)
    disjoint(package, output)
    inventory = _inventory(package / "payload")
    if set(inventory) != set(document["files"]):
        raise DeploymentError("Package contains missing or unlisted payload files")
    for name, expected in document["files"].items():
        if digest(inventory[name]) != expected:
            raise DeploymentError("Staged payload fingerprint changed: " + name)
    if _load(inventory[PLUGIN + "observer.json"]) != {"outputRoot": str(output), "captureRequests": True}:
        raise DeploymentError("Staged observer configuration changed")
    return document


def verify_original(client: Path, release: dict) -> None:
    for name, expected in release["source_fingerprints"].items():
        if digest(within(client, name)) != expected:
            raise DeploymentError("Original client fingerprint changed: " + name)


def _record(package: Path, client: Path, document: dict) -> tuple[dict, Path]:
    fingerprint = package_hash(document)
    record = {"format_version": 1, "client": str(client), "package_sha256": fingerprint, "package": document,
              "temporary_files": {_temporary(name): value for name, value in document["files"].items()}}
    identity = hashlib.sha256(os.path.normcase(str(client)).encode()).hexdigest()
    return record, within(package, f"receipts/{identity}-{fingerprint}.json")


def _layout(package: Path, client: Path, release: dict, workspace: Path, protected: tuple[Path, ...], expected_package_sha256: str | None = None):
    package, client = root_path(package), root_path(client)
    if not client.is_dir():
        raise DeploymentError("Selected official client directory does not exist")
    disjoint(package, client)
    document = verify_package(package, release, workspace, protected=protected)
    if expected_package_sha256 is not None and package_hash(document) != expected_package_sha256:
        raise DeploymentError("Package differs from its reviewed SHA-256")
    output_path(Path(document["output_root"]), workspace, client=client, protected=protected)
    record, receipt = _record(package, client, document)
    return package, client, document, record, receipt


def _read_ownership(client: Path, record: dict, receipt: Path) -> None:
    if _load(receipt) != record or _load(within(client, INSTALL_RECORD)) != record:
        raise DeploymentError("Installation ownership record differs from its external receipt")


def _scan_owned(client: Path, record: dict) -> tuple[list[str], list[str]]:
    missing, changed = [], []
    for name, expected in record["package"]["files"].items():
        path = within(client, name)
        if not path.exists():
            missing.append(name)
        elif digest(path) != expected:
            changed.append(name)
    return missing, changed


def verify_installation(package: Path, client: Path, release: dict, workspace: Path, *, protected: tuple[Path, ...] = (),
                        expected_package_sha256: str | None = None) -> dict:
    _, client, _, record, receipt = _layout(package, client, release, workspace, protected, expected_package_sha256)
    _read_ownership(client, record, receipt)
    verify_original(client, release)
    missing, changed = _scan_owned(client, record)
    if missing or changed:
        raise DeploymentError(f"Installation is incomplete or modified: missing={missing}, changed={changed}")
    return {"status": "verified", "client": str(client), "files": len(record["package"]["files"]), "original_verified": True}


def _partial_inventory(client: Path, record: dict) -> None:
    allowed = record["package"]["files"] | record["temporary_files"]
    for path in client.iterdir():
        if path.name.casefold() in {name.casefold() for name in LOADER_NAMES} and path.name not in allowed:
            raise DeploymentError("Unexpected loader in the selected client")
    for folder in ("BepInEx", "dotnet"):
        path = within(client, folder)
        if path.exists():
            for relative in _inventory(path):
                name = folder + "/" + relative
                if name not in allowed:
                    raise DeploymentError("Unexpected file in a partial deployment: " + name)
    for name, expected in allowed.items():
        path = within(client, name)
        if path.exists() and digest(path) != expected:
            raise DeploymentError("Partial deployment contains a changed owned file: " + name)


def exclusive_operation(function):
    @wraps(function)
    def guarded(package, client, release, workspace, *args, **kwargs):
        client, workspace = root_path(client), root_path(workspace)
        identity = hashlib.sha256(os.path.normcase(str(client)).encode()).hexdigest()
        lock = within(workspace, f".observer-deployment-locks/{identity}.lock")
        disjoint(client, lock.parent)
        lock.parent.mkdir(parents=True, exist_ok=True)
        token = f"{os.getpid()}:{secrets.token_hex(16)}".encode()
        try:
            with lock.open("xb") as stream:
                stream.write(token)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as error:
            raise DeploymentError("Another deployment operation or recovery lock exists") from error
        try:
            return function(package, client, release, workspace, *args, **kwargs)
        finally:
            no_links(lock)
            if lock.read_bytes() == token:
                lock.unlink()
    return guarded


@exclusive_operation
def install_package(package: Path, client: Path, release: dict, workspace: Path, *, repair: bool = False,
                    protected: tuple[Path, ...] = (), expected_package_sha256: str | None = None) -> dict:
    if expected_package_sha256 is None:
        raise DeploymentError("Install requires the reviewed package SHA-256")
    package, client, document, record, receipt = _layout(package, client, release, workspace, protected, expected_package_sha256)
    verify_original(client, release)
    owner = within(client, INSTALL_RECORD)
    if owner.exists():
        _read_ownership(client, record, receipt)
        _partial_inventory(client, record)
    elif repair and receipt.exists():
        if _load(receipt) != record:
            raise DeploymentError("Repair receipt differs from the requested deployment")
        _partial_inventory(client, record)
    else:
        blockers = {name.casefold() for name in (*LOADER_NAMES, "BepInEx", "dotnet", INSTALL_RECORD)}
        if any(path.name.casefold() in blockers for path in client.iterdir()):
            raise DeploymentError("Client already contains a loader or conflicting deployment directory")
    _write_once(receipt, encoded(record))
    _write_once(owner, encoded(record))
    try:
        ordered = sorted(name for name in document["files"] if name != "winhttp.dll") + ["winhttp.dll"]
        for name in ordered:
            _copy_verified(within(package / "payload", name), within(client, name), document["files"][name], within(client, _temporary(name)))
        return verify_installation(package, client, release, workspace, protected=protected, expected_package_sha256=expected_package_sha256)
    except (OSError, DeploymentError) as error:
        cleanup = _remove_owned(client, record, release)
        raise DeploymentError("Installation failed; cleanup=" + cleanup["status"]) from error


@exclusive_operation
def remove_package(package: Path, client: Path, release: dict, workspace: Path, *, protected: tuple[Path, ...] = (),
                   expected_package_sha256: str | None = None) -> dict:
    if expected_package_sha256 is None:
        raise DeploymentError("Remove requires the reviewed package SHA-256")
    _, client, _, record, receipt = _layout(package, client, release, workspace, protected, expected_package_sha256)
    _read_ownership(client, record, receipt)
    return _remove_owned(client, record, release)


def _remove_owned(client: Path, record: dict, release: dict) -> dict:
    document = record["package"]
    loader = within(client, "winhttp.dll")
    if loader.exists() and digest(loader) != document["files"]["winhttp.dll"]:
        return {"status": "manual_review", "loader_disabled": False, "changed": ["winhttp.dll"]}
    if loader.exists():
        loader.unlink()
    changed = []
    owned = document["files"] | record["temporary_files"]
    for name, expected in owned.items():
        path = within(client, name)
        if path.exists():
            if digest(path) != expected:
                changed.append(name)
            else:
                path.unlink()
    directories = {parent.as_posix() for name in owned for parent in PurePosixPath(name).parents if parent.as_posix() != "."}
    for name in sorted(directories, key=lambda value: (value.count("/"), value), reverse=True):
        directory = within(client, name)
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
    if not changed:
        owner = within(client, INSTALL_RECORD)
        if owner.read_bytes() != encoded(record):
            changed.append(INSTALL_RECORD)
        else:
            owner.unlink()
    try:
        verify_original(client, release)
        original_verified = True
    except (OSError, DeploymentError):
        original_verified = False
    return {"status": "manual_review" if changed else "removed", "loader_disabled": True,
            "changed": changed, "original_verified": original_verified}


def main() -> None:
    parser = argparse.ArgumentParser(description="Stage and reversibly deploy the official PC observer")
    parser.add_argument("action", nargs="?", choices=("stage", "verify", "install", "repair", "remove"), default="stage")
    parser.add_argument("--package", type=Path, default=PROJECT / "artifacts/pc-observer-package")
    parser.add_argument("--client", type=Path)
    parser.add_argument("--output-root", type=Path, default=PROJECT / "captures")
    parser.add_argument("--expected-package-sha256")
    args = parser.parse_args()
    release = _load(PROJECT / "pc/releases/pc-1.15.0.json")
    if len(release.get("source_fingerprints", {})) != 44 or release.get("launcher_product_id") != "dotabyss_x_cl":
        parser.error("The reviewed official PC release is unavailable")
    profile = Path(os.environ.get("USERPROFILE", str(Path.home())))
    protected = (profile / "AppData/LocalLow", profile / "AppData/Local/Packages",
                 WORKSPACE / "dotabyss_x_cl", WORKSPACE / "dotabyss_pc/local-client")
    try:
        if args.action == "stage":
            output = output_path(args.output_root, WORKSPACE, protected=protected)
            document = stage_package(args.package, release, collect_payload(output), output, WORKSPACE, protected=protected)
            result = {"status": "staged", "files": len(document["files"]), "package": str(root_path(args.package)),
                      "package_sha256": package_hash(document)}
        elif args.action == "verify" and args.client is None:
            document = verify_package(args.package, release, WORKSPACE, protected=protected)
            if args.expected_package_sha256 and package_hash(document) != args.expected_package_sha256:
                raise DeploymentError("Package differs from its reviewed SHA-256")
            result = {"status": "verified", "files": len(document["files"]), "installed": False,
                      "package_sha256": package_hash(document)}
        else:
            if args.client is None:
                parser.error("This action requires an explicit --client directory")
            options = {"protected": protected, "expected_package_sha256": args.expected_package_sha256}
            if args.action in ("install", "repair"):
                result = install_package(args.package, args.client, release, WORKSPACE, repair=args.action == "repair", **options)
            elif args.action == "remove":
                result = remove_package(args.package, args.client, release, WORKSPACE, **options)
            else:
                result = verify_installation(args.package, args.client, release, WORKSPACE, **options)
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Observer deployment failed ({type(error).__name__}): {error}\n")


if __name__ == "__main__":
    main()
