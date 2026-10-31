from __future__ import annotations

import copy
from functools import partial

from .gameplay_battle_rewards import attach_drops, stored_drop_probability, grant_display_rewards, settle_battle
from .gameplay_battle_session import battle_state, create_session, session_view
from .gameplay_battle_stages import build_stage
from .gameplay_catalog import GameplayError, number
from .gameplay_economy import consume
from .gameplay_drop_bonuses import saved_bonus
from .gameplay_event_effects import boost_event_drops
from .gameplay_event_quest_state import (
    QUEST_KINDS, node_record, prepare_event_quests, saved_node, selected_node,
)
from .gameplay_main_repeat import merged_drops, skipped_result
from .gameplay_receipts import operation_digest
from .gameplay_records import rows
from .gameplay_routes import route
from .gameplay_side_story import party_characters
from .gameplay_stamina import settle_stamina
from .gameplay_statistics import record as record_statistic


def check(game, body, kind):
    from .gameplay_events import require_event

    event = require_event(game, number(body["m_event_id"]), (kind.event_type,))
    prepare_event_quests(game, event)
    for node in game.catalog.rows(kind.nodes):
        if node["m_event_id"] == event["id"]:
            current = node_record(game, kind, node["id"])
            if current["status"] == 1:
                current["status"] = 2
    return {}


def clear_story(game, body, kind):
    from .gameplay_events import record_event_clear
    from .gameplay_library import complete_event_story

    event, node, _, native = selected_node(game, kind, body[kind.field])
    entry = saved_node(game, kind, node)
    if native["status"] == 99:
        return {"receive_content_list": []}
    if battle_state(game)["active"] is not None:
        raise GameplayError("Finish the active battle before advancing the event story")
    novels = [row for row in game.catalog.rows(f"m_event_{kind.name}_novels") if row[kind.field] == node["id"]]
    if not novels:
        raise GameplayError("Event story has no bound novel", 503, 5036)
    rewards = []
    for novel in novels:
        rewards.extend(complete_event_story(game, novel["m_novel_event_id"]))
    native["status"] = 99
    entry.update(first_claimed=True, clears=max(1, entry["clears"]))
    record_event_clear(game, event, node["id"], {}, {"kind": f"event_{kind.name}", "stage_id": node["id"],
                                                   "stamina_spent": 0, "party_characters": [], "is_story": True})
    prepare_event_quests(game, event)
    return {"receive_content_list": [{"origin_content": None, **reward} for reward in rewards]}


def costs(kind, node, count=1):
    if kind.name == "story":
        cost_type = node["cost_type"]
        if cost_type == 0:
            return 0, [], 0
        if cost_type == 1:
            return number(node["cost"], 0, 999) * count, [], 0
        raise GameplayError("Story-event ticket cost requires its event ticket binding", 503, 5036)
    if kind.name == "hunt":
        cost_type, identifier = node["cost_content_type"], node["cost_content_id"]
        amount = number(node["cost_amount"], 0, 999) * count
        if cost_type == 120 and identifier == 5:
            return amount, [], 0
        return 0, [{"content_type": cost_type, "content_id": identifier, "amount": amount}] if amount else [], 0
    if kind.name == "training" and node["type"] == 2:
        return 0, [], count
    return 0, [], 0


def spend_nonstamina(game, kind, node, count=1):
    from .gameplay_events import spend_event_supply

    stamina, contents, attempts = costs(kind, node, count)
    if contents:
        consume(game, contents)
    if attempts:
        spend_event_supply(game, node["m_event_id"], attempts)
    return stamina


def characters(game, definition, party_no):
    fixed = definition.get("m_fixed_party_id", 0)
    if fixed:
        party = game.catalog.get("m_fixed_parties", fixed)
        allies = [party[f"m_ally_id_{position}"] for position in range(1, 11) if party[f"m_ally_id_{position}"]]
        return [game.catalog.get("m_allies", identifier)["m_character_id"] for identifier in allies]
    return party_characters(game, party_no)


def start(game, body, kind):
    event, node, definition, _ = selected_node(game, kind, body[kind.field], battle=True)
    party_no = number(body["party_no"], 0 if definition.get("m_fixed_party_id") else 1, 10)
    context = {"event_id": event["id"], "kind": f"event_{kind.name}", "stage_id": node["id"],
               "stamina_spent": costs(kind, node)[0], "party_characters": characters(game, definition, party_no)}
    beginning = battle_state(game)["active"] is None
    if beginning:
        spend_nonstamina(game, kind, node)
    return create_session(game, definition, kind=f"event_{kind.name}", quest_id=definition["id"], party_no=party_no,
                          stamina=context["stamina_spent"], context=context)


def rewards_for(game, kind, node, *, first):
    return [row for row in game.catalog.rows(kind.rewards)
            if row[kind.field] == node["id"] and bool(row.get("is_first_reward", 1)) == first]


def finish(game, kind, session, stage_results):
    from .gameplay_events import boost_event_rewards, event_additional_drop_rate, record_event_clear

    context = session["context"]
    event = game.catalog.get("m_events", context["event_id"])
    node = game.catalog.get(kind.nodes, context["stage_id"])
    if node["m_event_id"] != event["id"]:
        raise GameplayError("Stored event battle refers to another event")
    entry = saved_node(game, kind, node)
    native = node_record(game, kind, node["id"])
    if native is None:
        raise GameplayError("Event battle progress is missing")
    status = rows(game.state, "t_statuses")[0]
    before_exp, before_gold = status["character_exp"], status["gold"]
    result = settle_battle(game, session, stage_results)["battle_result"]
    result["first_rewards"] = [] if entry["first_claimed"] else grant_display_rewards(game, rewards_for(game, kind, node, first=True))
    repeated = boost_event_rewards(game, event["id"], rewards_for(game, kind, node, first=False), context["party_characters"])
    result["clear_rewards"] = grant_display_rewards(game, repeated)
    entry.update(first_claimed=True, clears=number(entry["clears"] + 1, 1, 2 ** 31 - 1),
                 party_no=session["party_no"], party_characters=list(context["party_characters"]))
    native["status"] = 99
    if kind.name != "speedrun":
        native.update(skip_extra_drop_probability=stored_drop_probability(game, session["battle_id"], kind=f"event_{kind.name}", ability_bonus=session.get("ability_extra_drop", 0)),
                      skip_additional_drop_rate=event_additional_drop_rate(game, event["id"], context["party_characters"]),
                      skip_party_characters=list(context["party_characters"]))
    if kind.name in ("commission", "speedrun") and not context.get("is_skip"):
        time = number(stage_results["play_time"], 0, 86400)
        if time > 0:
            native["clear_time"] = min(native["clear_time"], time) if native["clear_time"] > 0 else time
        if kind.name == "commission":
            native["last_cleared_at"] = game.now.isoformat()
        else:
            native["updated_at"] = game.now.isoformat()
    record_event_clear(game, event, session["quest_id"], stage_results, context)
    released = prepare_event_quests(game, event)
    result.update(grant_gold=status["gold"] - before_gold, grant_character_exp=status["character_exp"] - before_exp,
                  before_character_exp=before_exp, after_character_exp=status["character_exp"])
    result.pop("event_drops", None)
    if kind.name != "commission":
        result["is_explore_start"] = 0
    if kind.name == "hunt":
        result["release_novel_event_ids"] = released
    return result


def clear(game, body, kind):
    digest = operation_digest(f"api/event/{kind.name}/clear-quest", "POST", body)
    data = battle_state(game)
    if data["last_result"] is not None and data["last_result"]["digest"] == digest:
        return copy.deepcopy(data["last_result"]["response"])
    session = data["active"]
    if session is None or session["kind"] != f"event_{kind.name}":
        raise GameplayError("No matching event battle is active")
    response = finish(game, kind, session, body["stage_results"])
    data["active"] = None
    data["last_result"] = {"digest": digest, "response": copy.deepcopy(response)}
    return response


def abandon(game, body, kind):
    number(body["play_time"], 0, 86400)
    data = battle_state(game)
    if data["active"] is not None and data["active"]["kind"] != f"event_{kind.name}":
        raise GameplayError("A different battle mode is active")
    data["active"] = None
    return session_view(None)


def skip(game, body, kind):
    count = number(body["skip_count"], 1, 100)
    event, node, definition, native = selected_node(game, kind, body[kind.field], battle=True)
    if native["status"] != 99:
        raise GameplayError("Clear the event quest before skipping it")
    if kind.name in ("story", "training") and node["type"] == 3:
        raise GameplayError("This event challenge cannot be skipped")
    data = battle_state(game)
    if data["active"] is not None:
        raise GameplayError("Finish the active battle before skipping")
    entry = saved_node(game, kind, node)
    first_id = number(data["next_id"], 1, 2 ** 31 - 1 - count)
    spent = spend_nonstamina(game, kind, node, count)
    if spent:
        stamina = settle_stamina(game.state, game.catalog, game.user_id, game.now)
        if stamina["amount"] < spent:
            raise GameplayError("Insufficient stamina", 409, 4093)
        stamina["amount"] -= spent
        record_statistic(game.progress, game.now, 130001, spent)
    status = rows(game.state, "t_statuses")[0]
    before_exp, before_gold = status["character_exp"], status["gold"]
    rewards = {field: [] for field in ("drops", "first_rewards", "clear_rewards")}
    ability_bonus = saved_bonus(game, f"event_{kind.name}", definition["id"], entry["party_no"])
    for identifier in range(first_id, first_id + count):
        stage = build_stage(game, definition["stage_type"], definition["stage_id"], identifier,
                            kind=f"event_{kind.name}", quest_id=definition["id"], party_no=entry["party_no"])
        reward_index = attach_drops(game, definition["id"], stage, identifier, kind=f"event_{kind.name}",
                                   party_no=entry["party_no"], ability_bonus=ability_bonus)
        boost_event_drops(game, event["id"], stage, reward_index, entry["party_characters"], identifier)
        session = {"kind": f"event_{kind.name}", "battle_id": definition["id"], "quest_id": definition["id"],
                   "stage_type": definition["stage_type"], "stage_detail": stage, "reward_index": reward_index,
                   "party_no": entry["party_no"], "time_limit": definition["time_limit"], "stamina_spent": spent // count,
                   "ability_extra_drop": ability_bonus,
                   "result_schema": "exploration", "context": {"event_id": event["id"], "kind": f"event_{kind.name}",
                   "stage_id": node["id"], "stamina_spent": spent // count, "party_characters": entry["party_characters"], "is_skip": True}}
        response = finish(game, kind, session, skipped_result(stage, definition["stage_type"]))
        for field in rewards:
            rewards[field].extend(response[field])
    data["next_id"] = first_id + count
    return {**{field: merged_drops(values) for field, values in rewards.items()},
            "grant_gold": status["gold"] - before_gold, "grant_character_exp": status["character_exp"] - before_exp,
            "before_character_exp": before_exp, "after_character_exp": status["character_exp"]}


EVENT_QUEST_ROUTES = {}
for _name, _kind in QUEST_KINDS.items():
    _prefix = f"api/event/{_name}/"
    for _path, _fields, _handler in (
        ("start-quest", f"{_kind.field} party_no", start), ("clear-quest", "stage_results", clear),
        ("close-quest", "play_time", abandon), ("surrender-quest", "play_time", abandon),
    ):
        EVENT_QUEST_ROUTES[_prefix + _path] = route("POST", _fields, partial(_handler, kind=_kind))
    if _name != "training":
        EVENT_QUEST_ROUTES[_prefix + "check-quest"] = route("POST", "m_event_id", partial(check, kind=_kind))
    if _name in ("story", "training", "hunt"):
        EVENT_QUEST_ROUTES[_prefix + "skip-quest"] = route("POST", f"{_kind.field} skip_count", partial(skip, kind=_kind))
    if _kind.staged:
        EVENT_QUEST_ROUTES[_prefix + "clear-story"] = route("POST", _kind.field, partial(clear_story, kind=_kind))
