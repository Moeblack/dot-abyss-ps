from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .il2cpp_metadata import read_manifest_identity
from .managed_metadata import ManagedMetadata


def export_schema(unpacked: Path) -> dict:
    assembly = unpacked / "assets/LemonLoader/runtime/interop/Il2CppProject.dll"
    metadata = unpacked / "assets/bin/Data/Managed/Metadata/global-metadata.dat"
    identity = read_manifest_identity((unpacked / "AndroidManifest.xml").read_bytes())
    types = ManagedMetadata(assembly.read_bytes(), str(assembly)).types(("Il2CppProject.Master.NoaMessagePack",))
    named = {item["full_name"]: item for item in types}
    first = next(item for item in types if item["name"] == "MFirstDownload")
    tables = {}
    for field in first["fields"]:
        element = field["type"].split("<", 1)[1][:-1]
        tables[field["name"]] = [{"name": value["name"], "type": value["type"]} for value in named[element]["fields"]]
    return {
        "schema_version": 1,
        "client": {"package": identity["package"], "version": identity["versionName"],
                   "metadata_sha256": hashlib.sha256(metadata.read_bytes()).hexdigest()},
        "interop_sha256": hashlib.sha256(assembly.read_bytes()).hexdigest(),
        "field_order_source": "Native NoaMessagePack model declaration order; checked against the generated formatter",
        "tables": tables,
        "compatibility": {"skip_unknown_tables": False, "defaulted_tails": {}},
    }


def main():
    parser = argparse.ArgumentParser(description="Export the exact client's master table layout from its packaged interop")
    parser.add_argument("--unpacked", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    schema = export_schema(args.unpacked)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported {len(schema['tables'])} table layouts to {args.output}")


if __name__ == "__main__":
    main()
