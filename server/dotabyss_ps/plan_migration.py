from __future__ import annotations

import hashlib
from pathlib import Path

import msgpack

from .masterdata import MAX_PAYLOAD_BYTES, canonical_json, inspect_payload
from .newgame import NewGameError
from .protocol import decode_json, validate_data


def load_plan_steps(data_dir: Path, metadata: dict, schema_path: Path | None) -> dict[int, list[int]]:
    if schema_path is None:
        raise NewGameError("Plan migration requires the active master schema (--schema)")
    schema = decode_json(schema_path.read_bytes())
    if hashlib.sha256(canonical_json(schema)).hexdigest() != metadata.get("schema_sha256"):
        raise NewGameError("Plan migration schema does not match the active master")
    path = (data_dir / metadata["payload_path"]).resolve(strict=True)
    if not path.is_relative_to(data_dir.resolve()) or path.stat().st_size > MAX_PAYLOAD_BYTES:
        raise NewGameError("Plan migration master path or size is invalid")
    payload = path.read_bytes()
    report = inspect_payload(payload, schema)
    if any(report[key] != metadata.get(key) for key in ("revision", "sha256", "bytes", "client")):
        raise NewGameError("Plan migration master integrity check failed")
    fields = {column["name"]: i for i, column in enumerate(schema["tables"].get("m_plan_steps", []))}
    if not {"m_plan_id", "step", "m_mission_id"} <= fields.keys():
        raise NewGameError("Plan migration requires bound plan mission identifiers")
    tables = msgpack.unpackb(payload, raw=False)
    groups = {}
    for row in tables["m_plan_steps"]:
        plan_id, step, mission_id = (row[fields[key]] for key in ("m_plan_id", "step", "m_mission_id"))
        if any(type(value) is not int or value < 1 for value in (plan_id, step, mission_id)):
            raise NewGameError("Plan migration master contains invalid steps")
        groups.setdefault(plan_id, []).append((step, mission_id))
    plans = {}
    for plan_id, steps in groups.items():
        steps.sort()
        if [step for step, _ in steps] != list(range(1, len(steps) + 1)) or len({mission for _, mission in steps}) != len(steps):
            raise NewGameError("Plan migration requires consecutive, unique plan steps")
        plans[plan_id] = [mission for _, mission in steps]
    return plans


def migrate_plan_progress(state: dict, progress: dict, plans: dict[int, list[int]]) -> None:
    claims = progress.get("mission_claims", {})
    if not isinstance(claims, dict) or any(not isinstance(rewards, list) for rewards in claims.values()):
        raise NewGameError("Plan migration mission receipts are invalid")
    owned = {}
    for row in state["records"]["t_plans"]["up"]:
        plan_id = row["m_plan_id"]
        if plan_id not in plans:
            raise NewGameError("Plan migration cannot resolve an owned plan")
        for mission in plans[plan_id]:
            if any(key.endswith(f":{mission}") and key != f"once:{mission}" for key in claims):
                raise NewGameError("Plan migration found ambiguous periodic mission receipts")
        claimed = [f"once:{mission}" in claims for mission in plans[plan_id]]
        completed = sum(claimed)
        if claimed != [True] * completed + [False] * (len(claimed) - completed) or row["step"] != completed + 1:
            raise NewGameError("Plan progress does not match consecutive mission receipts")
        row["step"] = completed
        owned[plan_id] = row
    pending = [progress]
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            if "t_plans" in item:
                container = item["t_plans"]
                if not isinstance(container, dict) or not isinstance(container.get("up"), list):
                    raise NewGameError("Cached plan response is invalid")
                for row in container["up"]:
                    if not isinstance(row, dict):
                        raise NewGameError("Cached plan response is invalid")
                    current = owned.get(row.get("m_plan_id"))
                    step = row.get("step")
                    if current is None or row.get("id") != current["id"] or type(step) is not int or not 1 <= step <= current["step"] + 1:
                        raise NewGameError("Cached plan progress conflicts with the saved plan")
                    row["step"] -= 1
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    validate_data(progress)
