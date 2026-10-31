from __future__ import annotations

from datetime import timedelta

from .gameplay_catalog import GameplayError, number


def login_data(progress):
    data = progress["features"].setdefault("login", {"version": 1, "data": {}})["data"]
    if not data:
        data.update(bonuses={}, subscription_days={}, friend_day="", history=[])
    if set(data) != {"bonuses", "subscription_days", "friend_day", "history"} or not isinstance(data["bonuses"], dict) or not isinstance(data["subscription_days"], dict) or not isinstance(data["history"], list):
        raise GameplayError("Stored login progress is invalid")
    return data


def bonus_entry(progress, identifier):
    entry = login_data(progress)["bonuses"].setdefault(str(number(identifier)), {
        "sheet_no": 1, "index": 0, "login_total": 0, "last_day": "", "paid_at": "", "limit_at": "", "last_received_at": ""})
    if not isinstance(entry, dict) or set(entry) != {"sheet_no", "index", "login_total", "last_day", "paid_at", "limit_at", "last_received_at"}:
        raise GameplayError("Stored login bonus is invalid")
    for key in ("sheet_no", "index", "login_total"):
        number(entry[key], 0, 2 ** 31 - 1)
    return entry


def grant_limited_login(progress, catalog, identifier, now):
    definition = catalog.get("m_login_bonuses", identifier)
    if definition["bonus_type"] not in (91, 92):
        raise GameplayError("Purchased login bonus must be a limited bonus")
    entry = bonus_entry(progress, identifier)
    from .gameplay_wallet import timestamp
    if entry["limit_at"] and timestamp(entry["limit_at"]) > now:
        raise GameplayError("This login bonus is already active")
    duration = definition["expire_days"] or 30
    entry.update(sheet_no=1, index=0, login_total=0, last_day="", last_received_at="", paid_at=now.isoformat(), limit_at=(now + timedelta(days=duration)).isoformat())
