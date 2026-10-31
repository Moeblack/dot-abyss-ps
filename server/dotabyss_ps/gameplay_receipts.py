from __future__ import annotations

import copy
import hashlib
import json
import re

from .gameplay_catalog import GameplayError
from .gameplay_records import identity_field
from .protocol import validate_data

FEATURE_NAMES = {"character", "equipment", "exchange", "gacha", "idle", "research", "tavern", "presents", "friends", "library", "party", "statistics", "missions", "mission_pass", "shop", "login", "home", "battle", "battle_rewards", "main_story", "tutorial", "nether", "disaster", "training", "encounter", "union", "side_story", "events", "feature_unlock"}


def validate_additional_progress(progress):
    receipts = progress["operation_receipts"]
    features = progress["features"]
    if not isinstance(receipts, dict) or len(receipts) > 64:
        raise GameplayError("Stored operation receipts are invalid")
    for key, value in receipts.items():
        validate_operation_id(key)
        if not isinstance(value, dict) or set(value) != {"digest", "response"} or not re.fullmatch(r"[0-9a-f]{64}", str(value["digest"])) or not isinstance(value["response"], dict):
            raise GameplayError("Stored operation receipt is invalid")
    if not isinstance(features, dict) or set(features) - FEATURE_NAMES:
        raise GameplayError("Stored feature state is invalid")
    for entry in features.values():
        if not isinstance(entry, dict) or set(entry) != {"version", "data"} or type(entry["version"]) is not int or entry["version"] != 1 or not isinstance(entry["data"], dict):
            raise GameplayError("Stored feature state requires migration")
    validate_data(receipts)
    validate_data(features)


def feature_data(game, name):
    if name not in FEATURE_NAMES:
        raise ValueError("Unknown feature state name")
    return game.progress["features"].setdefault(name, {"version": 1, "data": {}})["data"]


def validate_operation_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:\-]{1,128}", value):
        raise GameplayError("Invalid request identifier", 400, 4007)


def operation_digest(route, method, body):
    raw = json.dumps([route, method, body], ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def replay(progress, operation_id, digest):
    if not operation_id:
        return None
    validate_operation_id(operation_id)
    receipt = progress["operation_receipts"].get(operation_id)
    if receipt is None:
        return None
    if receipt["digest"] != digest:
        raise GameplayError("Request identifier was reused for different parameters", 409, 4094)
    return copy.deepcopy(receipt["response"])


def remember(progress, operation_id, digest, response):
    if not operation_id:
        return
    receipts = progress["operation_receipts"]
    receipts[operation_id] = {"digest": digest, "response": copy.deepcopy(response)}
    while len(receipts) > 64 or len(json.dumps(receipts, ensure_ascii=False).encode("utf-8")) > 768 * 1024:
        if len(receipts) == 1:
            raise GameplayError("Operation response is too large for replay protection", 413, 4130)
        receipts.pop(next(iter(receipts)))


def refresh_replay_records(state, response):
    result = copy.deepcopy(response)
    for table, changes in result.get("records", {}).items():
        if table not in state["records"]:
            continue
        identity = identity_field(table)
        current = {row[identity]: row for row in state["records"][table]["up"]}
        identifiers = list(dict.fromkeys([row[identity] for row in changes["up"]] + changes["down"]))
        changes["up"] = [copy.deepcopy(current[identifier]) for identifier in identifiers if identifier in current]
        changes["down"] = [identifier for identifier in identifiers if identifier not in current]
    return result
