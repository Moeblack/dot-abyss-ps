from __future__ import annotations

from datetime import timedelta

from .gameplay_catalog import GameplayError, number, parse_timestamp
from .gameplay_records import find, put


def daily_anchor(now):
    day = now - timedelta(hours=5)
    return day.replace(hour=5, minute=0, second=0, microsecond=0)


def daily_supply(game, kind, capacity):
    capacity = number(capacity, 0, 100000)
    anchor = daily_anchor(game.now)
    record = find(game.state, "t_timed_parameters", type=kind)
    if record is None:
        return put(game.state, "t_timed_parameters", {"type": kind}, {
            "amount": capacity, "capacity": capacity, "recovery": capacity, "duration_type": 2,
            "recovery_duration": 1, "last_recovery_at": anchor.isoformat()}, game.user_id)
    if parse_timestamp(record["last_recovery_at"]) < anchor:
        record.update(amount=capacity, last_recovery_at=anchor.isoformat())
    record.update(capacity=capacity, recovery=capacity, duration_type=2, recovery_duration=1)
    return record


def spend_supply(game, kind, capacity, amount=1):
    record = daily_supply(game, kind, capacity)
    amount = number(amount, 1, 100000)
    if record["amount"] < amount:
        raise GameplayError("Insufficient daily battle attempts", 409, 4093)
    record["amount"] -= amount
    return record
