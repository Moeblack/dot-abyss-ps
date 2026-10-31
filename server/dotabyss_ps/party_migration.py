from __future__ import annotations

import argparse
import copy
import hashlib
import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

from .gameplay import prepare_progress
from .gameplay_catalog import JST
from .migrations import _write_backup
from .newgame import NewGameError, ROW_FIELDS, SUPPORTED_CLIENTS, validate_saved_state
from .protocol import decode_json, validate_data
from .storage import Store

TABLE = "t_exploration_party_characters"
GEAR_FIELDS = ("t_weapon_id", "t_armor_id", "t_accessory_id")


def _digest(value):
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def _row(row, profile_id, *, stored=False):
    if not isinstance(row, dict):
        raise NewGameError("Stored party member is invalid")
    for key in ("id", "t_character_id", *GEAR_FIELDS):
        value = row.get(key)
        if type(value) is not int or not 0 <= value < 2 ** 63 - 1 or key == "id" and not value:
            raise NewGameError("Stored party member identifiers are invalid")
    if "user_id" in row and row["user_id"] != profile_id:
        raise NewGameError("Party member belongs to another profile")
    if stored:
        fields = {name: kind for kind, names in ROW_FIELDS[TABLE].items() for name in names.split()}
        if set(row) != fields.keys():
            raise NewGameError("Stored party member fields are invalid")
        if any(type(row[name]) is not int or not 0 <= row[name] < 2 ** (31 if kind == "int" else 63)
               for name, kind in fields.items()):
            raise NewGameError("Stored party member fields are invalid")
        if row["t_exploration_party_id"] < 1 or not 1 <= row["position_no"] <= 10:
            raise NewGameError("Stored party position is invalid")
    if not row["t_character_id"] and any(row[key] for key in GEAR_FIELDS):
        raise NewGameError("Empty party member still references equipment")
    return row["id"], row["t_character_id"] == 0


def clean_party_records(state, progress, profile_id):
    validate_data(state)
    validate_data(progress)
    if not isinstance(progress, dict):
        raise NewGameError("Stored gameplay progress is invalid")
    if progress:
        prepare_progress(copy.deepcopy(progress), datetime.now(JST))
    members = state["records"][TABLE]["up"]
    parents = {row["id"]: kind for table, kind in (("t_exploration_parties", 1), ("t_preset_exploration_parties", 2))
               for row in state["records"][table]["up"]}
    maximum, removed, retained, positions = 0, set(), set(), set()
    for member in members:
        identifier, empty = _row(member, profile_id, stored=True)
        position = member["t_exploration_party_id"], member["position_no"]
        kind = parents.get(position[0])
        if kind is None or member["party_type"] not in ((0, 1) if kind == 1 else (2,)):
            raise NewGameError("Stored party member parent is invalid")
        if identifier in removed | retained or position in positions:
            raise NewGameError("Stored party members are duplicated")
        positions.add(position)
        (removed if empty else retained).add(identifier)
        maximum = max(maximum, identifier)
    members[:] = [row for row in members if row["id"] not in removed]
    validate_saved_state(state, policy_version=state["policy_version"])
    owners = {row["user_id"] for value in state["records"].values() for row in value["up"] if "user_id" in row}
    if owners != {profile_id}:
        raise NewGameError("Stored game state belongs to another profile")

    cached_removed = 0
    pending = [receipt["response"] for receipt in progress.get("operation_receipts", {}).values()]
    payments = progress.get("features", {}).get("shop", {}).get("data", {}).get("receipts", {})
    if not isinstance(payments, dict) or any(not isinstance(receipt, dict) for receipt in payments.values()):
        raise NewGameError("Stored payment receipts are invalid")
    pending.extend(receipt[key] for receipt in payments.values() for key in ("response", "result") if key in receipt)
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if TABLE in value:
                container = value[TABLE]
                entries = container.get("up") if isinstance(container, dict) else container
                if not isinstance(entries, list):
                    raise NewGameError("Cached party member collection is invalid")
                deleted, seen, keep = set(), set(), []
                for entry in entries:
                    identifier, empty = _row(entry, profile_id)
                    if identifier in seen:
                        raise NewGameError("Cached party member identifiers are duplicated")
                    seen.add(identifier)
                    maximum = max(maximum, identifier)
                    if empty or identifier in removed:
                        deleted.add(identifier)
                        cached_removed += 1
                    else:
                        keep.append(entry)
                entries[:] = keep
                if isinstance(container, dict):
                    down = container.get("down")
                    if down is not None and (not isinstance(down, list) or any(type(v) is not int or not 1 <= v < 2 ** 63 - 1 for v in down)):
                        raise NewGameError("Cached party deletion identifiers are invalid")
                    maximum = max(maximum, max(down or [], default=0))
                    if deleted or set(down or []) & retained:
                        container["down"] = sorted((set(down or []) | deleted) - retained)
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)

    if maximum:
        if maximum >= 2 ** 63 - 2:
            raise NewGameError("Party identifier counter is exhausted")
        days = copy.deepcopy(progress.get("login_days", []))
        absent = not progress
        prepare_progress(progress, datetime.now(JST))
        progress["login_days"] = days
        if absent:
            progress["features"]["presents"] = {"version": 1, "data": {"seeded": True, "entries": []}}
        party = progress["features"].setdefault("party", {"version": 1, "data": {}})["data"]
        cursors = party.setdefault("next_ids", {})
        if not isinstance(cursors, dict):
            raise NewGameError("Stored party identifier counters are invalid")
        current = cursors.get(TABLE, 1)
        if type(current) is not int or not 1 <= current < 2 ** 63 - 1:
            raise NewGameError("Stored party identifier counter is invalid")
        cursors[TABLE] = max(current, maximum + 1)
    if progress:
        prepare_progress(copy.deepcopy(progress), datetime.now(JST))
    validate_data(progress)
    return {"removed_record_ids": sorted(removed), "cached_rows_removed": cached_removed,
            "next_member_id": progress.get("features", {}).get("party", {}).get("data", {}).get("next_ids", {}).get(TABLE)}


def clean_profile(database: Path, profile_name: str, *, apply=False):
    database = database.resolve(strict=True)
    with closing(sqlite3.connect(database.as_uri() + ("?mode=rw" if apply else "?mode=ro"), uri=True, timeout=10)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        profile = db.execute("SELECT id FROM profiles WHERE name=?", (profile_name,)).fetchone()
        if profile is None:
            raise NewGameError("The selected local profile does not exist")
        row = db.execute("SELECT * FROM game_states WHERE profile_id=?", (profile["id"],)).fetchone()
        if row is None or row["schema_version"] != 1:
            raise NewGameError("The selected profile requires a supported stored game state")
        active = db.execute("SELECT value FROM settings WHERE name='active_master'").fetchone()
        runtime = db.execute("SELECT value FROM settings WHERE name='runtime'").fetchone()
        dataset = db.execute("SELECT metadata FROM master_datasets WHERE revision=?", (row["master_revision"],)).fetchone()
        if active is None or active["value"] != row["master_revision"] or runtime is None or dataset is None:
            raise NewGameError("Saved and active master revisions do not match")
        metadata = decode_json(dataset["metadata"])
        if decode_json(runtime["value"]).get("resource_version") != row["master_revision"] or metadata.get("revision") != row["master_revision"] or metadata.get("client") not in SUPPORTED_CLIENTS:
            raise NewGameError("Stored game state master binding is invalid")
        progress_row = db.execute("SELECT * FROM gameplay_progress WHERE profile_id=?", (profile["id"],)).fetchone()
        if progress_row and progress_row["master_revision"] != row["master_revision"]:
            raise NewGameError("Gameplay progress is bound to another master revision")
        original_state = decode_json(row["contents"])
        original_progress = decode_json(progress_row["contents"]) if progress_row else {}
        state, progress = copy.deepcopy(original_state), copy.deepcopy(original_progress)
        result = clean_party_records(state, progress, profile["id"])
        changed = state != original_state or progress != original_progress
        state_text = Store._state_json(state) if state != original_state else row["contents"]
        old_progress_text = progress_row["contents"] if progress_row else None
        progress_text = Store._state_json(progress) if progress != original_progress else old_progress_text
        backup = database.parent / "migration-backups" / f"party-empty-slots-profile-{profile['id']}-{_digest(row['contents'])}-{_digest(old_progress_text)}.json"
        result.update(status="ready" if changed else "unchanged", profile_id=profile["id"],
                      before_sha256=_digest(row["contents"]), after_sha256=_digest(state_text),
                      progress_before_sha256=_digest(old_progress_text), progress_after_sha256=_digest(progress_text))
        if apply and changed:
            _write_backup(backup, {"format_version": 1, "migration": "party-empty-slots", "game_state": dict(row),
                                   "gameplay_progress": dict(progress_row) if progress_row else None, "result": result})
            updated = db.execute("UPDATE game_states SET contents=? WHERE profile_id=? AND contents=?",
                                 (state_text, profile["id"], row["contents"]))
            if updated.rowcount != 1:
                raise NewGameError("Stored game state changed during cleanup")
            if progress_text != old_progress_text:
                if progress_row:
                    updated = db.execute("UPDATE gameplay_progress SET contents=? WHERE profile_id=? AND contents=?",
                                         (progress_text, profile["id"], old_progress_text))
                    if updated.rowcount != 1:
                        raise NewGameError("Gameplay progress changed during cleanup")
                else:
                    db.execute("INSERT INTO gameplay_progress VALUES (?,?,?)", (profile["id"], row["master_revision"], progress_text))
            result.update(status="applied", backup=str(backup))
        return result


def main():
    parser = argparse.ArgumentParser(description="Remove legacy empty party records with an atomic backup")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        result = clean_profile(args.data_dir / "local.sqlite3", args.profile, apply=args.apply)
    except (ValueError, TypeError, KeyError, OSError, sqlite3.Error) as error:
        parser.exit(1, f"Party cleanup failed ({type(error).__name__}): {error}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
