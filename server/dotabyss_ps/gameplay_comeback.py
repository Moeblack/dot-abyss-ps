from __future__ import annotations

from datetime import date, timedelta

from .gameplay_catalog import available, number, parse_timestamp
from .gameplay_records import find, put


def active_record(game, identifier):
    definition = game.catalog.get("m_come_backs", identifier)
    record = find(game.state, "t_come_backs", m_come_back_id=identifier)
    if record is None or not available(definition, game.now, "begin_at"):
        return None
    if parse_timestamp(record["come_back_at"]) <= game.now < parse_timestamp(record["end_at"]):
        return record
    return None


def linked_window(game, table, field, identifier):
    if table not in game.catalog.tables:
        return True, None
    relations = [row for row in game.catalog.rows(table) if row[field] == identifier]
    if not relations:
        return True, None
    records = [record for row in relations if (record := active_record(game, row["m_come_back_id"])) is not None]
    return bool(records), max(records, key=lambda row: parse_timestamp(row["end_at"])) if records else None


def lineup_allowed(game, identifier):
    return linked_window(game, "m_come_back_payment_lineups", "m_payment_lineup_id", identifier)[0]


def special_window(game, identifier):
    return linked_window(game, "m_come_back_special_missions", "m_special_mission_id", identifier)


def reconcile_comeback(game):
    if "m_come_backs" not in game.catalog.tables:
        return
    minimum = game.rules.get("comeback_inactive_days", 30)
    if not minimum:
        return
    today = game.now.date()
    history = [date.fromisoformat(day) for day in game.progress["login_days"]]
    if any(day > today for day in history):
        return
    previous = max((day for day in history if day < today), default=None)
    if previous is None or (today - previous).days < minimum:
        return
    for definition in game.catalog.rows("m_come_backs"):
        if not available(definition, game.now, "begin_at") or active_record(game, definition["id"]) is not None:
            continue
        old = find(game.state, "t_come_backs", m_come_back_id=definition["id"])
        if old is not None and parse_timestamp(old["come_back_at"]).date() >= today:
            continue
        days = number(definition["limit_days"], 1, 3650)
        end = min(game.now + timedelta(days=days), parse_timestamp(definition["end_at"]))
        now = game.now.strftime("%Y-%m-%d %H:%M:%S")
        put(game.state, "t_come_backs", {"m_come_back_id": definition["id"]}, {
            "come_back_at": now, "end_at": end.strftime("%Y-%m-%d %H:%M:%S"),
            "created_at": old["created_at"] if old else now, "updated_at": now,
        }, game.user_id)
