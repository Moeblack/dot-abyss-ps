from __future__ import annotations

import base64
import binascii
import copy
import json
import zlib
from functools import wraps

from .gameplay_catalog import GAMEPLAY_TABLES, GameplayError, number
from .gameplay_rules import validate_rules
from .protocol import decode_json

NETHER_TABLES = frozenset(name for name in GAMEPLAY_TABLES if name.startswith("m_nether"))
MAX_MASTER_BYTES = 4 * 1024 * 1024
MAX_CONFIGURATION_BYTES = 256 * 1024


def capture_configuration(game):
    tables = {name: game.catalog.rows(name) for name in sorted(NETHER_TABLES) if name in game.catalog.tables}
    encoded = json.dumps(tables, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_MASTER_BYTES:
        raise GameplayError("Nether master snapshot exceeds its size limit", 503, 5036)
    result = {"version": 1, "rules": copy.deepcopy(game.rules), "master": {
        "encoding": "zlib-base64", "bytes": len(encoded),
        "data": base64.b64encode(zlib.compress(encoded)).decode("ascii")}}
    if len(json.dumps(result, ensure_ascii=False).encode("utf-8")) > MAX_CONFIGURATION_BYTES:
        raise GameplayError("Nether configuration snapshot exceeds its size limit", 503, 5036)
    return result


def validate_configuration(configuration, catalog=None):
    if not isinstance(configuration, dict) or set(configuration) != {"version", "rules", "master"} or type(configuration["version"]) is not int or configuration["version"] != 1:
        raise GameplayError("Stored Nether configuration is invalid")
    if len(json.dumps(configuration, ensure_ascii=False, allow_nan=False).encode("utf-8")) > MAX_CONFIGURATION_BYTES:
        raise GameplayError("Stored Nether configuration exceeds its size limit")
    rules = validate_rules(configuration["rules"])
    master = configuration["master"]
    if not isinstance(master, dict) or set(master) != {"encoding", "bytes", "data"} or master["encoding"] != "zlib-base64" or not isinstance(master["data"], str):
        raise GameplayError("Stored Nether master snapshot is invalid")
    size = number(master["bytes"], 1, MAX_MASTER_BYTES)
    try:
        compressed = base64.b64decode(master["data"], validate=True)
        decoder = zlib.decompressobj()
        encoded = decoder.decompress(compressed, size + 1)
        if len(encoded) != size or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise ValueError("Snapshot length mismatch")
        tables = decode_json(encoded)
    except (ValueError, TypeError, zlib.error, binascii.Error) as error:
        raise GameplayError("Stored Nether master snapshot cannot be decoded") from error
    if not isinstance(tables, dict) or set(tables) - NETHER_TABLES or not {"m_nethers", "m_nether_maps"} <= set(tables):
        raise GameplayError("Stored Nether master tables are invalid")
    if catalog is not None and set(tables) != NETHER_TABLES.intersection(catalog.tables):
        raise GameplayError("Stored Nether master snapshot is incomplete")
    indexed = {}
    for name, values in tables.items():
        if not isinstance(values, list) or len(values) > 100000:
            raise GameplayError("Stored Nether master rows are invalid")
        source = catalog.rows(name) if catalog is not None else []
        fields = set(source[0]) if source else None
        types = {field: {type(row[field]) for row in source} for field in fields} if fields else {}
        rows = {}
        for row in values:
            if not isinstance(row, dict) or "id" not in row:
                raise GameplayError("Stored Nether master row is invalid")
            if fields is not None and (set(row) != fields or any(type(row[field]) not in types[field] for field in fields)):
                raise GameplayError("Stored Nether master row does not match its schema")
            identifier = number(row["id"])
            if identifier in rows:
                raise GameplayError("Stored Nether master identifiers are duplicated")
            rows[identifier] = row
        indexed[name] = rows
    return rules, indexed


def freeze_configuration(game):
    feature = game.progress.get("features", {}).get("nether", {})
    expedition = feature.get("data", {}).get("run")
    if not isinstance(expedition, dict) or expedition.get("settled"):
        return None
    if "configuration" not in expedition:
        expedition["configuration"] = capture_configuration(game)
    if not isinstance(expedition["configuration"], dict):
        raise GameplayError("Stored Nether configuration is invalid")
    return expedition["configuration"]


def frozen_game(game, *, include_settled=False):
    if getattr(game, "_nether_configuration_active", False):
        return game
    configuration = freeze_configuration(game)
    if configuration is None and include_settled:
        expedition = game.progress.get("features", {}).get("nether", {}).get("data", {}).get("run")
        configuration = expedition.get("configuration") if isinstance(expedition, dict) else None
    if configuration is None:
        return game
    cached = getattr(game, "_nether_configuration_cache", None)
    if cached is not None and cached[0] is configuration:
        cached[1].now = game.now
        return cached[1]
    rules, tables = validate_configuration(configuration, game.catalog)
    scoped = copy.copy(game)
    scoped.rules = rules
    scoped.catalog = copy.copy(game.catalog)
    scoped.catalog.tables = {**game.catalog.tables, **tables}
    scoped._nether_configuration_active = True
    game._nether_configuration_cache = configuration, scoped
    return scoped


def frozen_nether(function):
    @wraps(function)
    def invoke(game, *args, **kwargs):
        return function(frozen_game(game), *args, **kwargs)
    return invoke
