from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
from pathlib import Path


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def within(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("File path leaves the selected directory")
    return path


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    if temporary.is_symlink() or path.is_symlink():
        raise ValueError("Manifest paths must be regular files")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def patch_settings(payload: bytes, release: dict) -> bytes:
    if hashlib.sha256(payload).hexdigest() != release["settings"]["sha256"]:
        raise ValueError("PlayerSettings source fingerprint does not match this release")
    patched = bytearray(payload)
    ranges = []
    for change in release["settings"]["string_changes"]:
        offset = change["offset"]
        old, new = change["before"].encode("utf-8"), change["after"].encode("utf-8")
        if len(old) != len(new):
            raise ValueError("PlayerSettings replacement must preserve its serialized width")
        expected = struct.pack("<I", len(old)) + old
        if payload[offset:offset + len(expected)] != expected:
            raise ValueError("PlayerSettings named-field bytes differ from the reviewed release")
        patched[offset + 4:offset + 4 + len(new)] = new
        ranges.append((offset + 4, offset + 4 + len(new)))
    for change in release["settings"]["integer_changes"]:
        offset = change["offset"]
        if payload[offset:offset + 4] != struct.pack("<I", change["before"]):
            raise ValueError("PlayerSettings integer differs from the reviewed release")
        patched[offset:offset + 4] = struct.pack("<I", change["after"])
        ranges.append((offset, offset + 4))
    if any(a != b and not any(start <= i < end for start, end in ranges)
           for i, (a, b) in enumerate(zip(payload, patched))):
        raise ValueError("Unexpected PlayerSettings modification")
    return bytes(patched)


def verify_client(destination: Path, release: dict, *, require_seeds: bool = False) -> dict:
    manifest = json.loads((destination / "local-client.json").read_text(encoding="utf-8"))
    if manifest["release"] != release["release"] or manifest["source_fingerprints"] != release["source_fingerprints"]:
        raise ValueError("Prepared client belongs to a different release")
    if manifest["prepared_fingerprints"] != release["prepared_fingerprints"]:
        raise ValueError("Prepared fingerprints differ from the reviewed release")
    if manifest["root"] != str(destination):
        raise ValueError("Prepared client location changed; prepare a separate validated copy")
    identity = release["identity"]
    profile = Path(os.environ["USERPROFILE"]) / "AppData/LocalLow" / identity["company"] / identity["product"]
    expected = {"format_version": 1, "identity": identity, "profile": str(profile.absolute()),
                "application_version": release["application_version"], "executable": release["executable"],
                "runtime_isolation_verified": False}
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise ValueError("Prepared identity, executable or profile differs from the reviewed release")
    if manifest.get("profile_physical_path") != str(profile.resolve(strict=True)):
        raise ValueError("The Windows profile storage context changed")
    for item in release["profile_seeds"]:
        seed = within(profile, item["relative"])
        if str(seed) != manifest.get("profile_seed_paths", {}).get(item["relative"]):
            raise ValueError("The Windows cache storage context changed")
        try:
            seed_digest = digest(seed)
        except FileNotFoundError:
            if require_seeds:
                raise
            continue
        if seed_digest != item["sha256"]:
            raise ValueError("Prepared catalog or master seed changed")
    for relative, expected in manifest["prepared_fingerprints"].items():
        if digest(within(destination, relative)) != expected:
            raise ValueError("Prepared client fingerprint changed: " + relative)
    return manifest


def prepare(source: Path, destination: Path, release: dict, profile: Path) -> dict:
    source, destination, profile = source.resolve(), destination.resolve(), profile.absolute()
    if source == destination or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError("Original and prepared clients must be separate directories")
    expected_profile = Path(os.environ["USERPROFILE"]) / "AppData/LocalLow" / release["identity"]["company"] / release["identity"]["product"]
    if profile != expected_profile.absolute():
        raise ValueError("Profile does not match the prepared Unity identity")
    for relative, expected in release["source_fingerprints"].items():
        if digest(within(source, relative)) != expected:
            raise ValueError("Official source fingerprint changed: " + relative)
    if (destination / "local-client.json").is_file():
        return verify_client(destination, release)
    if destination.exists():
        for path in destination.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(destination).as_posix()
            if relative == "local-client.json.partial" and not path.is_symlink() and path.stat().st_size <= 1024 * 1024:
                continue
            permitted = {release["source_fingerprints"].get(relative), release["prepared_fingerprints"].get(relative)}
            if relative == release["app_info"] and hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() in permitted:
                continue
            if path.is_symlink() or digest(path) not in permitted:
                raise ValueError("Unrecognized file in incomplete prepared client: " + relative)
    settings_path = within(source, release["settings"]["path"])
    patched = patch_settings(settings_path.read_bytes(), release)
    destination.mkdir(parents=True, exist_ok=True)
    for relative in release["source_fingerprints"]:
        origin, target = within(source, relative), within(destination, relative)
        if origin.is_symlink():
            raise ValueError("Client source must contain regular files")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(origin, target)
    within(destination, release["settings"]["path"]).write_bytes(patched)
    identity = release["identity"]
    within(destination, release["app_info"]).write_bytes((identity["company"] + "\n" + identity["product"] + "\n").encode("utf-8"))
    profile.mkdir(parents=True, exist_ok=True)
    for item in release["profile_seeds"]:
        origin, target = Path(item["source"]), within(profile, item["relative"])
        if digest(origin) != item["sha256"]:
            raise ValueError("Catalog or master seed fingerprint changed")
        if target.exists():
            if digest(target) != item["sha256"]:
                raise ValueError("Local profile already has different seed data; explicit update is required")
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(origin, target)
            if digest(target) != item["sha256"]:
                raise ValueError("Copied catalog or master seed fingerprint changed")
    fingerprints = {relative: digest(within(destination, relative)) for relative in release["source_fingerprints"]}
    manifest = {"format_version": 1, "release": release["release"], "root": str(destination),
                "profile": str(profile), "identity": identity, "application_version": release["application_version"],
                "executable": release["executable"], "source_root": str(source),
                "source_fingerprints": release["source_fingerprints"], "prepared_fingerprints": fingerprints,
                "profile_physical_path": str(profile.resolve(strict=True)),
                "profile_seed_paths": {item["relative"]: str(within(profile, item["relative"])) for item in release["profile_seeds"]},
                "runtime_isolation_verified": False}
    write_json(destination / "local-client.json", manifest)
    for relative, expected in release["source_fingerprints"].items():
        if digest(within(source, relative)) != expected:
            raise ValueError("Official source changed during preparation")
    return verify_client(destination, release, require_seeds=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare a fingerprint-bound isolated PC client")
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    release = json.loads(args.release.read_text(encoding="utf-8"))
    destination = args.destination.resolve()
    if args.verify:
        result = verify_client(destination, release)
    else:
        if args.source is None:
            parser.error("--source is required for preparation")
        identity = release["identity"]
        profile = Path(os.environ["USERPROFILE"]) / "AppData/LocalLow" / identity["company"] / identity["product"]
        result = prepare(args.source, destination, release, profile)
    print(json.dumps({"root": result["root"], "profile": result["profile"], "release": result["release"],
                      "verified_files": len(result["prepared_fingerprints"]),
                      "runtime_isolation_verified": result["runtime_isolation_verified"]}, ensure_ascii=True))


if __name__ == "__main__":
    main()
