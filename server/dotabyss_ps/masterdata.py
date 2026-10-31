from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import msgpack

from .storage import Store

MAX_PAYLOAD_BYTES = 64 * 1024 * 1024
INTEGER_RANGES = {"int": (-(2**31), 2**31 - 1), "long": (-(2**63), 2**63 - 1)}


class MasterDataError(ValueError):
    pass


def canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def unique_map(pairs: list[tuple]) -> dict:
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise MasterDataError("Master data map keys must be unique strings")
        result[key] = value
    return result


def validate_schema(schema: dict) -> None:
    if not isinstance(schema, dict) or schema.get("schema_version") != 1:
        raise MasterDataError("Unsupported master schema")
    client = schema.get("client")
    if not isinstance(client, dict) or any(not isinstance(client.get(key), str) or not client[key] for key in ("package", "version", "metadata_sha256")):
        raise MasterDataError("The schema must identify its client and metadata hash")
    if not re.fullmatch(r"[A-Za-z0-9_.]{1,200}", client["package"]) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", client["version"]):
        raise MasterDataError("Invalid client identity")
    if not re.fullmatch(r"[0-9a-f]{64}", client["metadata_sha256"]):
        raise MasterDataError("Invalid schema metadata hash")
    tables = schema.get("tables")
    if not isinstance(tables, dict) or not 1 <= len(tables) <= 2000:
        raise MasterDataError("Master schema must contain table definitions")
    for name, columns in tables.items():
        if not isinstance(name, str) or not re.fullmatch(r"m_[a-z0-9_]{1,126}", name) or not isinstance(columns, list) or not 1 <= len(columns) <= 256:
            raise MasterDataError("Invalid master table definition")
        names = set()
        for column in columns:
            if not isinstance(column, dict) or column.get("type") not in (*INTEGER_RANGES, "string"):
                raise MasterDataError(f"Unsupported column type in {name}")
            column_name = column.get("name")
            if not isinstance(column_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", column_name) or column_name in names:
                raise MasterDataError(f"Invalid column name in {name}")
            if "unique" in column and type(column["unique"]) is not bool:
                raise MasterDataError("Unique column constraints must be booleans")
            if column.get("unique") and not column.get("uniqueness_evidence"):
                raise MasterDataError("Unique column constraints require evidence")
            names.add(column_name)
    policy = schema.get("compatibility", {})
    if not isinstance(policy, dict):
        raise MasterDataError("Invalid compatibility policy")
    if policy.get("skip_unknown_tables") and not policy.get("unknown_table_evidence"):
        raise MasterDataError("Skipping unknown tables requires formatter evidence")
    tails = policy.get("defaulted_tails", {})
    if not isinstance(tails, dict) or type(policy.get("skip_unknown_tables", False)) is not bool:
        raise MasterDataError("Invalid compatibility policy")
    for name, entry in tails.items():
        if name not in tables or not isinstance(entry, dict) or not entry.get("evidence"):
            raise MasterDataError("Defaulted table tails require formatter evidence")
        count = entry.get("minimum_columns")
        if type(count) is not int or not 0 < count < len(tables[name]):
            raise MasterDataError("Invalid defaulted column boundary")


def inspect_payload(payload: bytes, schema: dict) -> dict:
    validate_schema(schema)
    if not 0 < len(payload) <= MAX_PAYLOAD_BYTES:
        raise MasterDataError("Master payload must be at most 64 MiB")
    try:
        tables = msgpack.unpackb(payload, raw=False, strict_map_key=True, object_pairs_hook=unique_map,
                                 max_map_len=2000, max_array_len=500000, max_str_len=16 * 1024 * 1024,
                                 max_bin_len=MAX_PAYLOAD_BYTES, max_ext_len=0)
    except (ValueError, TypeError, UnicodeError, msgpack.UnpackException) as exc:
        raise MasterDataError("Invalid master MessagePack payload") from exc
    if not isinstance(tables, dict):
        raise MasterDataError("Master payload must be a table map")
    definitions = schema["tables"]
    missing = sorted(set(definitions) - set(tables))
    if missing:
        raise MasterDataError("Missing required tables: " + ", ".join(missing[:12]))
    extra = sorted(set(tables) - set(definitions))
    policy = schema.get("compatibility", {})
    if extra and not policy.get("skip_unknown_tables"):
        raise MasterDataError("Unknown tables require a confirmed skip policy")
    counts, defaults, duplicate_ids = {}, {}, {}
    total = 0
    for name, rows in tables.items():
        if not re.fullmatch(r"m_[a-z0-9_]{1,126}", name):
            raise MasterDataError("Invalid master table name")
        if not isinstance(rows, list):
            raise MasterDataError(f"{name}: expected a row array")
        total += len(rows)
        if total > 500000:
            raise MasterDataError("Master payload exceeds 500000 rows")
        counts[name] = len(rows)
        columns = definitions.get(name)
        if columns is None:
            continue
        tail = policy.get("defaulted_tails", {}).get(name)
        minimum = tail["minimum_columns"] if tail else len(columns)
        seen_ids = set()
        unique_values = {index: set() for index, column in enumerate(columns) if column.get("unique")}
        id_index = next((index for index, column in enumerate(columns) if column["name"] == "id"), None)
        for row_index, row in enumerate(rows):
            if not isinstance(row, list) or not minimum <= len(row) <= len(columns):
                raise MasterDataError(f"{name}[{row_index}]: unexpected column count")
            if len(row) < len(columns):
                defaults[name] = defaults.get(name, 0) + 1
            for column, value in zip(columns, row):
                kind = column["type"]
                if kind in INTEGER_RANGES:
                    lower, upper = INTEGER_RANGES[kind]
                    valid = type(value) is int and lower <= value <= upper
                else:
                    valid = value is None or isinstance(value, str)
                    if isinstance(value, str):
                        value.encode("utf-8", errors="strict")
                if not valid:
                    raise MasterDataError(f"{name}[{row_index}].{column['name']}: expected {kind}")
            if id_index is not None:
                if id_index >= len(row):
                    raise MasterDataError(f"{name}[{row_index}]: id cannot be defaulted")
                identity = row[id_index]
                if identity in seen_ids:
                    duplicate_ids[name] = duplicate_ids.get(name, 0) + 1
                seen_ids.add(identity)
            for index, seen in unique_values.items():
                if index >= len(row) or row[index] in seen:
                    raise MasterDataError(f"{name}[{row_index}]: duplicate or absent unique key")
                seen.add(row[index])
    payload_hash = hashlib.sha256(payload).hexdigest()
    schema_hash = hashlib.sha256(canonical_json(schema)).hexdigest()
    return {
        "revision": f"local-{payload_hash[:16]}-{schema_hash[:12]}",
        "sha256": payload_hash,
        "schema_sha256": schema_hash,
        "client": schema["client"],
        "bytes": len(payload),
        "table_count": len(tables),
        "row_count": total,
        "required_table_count": len(definitions),
        "skipped_tables": extra,
        "defaulted_tail_rows": defaults,
        "duplicate_id_rows": duplicate_ids,
        "table_rows": counts,
        "payload_modified": False,
        "format_validated": True,
        "business_validated": False,
    }


class MasterCatalog:
    def __init__(self, data_dir: Path, store: Store):
        self.root = data_dir.resolve()
        self.store = store

    def _path(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise MasterDataError("Master data path leaves the data directory")
        return path

    def stage(self, payload_path: Path, schema_path: Path, source: str) -> dict:
        if not source.strip() or len(source) > 2048:
            raise MasterDataError("A bounded source description is required")
        source.encode("utf-8", errors="strict")
        if payload_path.stat().st_size > MAX_PAYLOAD_BYTES:
            raise MasterDataError("Master payload exceeds 64 MiB")
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        payload = payload_path.read_bytes()
        report = inspect_payload(payload, schema)
        report["source"] = source
        report["payload_path"] = f"master/blobs/{report['sha256']}.msgpack"
        path = self._path(report["payload_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            if hashlib.sha256(path.read_bytes()).hexdigest() != report["sha256"]:
                raise MasterDataError("An existing immutable master blob is corrupt")
        else:
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".partial") as file:
                    temporary = Path(file.name)
                    file.write(payload)
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary, path)
            finally:
                if temporary and temporary.exists():
                    temporary.unlink()
        self.store.save_master(report)
        return self.store.master(report["revision"])

    def checked(self, revision: str) -> tuple[dict, Path]:
        record, _ = self.read(revision)
        return record, self._path(record["payload_path"])

    def read(self, revision: str) -> tuple[dict, bytes]:
        record = self.store.master(revision)
        if record is None:
            raise MasterDataError("Unknown master revision")
        path = self._path(record["payload_path"])
        if not path.is_file() or path.stat().st_size != record["bytes"]:
            raise MasterDataError("Master payload is missing or truncated")
        with path.open("rb") as file:
            payload = file.read(MAX_PAYLOAD_BYTES + 1)
        if len(payload) != record["bytes"] or hashlib.sha256(payload).hexdigest() != record["sha256"]:
            raise MasterDataError("Master payload integrity check failed")
        return record, payload

    def activate(self, revision: str) -> dict:
        record, _ = self.checked(revision)
        self.store.activate_master(revision)
        return record


def main():
    parser = argparse.ArgumentParser(description="Inspect and stage immutable master data for the local server")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    commands = parser.add_subparsers(dest="command", required=True)
    inspect = commands.add_parser("inspect")
    stage = commands.add_parser("stage")
    for command in (inspect, stage):
        command.add_argument("--payload", type=Path, required=True)
        command.add_argument("--schema", type=Path, required=True)
    stage.add_argument("--source", required=True)
    activate = commands.add_parser("activate")
    activate.add_argument("revision")
    args = parser.parse_args()
    if args.command == "inspect":
        if args.payload.stat().st_size > MAX_PAYLOAD_BYTES:
            raise MasterDataError("Master payload exceeds 64 MiB")
        result = inspect_payload(args.payload.read_bytes(), json.loads(args.schema.read_text(encoding="utf-8")))
    else:
        catalog = MasterCatalog(args.data_dir, Store(args.data_dir / "local.sqlite3"))
        result = catalog.stage(args.payload, args.schema, args.source) if args.command == "stage" else catalog.activate(args.revision)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
