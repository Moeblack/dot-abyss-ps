from __future__ import annotations

from .gameplay_catalog import GameplayError
from .gameplay_receipts import feature_data
from .gameplay_records import rows


def repair_ability_responses(game):
    data = feature_data(game, "character")
    if type(data.get("ability_response_version")) is int and data["ability_response_version"] == 1:
        return
    if "ability_response_version" in data:
        raise GameplayError("Stored ability response version is invalid")
    current = {row["id"]: row for row in rows(game.state, "t_character_abilities")}
    pending = [receipt["response"] for receipt in game.progress["operation_receipts"].values()]
    payments = game.progress["features"].get("shop", {}).get("data", {}).get("receipts", {})
    pending.extend(receipt[key] for receipt in payments.values() for key in ("response", "result") if key in receipt)
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            if {"id", "t_character_id", "m_character_ability_id", "level", "possible_max_level"} <= value.keys():
                row = current.get(value["id"])
                if row and all(row[key] == value[key] for key in ("t_character_id", "m_character_ability_id")):
                    value.update(level=row["level"], possible_max_level=row["possible_max_level"])
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    data["ability_response_version"] = 1
