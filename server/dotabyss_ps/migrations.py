from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path

from .newgame import POLICY_VERSION, PROLOGUE_COMPLETED, SUPPORTED_CLIENTS, NewGameError, dormant_exploration_values, dormant_tavern_values, validate_saved_state
from .protocol import decode_json, validate_data
from .plan_migration import load_plan_steps, migrate_plan_progress

TARGET_POLICY_VERSION = 5


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _write_backup(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(document, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")
    try:
        with path.open("xb") as backup:
            backup.write(encoded)
            backup.flush()
            os.fsync(backup.fileno())
    except FileExistsError:
        with path.open("r+b") as backup:
            if path.stat().st_size != len(encoded) or backup.read(len(encoded) + 1) != encoded:
                raise NewGameError("An existing migration backup does not match this save")
            os.fsync(backup.fileno())


def _add_prologue(state: dict, profile_id: int, context=None) -> str:
    rows = state["records"]["t_prologue_tutorials"]["up"]
    if rows:
        return "preserve_tutorial_progress"
    rows.append({"id": 1, "user_id": profile_id, "status": PROLOGUE_COMPLETED})
    return "add_completed_tutorial"


def _add_tavern(state: dict, profile_id: int, context=None) -> str:
    rows = state["records"]["t_tavern_daily_cards"]["up"]
    if rows:
        return "preserve_tavern_progress"
    rows.append({"id": 1, "user_id": profile_id, **dormant_tavern_values()})
    return "add_dormant_tavern"


def _add_idle_exploration(state: dict, profile_id: int, context=None) -> str:
    rows = state["records"]["t_idle_exploration_statuses"]["up"]
    if rows:
        return "preserve_idle_exploration_progress"
    rows.append({"id": 1, "user_id": profile_id, **dormant_exploration_values()})
    return "add_dormant_idle_exploration"


def _correct_plan_progress(state: dict, profile_id: int, context) -> str:
    if context is None:
        raise NewGameError("Plan migration requires the active master and mission receipts")
    plans, progress = context
    migrate_plan_progress(state, progress, plans)
    return "correct_completed_plan_steps"


MIGRATION_STEPS = {(1, 2): _add_prologue, (2, 3): _add_tavern, (3, 4): _add_idle_exploration,
                   (4, 5): _correct_plan_progress}


def _candidate(row: sqlite3.Row, profile_id: int, target_policy: int, context=None) -> tuple[dict, list[dict], int]:
    if row["schema_version"] != 1:
        raise NewGameError("Stored game state requires a separate schema migration")
    state = decode_json(row["contents"])
    if not isinstance(state, dict) or type(state.get("policy_version")) is not int or state["policy_version"] not in (1, 2, 3, 4, 5):
        raise NewGameError("Unsupported source policy for migration")
    validate_saved_state(state, policy_version=state["policy_version"])
    owners = {item["user_id"] for container in state["records"].values() for item in container["up"] if "user_id" in item}
    if owners != {profile_id}:
        raise NewGameError("Stored game state does not belong to the selected profile")
    source_policy = state["policy_version"]
    if source_policy > target_policy:
        raise NewGameError("Policy downgrades are not supported")
    candidate = copy.deepcopy(state)
    actions = []
    while candidate["policy_version"] < target_policy:
        current = candidate["policy_version"]
        step = MIGRATION_STEPS.get((current, current + 1))
        if step is None:
            raise NewGameError("No registered migration connects these policies")
        action = step(candidate, profile_id, context)
        candidate["policy_version"] = current + 1
        validate_saved_state(candidate, policy_version=current + 1)
        validate_data(candidate)
        actions.append({"from_policy": current, "to_policy": current + 1, "action": action})
    return candidate, actions, source_policy


def migrate_profile(database: Path, profile_name: str, *, apply: bool = False, target_policy: int = TARGET_POLICY_VERSION,
                    schema_path: Path | None = None) -> dict:
    if POLICY_VERSION != TARGET_POLICY_VERSION:
        raise NewGameError("Migration steps must be reviewed for the current local policy")
    if type(target_policy) is not int or target_policy not in (2, 3, 4, 5):
        raise NewGameError("Unsupported target policy for migration")
    database = database.resolve(strict=True)
    if not database.is_file():
        raise NewGameError("A local state database is required")
    with closing(sqlite3.connect(database.as_uri() + ("?mode=rw" if apply else "?mode=ro"), uri=True, timeout=10)) as db, db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("BEGIN IMMEDIATE" if apply else "BEGIN")
        profile = db.execute("SELECT id,uuid FROM profiles WHERE name=?", (profile_name,)).fetchone()
        if profile is None:
            raise NewGameError("The selected local profile does not exist")
        row = db.execute("SELECT * FROM game_states WHERE profile_id=?", (profile["id"],)).fetchone()
        if row is None:
            raise NewGameError("The selected local profile has no stored game state")
        active = db.execute("SELECT value FROM settings WHERE name='active_master'").fetchone()
        runtime = db.execute("SELECT value FROM settings WHERE name='runtime'").fetchone()
        dataset = db.execute("SELECT metadata FROM master_datasets WHERE revision=?", (row["master_revision"],)).fetchone()
        if active is None or active["value"] != row["master_revision"] or runtime is None or dataset is None:
            raise NewGameError("The saved master revision must match the active dataset")
        runtime_data, metadata = decode_json(runtime["value"]), decode_json(dataset["metadata"])
        if not isinstance(runtime_data, dict) or runtime_data.get("resource_version") != row["master_revision"]:
            raise NewGameError("Runtime resource version does not match the saved master revision")
        if not isinstance(metadata, dict) or metadata.get("revision") != row["master_revision"] or metadata.get("client") not in SUPPORTED_CLIENTS:
            raise NewGameError("The saved master dataset does not identify a supported client")
        source = decode_json(row["contents"])
        needs_plan_migration = (target_policy == 5 and isinstance(source, dict)
                                and type(source.get("policy_version")) is int and source["policy_version"] < 5)
        progress_row, progress, context = None, None, None
        if needs_plan_migration:
            progress_row = db.execute("SELECT * FROM gameplay_progress WHERE profile_id=?", (profile["id"],)).fetchone()
            if progress_row is not None and progress_row["master_revision"] != row["master_revision"]:
                raise NewGameError("Gameplay progress is bound to a different master revision")
            progress = decode_json(progress_row["contents"]) if progress_row else {}
            if not isinstance(progress, dict):
                raise NewGameError("Stored gameplay progress is invalid")
            if progress:
                from datetime import datetime
                from .gameplay import prepare_progress
                from .gameplay_catalog import JST

                prepare_progress(copy.deepcopy(progress), datetime.now(JST))
            context = (load_plan_steps(database.parent, metadata, schema_path), progress)
        candidate, actions, source_policy = _candidate(row, profile["id"], target_policy, context)
        action = "unchanged" if not actions else actions[0]["action"] if len(actions) == 1 else "apply_migration_chain"
        before = _digest(row["contents"])
        encoded = row["contents"] if action == "unchanged" else json.dumps(candidate, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode("utf-8")) > 2 * 1024 * 1024:
            raise NewGameError("Migrated game state exceeds 2 MiB")
        progress_before = progress_row["contents"] if progress_row else None
        progress_after = json.dumps(progress, ensure_ascii=False, allow_nan=False) if progress_row else None
        if progress_row and progress == decode_json(progress_before):
            progress_after = progress_before
        progress_changed = progress_before != progress_after
        if progress_after is not None and len(progress_after.encode("utf-8")) > 2 * 1024 * 1024:
            raise NewGameError("Migrated gameplay progress exceeds 2 MiB")
        progress_suffix = "-" + _digest(progress_before or "") if needs_plan_migration else ""
        backup = database.parent / "migration-backups" / f"policy-{source_policy}-to-{target_policy}-profile-{profile['id']}-{before}{progress_suffix}.json"
        result = {"status": "unchanged" if action == "unchanged" else "ready", "profile_id": profile["id"],
                  "master_revision": row["master_revision"], "from_policy": source_policy, "to_policy": target_policy,
                  "action": action, "actions": actions, "before_sha256": before, "after_sha256": _digest(encoded)}
        if needs_plan_migration:
            result.update(progress_before_sha256=_digest(progress_before) if progress_before is not None else None,
                          progress_after_sha256=_digest(progress_after) if progress_after is not None else None,
                          progress_changed=progress_changed)
        if apply and action != "unchanged":
            backup_document = {"format_version": 1, "migration": f"local-policy-{source_policy}-to-{target_policy}",
                               "profile_uuid_sha256": _digest(profile["uuid"]), "before_sha256": before,
                               "after_sha256": _digest(encoded), "game_state": dict(row)}
            if needs_plan_migration:
                backup_document.update(gameplay_progress=dict(progress_row) if progress_row else None,
                                       progress_before_sha256=result["progress_before_sha256"],
                                       progress_after_sha256=result["progress_after_sha256"])
            _write_backup(backup, backup_document)
            updated = db.execute("UPDATE game_states SET contents=? WHERE profile_id=? AND schema_version=1 AND master_revision=? AND contents=?",
                                 (encoded, profile["id"], row["master_revision"], row["contents"]))
            if updated.rowcount != 1:
                raise NewGameError("The selected save changed before migration could be committed")
            if progress_changed:
                updated = db.execute("UPDATE gameplay_progress SET contents=? WHERE profile_id=? AND master_revision=? AND contents=?",
                                     (progress_after, profile["id"], row["master_revision"], progress_before))
                if updated.rowcount != 1:
                    raise NewGameError("Gameplay progress changed before migration could be committed")
            db.commit()
            result.update(status="applied", backup=str(backup))
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Preview or apply a backed-up local player policy migration")
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--to-policy", type=int, choices=(2, 3, 4, 5), default=TARGET_POLICY_VERSION)
    parser.add_argument("--schema", type=Path)
    args = parser.parse_args()
    try:
        result = migrate_profile(args.data_dir / "local.sqlite3", args.profile, apply=args.apply,
                                 target_policy=args.to_policy, schema_path=args.schema)
    except (NewGameError, OSError, sqlite3.Error, ValueError, TypeError, RecursionError) as error:
        parser.exit(1, f"Migration failed ({type(error).__name__}): {error}\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
