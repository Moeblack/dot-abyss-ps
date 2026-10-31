from __future__ import annotations

from .gameplay_catalog import GameplayError, available, identifiers, number
from .gameplay_delivery import deliver
from .gameplay_missions import Missions
from .gameplay_records import find, rows
from .gameplay_routes import route


def missions(game):
    return Missions(game.state, game.progress, game.catalog, game.now, game.user_id, game.rules)


def sync_plans(game):
    evaluator = missions(game)
    for plan in game.catalog.rows("m_plans"):
        if find(game.state, "t_plans", m_plan_id=plan["id"]) is not None:
            continue
        requirements = [r for r in game.catalog.rows("m_plan_restricts") if r["m_plan_id"] == plan["id"]]
        if not requirements:
            continue
        conditions = [game.catalog.get("m_missions", r["m_mission_id"]) for r in requirements]
        if not all(evaluator.active(r) and evaluator.completed(r) for r in conditions):
            continue
        rewards = [{key: r[key] for key in ("content_type", "content_id", "amount")} for r in game.catalog.rows("m_plan_restrict_rewards") if r["m_plan_id"] == plan["id"]]
        if not rewards:
            raise GameplayError("Plan unlock rewards are missing", 503, 5036)
        deliver(game, rewards)
        for condition in conditions:
            game.progress["mission_claims"].setdefault(evaluator.key(condition), [])


def plan_steps(game, identifier):
    game.catalog.get("m_plans", identifier)
    sync_plans(game)
    record = find(game.state, "t_plans", m_plan_id=identifier)
    if record is None:
        raise GameplayError("Plan has not been unlocked")
    steps = sorted([r for r in game.catalog.rows("m_plan_steps") if r["m_plan_id"] == identifier], key=lambda r: r["step"])
    if not steps:
        raise GameplayError("Plan steps are missing", 503, 5036)
    if [row["step"] for row in steps] != list(range(1, len(steps) + 1)) or len({row["m_mission_id"] for row in steps}) != len(steps):
        raise GameplayError("Plan steps must be consecutive with distinct missions", 503, 5036)
    if record["step"] > len(steps):
        raise GameplayError("Stored plan progress exceeds its stage count", 503, 5036)
    return record, steps


def plan_list(game, body):
    identifier = number(body["m_plan_id"], 0)
    sync_plans(game)
    selected = [identifier] if identifier else sorted(r["m_plan_id"] for r in rows(game.state, "t_plans"))
    evaluator = missions(game)
    result, seen = [], set()
    for identifier in selected:
        record, steps = plan_steps(game, identifier)
        for step in steps:
            mission_id = step["m_mission_id"]
            if step["step"] <= record["step"] + 1 and mission_id not in seen:
                result.append(evaluator.view(game.catalog.get("m_missions", mission_id)))
                seen.add(mission_id)
    return {"limited": result}


def plan_receive(game, body):
    record, steps = plan_steps(game, number(body["m_plan_id"]))
    selected = identifiers(body["m_mission_ids"], 100)
    by_mission = {r["m_mission_id"]: r for r in steps}
    if not selected or set(selected) - by_mission.keys():
        raise GameplayError("Requested missions do not belong to this plan", 400, 4007)
    evaluator = missions(game)
    for identifier in sorted(selected, key=lambda i: by_mission[i]["step"]):
        step = by_mission[identifier]
        if step["step"] > record["step"] + 1:
            raise GameplayError("Previous plan step has not been completed")
        evaluator.receive([identifier], allowed_ids=by_mission.keys())
        record["step"] = max(record["step"], step["step"])
    sync_plans(game)
    return {}


def special_complete(game, body):
    identifier = number(body["m_special_mission_id"])
    evaluator = missions(game)
    record, days = evaluator.special(identifier)
    if record["completed_at"]:
        return {}
    if not available(record, game.now) or not days:
        raise GameplayError("Special mission reward is not available")
    if any(evaluator.key(game.catalog.get("m_missions", r["m_mission_id"])) not in game.progress["mission_claims"] for r in days):
        raise GameplayError("All special missions must be claimed first")
    definition = game.catalog.get("m_special_missions", identifier)
    rewards = [{key: r[key] for key in ("content_type", "content_id", "amount")} for r in game.catalog.rows("m_mission_rewards")
               if r["group_id"] == definition["complete_m_mission_reward_group_id"]]
    if not rewards:
        raise GameplayError("Special mission completion rewards are missing", 503, 5036)
    deliver(game, rewards)
    record.update(completed_at=game.now.isoformat(), updated_at=game.now.isoformat())
    return {}


def visitor_missions(game, identifier):
    definition = game.catalog.get("m_visitors", identifier)
    if not available(definition, game.now) or find(game.state, "t_visitors", m_visitor_id=identifier) is None:
        raise GameplayError("Visitor is not available")
    return {r["m_mission_id"] for r in game.catalog.rows("m_visitor_missions") if r["m_visitor_id"] == identifier}


def visitor_list(game, body):
    selected = visitor_missions(game, number(body["m_visitor_id"]))
    evaluator = missions(game)
    result = {"achievement": [], "daily": [], "weekly": [], "limited": []}
    groups = {1: "achievement", 2: "daily", 3: "weekly", 4: "limited", 8: "limited"}
    for identifier in sorted(selected):
        definition = game.catalog.get("m_missions", identifier)
        if evaluator.active(definition) and evaluator.prerequisites(definition):
            result[groups.get(definition["mission_type"], "limited")].append(evaluator.view(definition))
    return result


def visitor_receive(game, body):
    return missions(game).receive(body["m_mission_ids"], allowed_ids=visitor_missions(game, number(body["m_visitor_id"])))


PLAN_ROUTES = {
    "api/plan-mission/list/{0}": route("GET", "m_plan_id", plan_list, "m_plan_id"),
    "api/plan-mission/reward/receive": route("POST", "m_plan_id m_mission_ids", plan_receive),
    "api/special-mission/complete-reward/receive": route("POST", "m_special_mission_id", special_complete),
    "api/visitor-mission/list/{0}": route("GET", "m_visitor_id", visitor_list, "m_visitor_id"),
    "api/visitor-mission/reward/receive": route("POST", "m_mission_ids m_visitor_id", visitor_receive),
}
