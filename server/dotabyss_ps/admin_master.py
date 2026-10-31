from __future__ import annotations

import copy
import hashlib
import json
import os
import tempfile
from pathlib import Path

from .masterdata import INTEGER_RANGES, MasterDataError, canonical_json


def fingerprint(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def read_overrides(path: Path):
    if not path.exists():
        return {"format_version": 1, "patches": []}
    if path.stat().st_size > 2 * 1024 * 1024:
        raise MasterDataError("Master overrides exceed 2 MiB")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as error:
        raise MasterDataError("Invalid master overrides") from error
    if (not isinstance(data, dict) or data.get("format_version") != 1
            or not isinstance(data.get("patches"), list) or len(data["patches"]) > 10000):
        raise MasterDataError("Unsupported master overrides")
    return data


def atomic_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".partial", delete=False) as stream:
            temporary = stream.name
            stream.write(canonical_json(data))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def reference_table(field, schema):
    if not field.startswith("m_") or not field.endswith("_id"):
        return None
    stem = field[:-3]
    names = (stem + "s", stem + "es", stem[:-1] + "ies" if stem.endswith("y") else "")
    return next((name for name in names if name in schema["tables"]), None)


def editable_field(field, schema):
    name = field["name"]
    if name == "id" or field.get("unique") or name.startswith(("unknown", "column_", "field_")):
        return False
    if name.endswith("_id"):
        return reference_table(name, schema) is not None
    return True


def validate_value(field, value, tables, schema):
    name, kind = field["name"], field["type"]
    if not editable_field(field, schema):
        raise MasterDataError("This master field is read-only: " + name)
    if kind in INTEGER_RANGES:
        lower, upper = INTEGER_RANGES[kind]
        if type(value) is not int or not lower <= value <= upper:
            raise MasterDataError("Invalid integer value: " + name)
    elif value is not None and (not isinstance(value, str) or len(value) > 16384):
        raise MasterDataError("Invalid string value: " + name)
    if kind == "string" and name.endswith("_at") and value:
        parse_date(value)
    target = reference_table(name, schema)
    if target and value:
        keys = [field["name"] for field in schema["tables"][target]]
        if "id" not in keys or value not in {row[keys.index("id")] for row in tables.get(target, [])}:
            raise MasterDataError("Master reference does not exist: " + name)


def parse_date(value):
    from .gameplay_catalog import parse_timestamp
    try:
        return parse_timestamp(value)
    except (ValueError, TypeError, AttributeError) as error:
        raise MasterDataError("Invalid master date") from error


def validate_dates(row, fields):
    values = dict(zip((field["name"] for field in fields), row))
    for start, end in (("start_at", "end_at"), ("open_at", "close_at"), ("begin_at", "end_at"), ("open_at", "end_at")):
        if values.get(start) and values.get(end) and parse_date(values[start]) > parse_date(values[end]):
            raise MasterDataError("Master start date must not follow its end date")


def apply_overrides(tables, schema, record, config):
    if not config.get("patches"):
        return tables, {}
    if config.get("base_sha256") != record["sha256"] or config.get("schema_sha256") != record["schema_sha256"]:
        raise MasterDataError("Master overrides belong to another data version; review them before loading")
    result, counts, seen = dict(tables), {}, set()
    for patch in config["patches"]:
        if not isinstance(patch, dict):
            raise MasterDataError("Invalid master override entry")
        table, index = patch.get("table"), patch.get("index")
        if table not in schema["tables"] or table not in tables or type(index) is not int or not 0 <= index < len(tables[table]):
            raise MasterDataError("Master override row is missing")
        if (table, index) in seen:
            raise MasterDataError("Duplicate master override row")
        seen.add((table, index))
        original = tables[table][index]
        if fingerprint(original) != patch.get("original"):
            raise MasterDataError("Master override source row has changed")
        changes = patch.get("changes")
        if not isinstance(changes, dict) or not changes:
            raise MasterDataError("Empty master override")
        columns = schema["tables"][table]
        fields = {field["name"]: (offset, field) for offset, field in enumerate(columns)}
        row = list(original)
        for name, value in changes.items():
            if name not in fields:
                raise MasterDataError("Unknown master field: " + name)
            offset, field = fields[name]
            if offset >= len(row):
                raise MasterDataError("Defaulted master tail fields require explicit schema support")
            validate_value(field, value, tables, schema)
            row[offset] = value
        validate_dates(row, columns)
        if result[table] is tables[table]:
            result[table] = list(tables[table])
        result[table][index] = row
        counts[table] = counts.get(table, 0) + 1
    return result, counts


def patch_config(config, tables, schema, record, table, index, changes):
    if table not in schema["tables"] or table not in tables or type(index) is not int or not 0 <= index < len(tables[table]):
        raise MasterDataError("Unknown master row")
    if not isinstance(changes, dict) or not changes:
        raise MasterDataError("No master changes were supplied")
    apply_overrides(tables, schema, record, config)
    result = copy.deepcopy(config)
    result.update(base_sha256=record["sha256"], schema_sha256=record["schema_sha256"])
    existing = next((row for row in result["patches"] if row["table"] == table and row["index"] == index), None)
    if existing is None:
        existing = {"table": table, "index": index, "original": fingerprint(tables[table][index]), "changes": {}}
        result["patches"].append(existing)
    existing["changes"].update(changes)
    fields = [field["name"] for field in schema["tables"][table]]
    existing["changes"] = {name: value for name, value in existing["changes"].items()
                           if name not in fields or fields.index(name) >= len(tables[table][index]) or tables[table][index][fields.index(name)] != value}
    if not existing["changes"]:
        result["patches"].remove(existing)
    apply_overrides(tables, schema, record, result)
    return result
