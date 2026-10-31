from __future__ import annotations

from .gameplay_catalog import GameplayError, number, parse_timestamp
from .gameplay_delivery import deliver
from .gameplay_economy import balance_location, combined_costs, consume
from .gameplay_receipts import feature_data
from .gameplay_records import find, put, rows
from .gameplay_routes import route
from .gameplay_effects import research_effects, settle_before_effect_change
from .gameplay_character_levels import effective_character_level

TUTORIAL_MATERIAL_IDS = (50001, 50004, 50007)
RESEARCH_TUTORIAL_MASK = 1 << (9 - 1)


def ensure_research_rewards(game):
    rewards = []
    for effect in research_effects(game, 600):
        identifier = number(effect["param_1"])
        game.catalog.get("m_mana_gems", identifier)
        if find(game.state, "t_mana_gems", m_mana_gem_id=identifier) is None:
            rewards.append({"content_type": 60, "content_id": identifier, "amount": 1})
    if rewards:
        deliver(game, rewards)


def tutorial_supply_receipt(game):
    data = game.progress["features"].get("research", {}).get("data", {})
    if "tutorial_supplies" not in data:
        return None
    receipt = data["tutorial_supplies"]
    if (not isinstance(receipt, dict) or not {"amount", "granted_at"} <= set(receipt)
            or set(receipt) - {"amount", "granted_at", "feature_reward_bone_credit"}):
        raise GameplayError("Stored research tutorial supplies are invalid")
    amount = number(receipt["amount"], 1, 999999)
    number(receipt.get("feature_reward_bone_credit", 0), 0, amount)
    if not isinstance(receipt["granted_at"], str):
        raise GameplayError("Stored research tutorial supply date is invalid")
    try:
        parse_timestamp(receipt["granted_at"])
    except (ValueError, TypeError) as error:
        raise GameplayError("Stored research tutorial supply date is invalid") from error
    return receipt


def ensure_tutorial_supplies(game):
    from .gameplay_access import reward_claims

    if tutorial_supply_receipt(game) is not None:
        return
    amount = game.rules["research_tutorial_material_amount"]
    completed = any(row["completed_flags"] & RESEARCH_TUTORIAL_MASK for row in rows(game.state, "t_feature_tutorials"))
    if not game.rules["unlock_common_features"] or not amount or completed:
        return
    claims = reward_claims(game).values() if "feature_unlock" in game.progress["features"] else ()
    bone_credit = min(amount, sum(claim["granted_amount"] for claim in claims
                                  if claim["m_feature_lock_id"] == 1 and claim["content_type"] == 30 and claim["content_id"] == 50004))
    rewards = [{"content_type": 30, "content_id": identifier, "amount": amount - (bone_credit if identifier == 50004 else 0)}
               for identifier in TUTORIAL_MATERIAL_IDS]
    deliver(game, [reward for reward in rewards if reward["amount"]])
    feature_data(game, "research")["tutorial_supplies"] = {
        "amount": amount, "granted_at": game.now.isoformat(), "feature_reward_bone_credit": bone_credit}


def building(game, identifier):
    game.catalog.get("m_buildings", identifier)
    record = find(game.state, "t_buildings", m_building_id=identifier)
    if record is None:
        raise GameplayError("Building is not open")
    return record


def skill_definitions(game, identifier, kind):
    definitions = sorted([r for r in game.catalog.rows("m_building_skill_levels")
                          if r["m_building_id"] == identifier and r["skill_type"] == kind], key=lambda r: r["skill_level"])
    if not definitions:
        raise GameplayError("Unknown building skill", 400, 4007)
    return definitions


def current_skill(game, identifier, kind):
    skill_definitions(game, identifier, kind)
    return put(game.state, "t_building_skills", {"m_building_id": identifier, "skill_type": kind},
               {} if find(game.state, "t_building_skills", m_building_id=identifier, skill_type=kind) else
               {"skill_level": 0, "checked_skill_level": 0}, game.user_id)


def requirements_met(game, definition):
    restrictions = [r for r in game.catalog.rows("m_building_skill_restricts") if r["m_building_skill_level_id"] == definition["id"]]
    levels = game.catalog.rows("m_character_intimacy_levels")
    for restriction in restrictions:
        kind, needed = restriction["restrict_type"], restriction["restrict_param_1"]
        if kind == 1:
            value = sum(max((r["level"] for r in levels if r["total_point"] <= character["intimacy_point"]), default=0)
                        for character in rows(game.state, "t_characters"))
        elif kind == 2:
            value = sum(effective_character_level(game, r) >= 30 for r in rows(game.state, "t_characters"))
        else:
            raise GameplayError("Building restriction type is not bound", 503, 5036)
        if value < needed:
            return False
    for cost in costs(game, definition):
        if cost["content_type"] == 132:
            plan = find(game.state, "t_plans", m_plan_id=cost["content_id"])
            steps = [r for r in game.catalog.rows("m_plan_steps") if r["m_plan_id"] == cost["content_id"] and r["step"] <= cost["amount"]]
            if plan is None or len(steps) != cost["amount"] or any(f"once:{r['m_mission_id']}" not in game.progress["mission_claims"] for r in steps):
                return False
    return True


def costs(game, definition):
    return [{"content_type": r["content_type"], "content_id": r["content_id"], "amount": r["require_amount"]}
            for r in game.catalog.rows("m_building_skill_costs") if r["m_building_skill_level_id"] == definition["id"]]


def spendable_costs(game, definition):
    return [r for r in costs(game, definition) if r["content_type"] != 132]


def affordable(game, definition):
    for cost in combined_costs(spendable_costs(game, definition)):
        row, field = balance_location(game, cost["content_type"], cost["content_id"])
        if row is None or row[field] < cost["amount"]:
            return False
    return True


def listing(game, body):
    result = []
    opened = {r["m_building_id"] for r in rows(game.state, "t_buildings")}
    keys = sorted({(r["m_building_id"], r["skill_type"]) for r in game.catalog.rows("m_building_skill_levels")})
    for identifier, kind in keys:
        if identifier not in opened:
            continue
        definitions = skill_definitions(game, identifier, kind)
        current = current_skill(game, identifier, kind)
        for definition in definitions:
            level = definition["skill_level"]
            acquired = level <= current["skill_level"]
            ready = level == current["skill_level"] + 1 and requirements_met(game, definition)
            result.append({"m_building_id": identifier, "skill_type": kind, "skill_level": level,
                           "m_building_skill_level_id": definition["id"],
                           "is_upgradable": int(ready and affordable(game, definition)),
                           "is_unlocked": int(acquired), "is_new": int(current["checked_skill_level"] < level <= current["skill_level"])})
    return {"building_skills": result}


def upgrade(game, body):
    from .gameplay_idle import reconcile

    identifier, kind = number(body["m_building_id"]), number(body["skill_type"], 1, 2 ** 31 - 1)
    record = building(game, identifier)
    current = current_skill(game, identifier, kind)
    target = next((r for r in skill_definitions(game, identifier, kind) if r["skill_level"] == current["skill_level"] + 1), None)
    if target is None:
        raise GameplayError("Building skill has reached its maximum level")
    if not requirements_met(game, target):
        raise GameplayError("Building upgrade requirements are not met")
    settle_before_effect_change(game)
    consume(game, spendable_costs(game, target))
    current["skill_level"] = target["skill_level"]
    record["exp"] = number(record["exp"] + target["building_exp"], 0, 2 ** 31 - 1)
    record["level"] = max(r["level"] for r in game.catalog.rows("m_building_levels") if r["m_building_id"] == identifier and r["exp"] <= record["exp"])
    ensure_research_rewards(game)
    reconcile(game, settle_pending=False)
    return {"building": {key: record[key] for key in ("id", "user_id", "m_building_id", "level", "exp", "opened_at")}, "payback_costs": []}


def check_level(game, body):
    record = building(game, number(body["m_building_id"]))
    record["checked_level"] = record["level"]
    return {}


def check_upgrade(game, body):
    identifier, kind = number(body["m_building_id"]), number(body["skill_type"], 1, 2 ** 31 - 1)
    building(game, identifier)
    record = current_skill(game, identifier, kind)
    record["checked_skill_level"] = record["skill_level"]
    return {}


RESEARCH_ROUTES = {
    "api/research": route("GET", "", listing),
    "api/research/upgrade": route("POST", "m_building_id skill_type", upgrade),
    "api/research/level/direction/check": route("POST", "m_building_id", check_level),
    "api/research/upgrade/direction/check": route("POST", "m_building_id skill_type", check_upgrade),
}
