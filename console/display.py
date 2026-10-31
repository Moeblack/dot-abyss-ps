from __future__ import annotations

import json


IDENTITY_FIELDS = ("id", "m_event_currency_id", "m_nether_code_id", "m_event_id", "m_event_disaster_quest_id")
RECORD_IDENTITIES = {"t_event_currencies": "m_event_currency_id", "t_nether_codes": "m_nether_code_id",
                     "t_events": "m_event_id", "t_event_disaster_quests": "m_event_disaster_quest_id"}


def record_identity_field(snapshot, table):
    return snapshot.get("record_identity_fields", {}).get(table, RECORD_IDENTITIES.get(table, "id"))


def row_identity(row, field=None):
    if field is not None:
        return row.get(field)
    return next((row[name] for name in IDENTITY_FIELDS if row.get(name) is not None), None)


def display_value(value, label=None, key=None):
    if isinstance(value, dict):
        labels = label if isinstance(label, dict) else value.get("_display", {})
        labels = labels if isinstance(labels, dict) else {}
        result = {field: display_value(item, labels.get(field), field) for field, item in value.items() if not field.startswith("_")}
        if "name" not in result and isinstance(labels.get("name"), str):
            result["name"] = labels["name"]
        return result
    if isinstance(value, list):
        labels = label if isinstance(label, list) else []
        return [display_value(item, labels[index] if index < len(labels) else None, key) for index, item in enumerate(value)]
    if isinstance(label, str) and label:
        identifier = type(value) is int or (isinstance(value, str) and value.isascii() and value.isdecimal()
                                          and isinstance(key, str) and (key in {"id", "ids"} or key.endswith(("_id", "_ids"))))
        return f"{label}（ID: {value}）" if identifier else label
    return value


def cell_value(row, key):
    labels = row.get("_display", {})
    return display_value(row.get(key), labels.get(key) if isinstance(labels, dict) else None, key)


def row_label(row):
    return next((value for key in ("name", "title", "label", "display_text")
                 if isinstance(value := cell_value(row, key), str) and value.strip()), "")


def record_rows(snapshot, table):
    tables = snapshot.get("state", {}).get("records", {})
    records = tables.get(table)
    if table not in tables:
        path = snapshot.get("record_sources", {}).get(table)
        if not isinstance(path, list) or not all(isinstance(key, str) for key in path):
            return []
        records = snapshot
        for key in path:
            records = records.get(key) if isinstance(records, dict) else None
    values = records.get("up", []) if isinstance(records, dict) else records
    if not isinstance(values, list):
        return []
    labels = snapshot.get("record_display", {}).get(table, {})
    identity = record_identity_field(snapshot, table)
    return [{**row, "_display": labels.get(str(row_identity(row, identity)), row.get("_display", {}))}
            for row in values if isinstance(row, dict)]


def matches_query(row, query):
    return query.casefold() in json.dumps(row, ensure_ascii=False).casefold()


def preferred_columns(rows, maximum=8):
    keys = list(dict.fromkeys(key for row in rows[:20] for key in row if not key.startswith("_")))
    if "name" not in keys and any(isinstance(row.get("_display"), dict) and row["_display"].get("name") for row in rows[:20]):
        keys.append("name")
    priority = ("id", "name", "title", "label", "player_name", "rarity", "level", "amount", "type", "status")
    return ([key for key in priority if key in keys] + [key for key in keys if key not in priority])[:maximum]
