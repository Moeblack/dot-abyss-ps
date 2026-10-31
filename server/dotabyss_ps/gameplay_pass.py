from __future__ import annotations

from .gameplay_catalog import GameplayError, available, identifiers, number
from .gameplay_delivery import deliver
from .gameplay_economy import consume
from .gameplay_pass_state import pass_state
from .gameplay_plans import missions
from .gameplay_routes import route


def active_pass(game, value):
    identifier = number(value)
    definition = game.catalog.get("m_mission_passes", identifier)
    if not available(definition, game.now):
        raise GameplayError("Mission pass is not active")
    levels = sorted([r for r in game.catalog.rows("m_mission_pass_levels") if r["m_mission_pass_id"] == identifier], key=lambda r: r["level"])
    if not levels:
        raise GameplayError("Mission pass levels are missing", 503, 5036)
    return definition, levels, pass_state(game.progress, identifier)


def maximum(definition, levels):
    result = next((r for r in levels if r["level"] == definition["m_mission_pass_level_max"]), None)
    if result is None:
        raise GameplayError("Mission pass maximum level is missing", 503, 5036)
    return result


def user_view(definition, levels, record):
    cap = maximum(definition, levels)
    loop = next((r for r in levels if r["level"] == cap["level"] + 1), None)
    interval = loop["require_total_pass_point"] - cap["require_total_pass_point"] if loop else 0
    return {"m_mission_pass_id": definition["id"], "total_pass_point": min(record["points"], cap["require_total_pass_point"]),
            "loop_pass_point": max(0, record["points"] - cap["require_total_pass_point"] - record["loop_received"] * interval), "is_premium": record["premium"]}


def receivable(game, evaluator=None):
    if not game.catalog.tables.get("m_mission_passes"):
        return False
    for definition in game.catalog.rows("m_mission_passes"):
        if not available(definition, game.now):
            continue
        if evaluator is None:
            evaluator = missions(game)
        if any(evaluator.receivable(mission) for mission in game.catalog.rows("m_missions")
               if mission["m_mission_pass_id"] == definition["id"]):
            return True
        _, levels, record = active_pass(game, definition["id"])
        for level in levels:
            if level["level"] > definition["m_mission_pass_level_max"] or level["require_total_pass_point"] > record["points"]:
                continue
            flags = record["rewards"].get(str(level["level"]), {})
            if not flags.get("normal", False) or record["premium"] and not flags.get("premium", False):
                return True
        cap = maximum(definition, levels)
        loop = next((level for level in levels if level["level"] == cap["level"] + 1), None)
        if loop is not None:
            interval = loop["require_total_pass_point"] - cap["require_total_pass_point"]
            if interval > 0 and user_view(definition, levels, record)["loop_pass_point"] >= interval:
                return True
    return False


def top(game, body):
    definition, levels, record = active_pass(game, body["m_mission_pass_id"])
    evaluator = missions(game)
    groups = {"pass_total": [], "pass_weekly": []}
    for mission in sorted(game.catalog.rows("m_missions"), key=lambda r: (r["display_order"], r["id"])):
        if mission["m_mission_pass_id"] != definition["id"] or not evaluator.active(mission) or not evaluator.prerequisites(mission):
            continue
        view = evaluator.view(mission)
        view.pop("calculate_type")
        groups["pass_weekly" if mission["mission_type"] == 6 else "pass_total"].append(view)
    rewards = []
    for level in levels:
        if (level["level"] > definition["m_mission_pass_level_max"]
                or level["require_total_pass_point"] > record["points"]):
            continue
        flags = record["rewards"].get(str(level["level"]), {})
        rewards.append({"level": level["level"], "is_normal_received": int(flags.get("normal", False)), "is_premium_received": int(flags.get("premium", False))})
    return {"t_mission_pass": user_view(definition, levels, record), "t_mission_pass_loop_reward": {"received_count": record["loop_received"]},
            "t_mission_pass_level_rewards": rewards, "missions": groups}


def group_rewards(game, group):
    rewards = [{key: r[key] for key in ("content_type", "content_id", "amount")} for r in game.catalog.rows("m_mission_rewards") if r["group_id"] == group]
    if not rewards:
        raise GameplayError("Mission pass reward group is missing", 503, 5036)
    return rewards


def level_receive(game, body):
    definition, levels, record = active_pass(game, body["m_mission_pass_id"])
    requested = body["levels"]
    if not isinstance(requested, list) or not 1 <= len(requested) <= 2 * definition["m_mission_pass_level_max"]:
        raise GameplayError("Invalid pass level selection", 400, 4007)
    by_level = {r["level"]: r for r in levels}
    seen, rewards = set(), []
    for value in requested:
        if not isinstance(value, dict) or set(value) != {"level", "receive_normal", "receive_premium"}:
            raise GameplayError("Invalid pass reward fields", 400, 4007)
        level = number(value["level"], 1, definition["m_mission_pass_level_max"])
        normal, premium = number(value["receive_normal"], 0, 1), number(value["receive_premium"], 0, 1)
        selected_tracks = {(level, field) for field, enabled in (("normal", normal), ("premium", premium)) if enabled}
        if seen.intersection(selected_tracks) or not selected_tracks or level not in by_level:
            raise GameplayError("Pass reward selection is duplicated or empty", 400, 4007)
        seen.update(selected_tracks)
        master = by_level[level]
        if record["points"] < master["require_total_pass_point"] or premium and not record["premium"]:
            raise GameplayError("Pass reward level or premium access is locked")
        flags = record["rewards"].setdefault(str(level), {"normal": False, "premium": False})
        for field, selected in (("normal", normal), ("premium", premium)):
            if selected and not flags[field]:
                rewards.extend(group_rewards(game, master[f"{field}_m_mission_reward_group_id"]))
                flags[field] = True
    deliver(game, rewards)
    return {}


def level_up(game, body):
    definition, levels, record = active_pass(game, body["m_mission_pass_id"])
    current = max((r["level"] for r in levels if r["require_total_pass_point"] <= record["points"]), default=0)
    target = current + number(body["level_up_count"], 1, definition["m_mission_pass_level_max"])
    if target > definition["m_mission_pass_level_max"]:
        raise GameplayError("Mission pass level cap exceeded")
    purchased = [r for r in levels if current < r["level"] <= target]
    if len(purchased) != target - current:
        raise GameplayError("Mission pass level costs are incomplete", 503, 5036)
    consume(game, [{"content_type": 10, "content_id": 0, "amount": sum(r["require_virtual_money"] for r in purchased)}])
    record["points"] = max(record["points"], purchased[-1]["require_total_pass_point"])
    return {}


def mission_receive(game, body):
    definition, _, _ = active_pass(game, body["m_mission_pass_id"])
    allowed = {r["id"] for r in game.catalog.rows("m_missions") if r["m_mission_pass_id"] == definition["id"]}
    return missions(game).receive(body["m_mission_ids"], allowed_ids=allowed)


def loop_receive(game, body):
    definition, levels, record = active_pass(game, body["m_mission_pass_id"])
    cap = maximum(definition, levels)
    loop = next((r for r in levels if r["level"] == cap["level"] + 1), None)
    if loop is None or loop["require_total_pass_point"] <= cap["require_total_pass_point"]:
        raise GameplayError("Mission pass loop rewards are not configured", 503, 5036)
    available_count = max(0, record["points"] - cap["require_total_pass_point"]) // (loop["require_total_pass_point"] - cap["require_total_pass_point"])
    count = available_count - record["loop_received"]
    if count > 0:
        rewards = [{**r, "amount": number(r["amount"] * count, 1, 2 ** 31 - 1)} for r in group_rewards(game, loop["normal_m_mission_reward_group_id"])]
        deliver(game, rewards)
        record["loop_received"] = available_count
    return {"t_mission_pass": user_view(definition, levels, record)}


PASS_ROUTES = {
    "api/mission-pass/top": route("POST", "m_mission_pass_id", top),
    "api/mission-pass/level/up": route("POST", "m_mission_pass_id level_up_count", level_up),
    "api/mission-pass/level/reward/receive": route("POST", "m_mission_pass_id levels", level_receive),
    "api/mission-pass/mission/reward/receive": route("POST", "m_mission_pass_id m_mission_ids", mission_receive),
    "api/mission-pass/loop-reward/receive": route("POST", "m_mission_pass_id", loop_receive),
}
