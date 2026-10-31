from __future__ import annotations

from .gameplay_catalog import GameplayError, number


def pass_state(progress, identifier):
    data = progress["features"].setdefault("mission_pass", {"version": 1, "data": {}})["data"]
    record = data.setdefault(str(number(identifier)), {"points": 0, "premium": 0, "loop_received": 0, "rewards": {}})
    if not isinstance(record, dict) or set(record) != {"points", "premium", "loop_received", "rewards"} or not isinstance(record["rewards"], dict):
        raise GameplayError("Stored mission pass state is invalid")
    number(record["points"], 0, 2 ** 31 - 1)
    number(record["premium"], 0, 1)
    number(record["loop_received"], 0, 2 ** 31 - 1)
    for key, flags in record["rewards"].items():
        if not isinstance(key, str) or not key.isascii() or not key.isdecimal() or not isinstance(flags, dict) or set(flags) != {"normal", "premium"}:
            raise GameplayError("Stored pass level receipts are invalid")
        if any(type(value) is not bool for value in flags.values()):
            raise GameplayError("Stored pass reward flag is invalid")
    return record
