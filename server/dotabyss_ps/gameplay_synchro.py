from __future__ import annotations

from .gameplay_catalog import GameplayError, number
from .gameplay_economy import consume
from .gameplay_records import put, rows
from .gameplay_routes import route
from .gameplay_character_levels import synchronization_unlocked


def ensure_synchro(game):
    records = rows(game.state, "t_level_synchros")
    if not records:
        initial = min(game.catalog.rows("m_level_synchros"), key=lambda r: r["grade"])
        records.append({"id": 1, "user_id": game.user_id, "grade": initial["grade"], "level": initial["level_min"]})
    return records[0]


def definition(game, record):
    result = next((r for r in game.catalog.rows("m_level_synchros") if r["grade"] == record["grade"]), None)
    if result is None or not result["level_min"] <= record["level"] <= result["level_max"]:
        raise GameplayError("Stored synchronization level does not match its grade")
    return result


def level_up(game, body):
    if not synchronization_unlocked(game):
        raise GameplayError("Synchronization research has not been unlocked")
    record = ensure_synchro(game)
    master = definition(game, record)
    count = number(body["exec_count"], 1, 1000)
    target = record["level"] + count
    if target > master["level_max"]:
        raise GameplayError("Synchronization grade must be upgraded first")
    costs = [r for r in game.catalog.rows("m_level_synchro_level_up_costs") if record["level"] < r["level"] <= target]
    if {r["level"] for r in costs} != set(range(record["level"] + 1, target + 1)):
        raise GameplayError("Synchronization level costs are missing", 503, 5036)
    consume(game, [{k: r[k] for k in ("content_type", "content_id", "amount")} for r in costs])
    record["level"] = target
    return {}


def upgrade(game, body):
    if not synchronization_unlocked(game):
        raise GameplayError("Synchronization research has not been unlocked")
    record = ensure_synchro(game)
    current = definition(game, record)
    target = next((r for r in game.catalog.rows("m_level_synchros") if r["grade"] == current["grade"] + 1), None)
    if target is None:
        raise GameplayError("Synchronization has reached its final grade")
    if record["level"] < current["level_max"] or record["level"] < target["level_min"]:
        raise GameplayError("Synchronization level is below the grade requirement")
    costs = [r for r in game.catalog.rows("m_level_synchro_upgrade_costs") if r["grade"] == target["grade"]]
    if not costs:
        raise GameplayError("Synchronization grade costs are missing", 503, 5036)
    consume(game, [{k: r[k] for k in ("content_type", "content_id", "amount")} for r in costs])
    record["grade"] = target["grade"]
    return {}


SYNCHRO_ROUTES = {
    "api/level-synchro/level-up": route("POST", "exec_count", level_up),
    "api/level-synchro/upgrade": route("POST", "", upgrade),
}
