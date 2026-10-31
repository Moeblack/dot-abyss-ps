from __future__ import annotations

import random

from .gameplay_catalog import GameplayError, number
from .gameplay_economy import consume
from .gameplay_records import find, put
from .gameplay_routes import route


def context_characters(context):
    selected = context.get("party_characters")
    if selected is not None:
        return list(selected)
    return [int(identifier) for identifier in context.get("members", {})]


def drop_rates(game, event_id, party_characters):
    members = set(party_characters)
    rates = {}
    for row in game.catalog.rows("m_event_special_effect_drops"):
        if row["m_event_id"] == event_id and row["m_character_id"] in members:
            key = row["bonus_content_type"], row["bonus_content_id"]
            rates[key] = rates.get(key, 0) + number(row["bonus_ratio"], 0, 1000000)
    return rates


def event_additional_drop_rate(game, event_id, party_characters):
    return max(drop_rates(game, event_id, party_characters).values(), default=0)


def boost_event_rewards(game, event_id, rewards, party_characters):
    rates = drop_rates(game, event_id, party_characters)
    return [{**reward, "amount": number(reward["amount"] * (1000 + rates.get((reward["content_type"], reward["content_id"]), 0)) // 1000,
                                         1, 2 ** 31 - 1)} for reward in rewards]


def boost_event_drops(game, event_id, stage, reward_index, party_characters, seed):
    rates = drop_rates(game, event_id, party_characters)
    randomizer = random.Random(f"event-drops:{event_id}:{seed}")
    for drop in stage["drops"]:
        numerator = drop["amount"] * rates.get((drop["content_type"], drop["content_id"]), 0)
        whole, fraction = divmod(numerator, 1000)
        drop["amount"] = number(drop["amount"] + whole + int(fraction > 0 and randomizer.randrange(1000) < fraction), 1, 2 ** 31 - 1)
        reward_index[str(drop["sid"])]["amount"] = drop["amount"]


def effect_groups(game, event):
    return {row["m_event_stage_effect_group_no"] for row in game.catalog.rows("m_event_stage_effect_assignments")
            if row["group_no"] == event["m_event_stage_effect_assignment_group_no"]}


def toggle(game, body):
    from .gameplay_events import require_event
    from .gameplay_battle_session import battle_state

    event = require_event(game, body["m_event_id"])
    if battle_state(game)["active"] is not None:
        raise GameplayError("Finish the current battle before changing event effects")
    record = find(game.state, "t_events", m_event_id=event["id"])
    record.update(is_event_stage_effect_enabled=number(body["value"], 0, 1), updated_at=game.now.isoformat())
    return {}


def upgrade(game, body):
    from .gameplay_events import require_event
    from .gameplay_battle_session import battle_state

    event = require_event(game, body["m_event_id"])
    if battle_state(game)["active"] is not None:
        raise GameplayError("Finish the current battle before upgrading event effects")
    group = number(body["m_event_stage_effect_group_no"])
    if group not in effect_groups(game, event):
        raise GameplayError("Effect group does not belong to this event", 400, 4007)
    record = find(game.state, "t_event_stage_effect_levels", m_event_id=event["id"], m_event_stage_effect_group_no=group)
    current = record["level"] if record else 0
    definitions = [row for row in game.catalog.rows("m_event_stage_effects") if row["group_no"] == group and row["level"] == current + 1]
    if len(definitions) != 1:
        raise GameplayError("Event effect has reached its maximum level")
    costs = [row for row in game.catalog.rows("m_event_stage_effect_costs") if row["group_no"] == definitions[0]["m_event_stage_effect_cost_group_no"]]
    consume(game, [{key: row[key] for key in ("content_type", "content_id", "amount")} for row in costs])
    put(game.state, "t_event_stage_effect_levels", {"m_event_id": event["id"], "m_event_stage_effect_group_no": group}, {"level": current + 1})
    return {}


EVENT_EFFECT_ROUTES = {
    "api/event/stage-effect/toggle": route("POST", "m_event_id value", toggle),
    "api/event/stage-effect/upgrade": route("POST", "m_event_id m_event_stage_effect_group_no", upgrade),
}
