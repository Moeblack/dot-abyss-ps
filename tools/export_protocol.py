from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from .binary_reader import BinaryFormatError
from .il2cpp_metadata import read_literals, read_manifest_identity
from .managed_metadata import ManagedMetadata


HEADERS = {
    "api-key", "api-token", "x-olg-session", "x-olg-session-name", "x-olg-device",
    "x-olg-response", "x-verification", "x-request-id", "x-new-user", "x-guest",
    "x-aws-waf-token", "X-Client-Version", "X-Resource-Version", "X-Server-Version",
    "X-Content-Type", "version-application", "version-asset-bundle-data",
    "version-asset-bundle-asset", "Content-Type", "Content-Encoding", "Cookie", "Set-Cookie",
}
BOOTSTRAP_TYPES = {
    "ApiResponseEntity`1", "CommonEntity", "ErrorEntity", "VersionEntity", "VersionErrorType",
    "RootLoginRequestEntity", "RootLoginResponseEntity", "RootRequestEntity", "RootResponseEntity",
    "HomeRequestEntity", "HomeResponseEntity", "UserRequestEntity", "UserResponseEntity",
}


def source_record(path: Path, root: Path) -> dict:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": path.relative_to(root).as_posix(), "size_bytes": path.stat().st_size, "sha256": digest}


def select_protocol_literals(literals: list[str]) -> dict:
    return {
        "routes": sorted({value for value in literals if re.fullmatch(r"api/[a-z0-9_/{}/-]+", value)}),
        "headers": sorted(HEADERS.intersection(literals), key=str.casefold),
        "content_types": sorted({value for value in literals if value in {
            "application/json", "application/x-msgpack", "application/octet-stream",
            "application/x-www-form-urlencoded",
        }}),
    }


def export_protocol(unpacked: Path) -> dict:
    root = unpacked.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Unpacked APK directory does not exist: {root}")
    metadata_path = root / "assets/bin/Data/Managed/Metadata/global-metadata.dat"
    manifest_path = root / "AndroidManifest.xml"
    assembly_root = root / "assets/LemonLoader/runtime/interop"
    assembly_paths = [assembly_root / name for name in ("Il2CppAbsf.dll", "Il2CppProject.dll")]
    for path in [metadata_path, manifest_path, *assembly_paths]:
        if not path.is_file():
            raise FileNotFoundError(f"Required APK file does not exist: {path}")
    literals = read_literals(metadata_path.read_bytes())
    selected_literals = select_protocol_literals(literals)
    types = []
    for path in assembly_paths:
        metadata = ManagedMetadata(path.read_bytes(), path.name)
        for entry in metadata.types(("Il2CppAbsf.Api", "Il2CppProject.Api")):
            types.append({"assembly": path.name, **entry})
    sources = [source_record(path, root) for path in [manifest_path, metadata_path, *assembly_paths]]
    identity = read_manifest_identity(manifest_path.read_bytes())
    apk = root.with_name(root.name.removesuffix("_unpacked") + ".apk")
    if apk.is_file():
        identity["adjacent_apk"] = source_record(apk, root.parent)
        identity["adjacent_apk_verification"] = "hash_only; extraction correspondence not independently verified"
    return {
        "schema_version": 1,
        "identity": identity,
        "sources": sources,
        "metadata_version": 39,
        **selected_literals,
        "types": types,
        "bootstrap": {
            "type_names": [entry["full_name"] for entry in types if entry["name"] in BOOTSTRAP_TYPES],
            "confirmed_route_literals": [route for route in selected_literals["routes"] if route in {
                "api/health-check", "api/home", "api/user", "api/mypage/login",
            }],
            "root_login_route": None,
            "root_login_route_status": "not bound by static literal evidence; api/mypage/login is a distinct route",
        },
        "limitations": [
            "Routes are client literals; HTTP verbs and DTO bindings require call-site verification.",
            "Interop declarations expose client shapes, not original native method bodies or server code.",
            "Interop backing fields are not independently verified wire serialization keys.",
            "No account credentials, signing-key values, or complete literal heap are exported.",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Export APK protocol evidence without executing game code.")
    parser.add_argument("--unpacked", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args(argv)
    try:
        protocol = export_protocol(arguments.unpacked)
        output = arguments.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(protocol, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (OSError, BinaryFormatError) as error:
        print(f"Protocol export failed: {error}", file=sys.stderr)
        return 1
    print(f"Exported {len(protocol['routes'])} routes and {len(protocol['types'])} types to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
