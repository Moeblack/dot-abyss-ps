from __future__ import annotations

import copy

from .gameplay_battle_rewards import attach_drops, stored_drop_probability, grant_display_rewards, settle_battle
from .gameplay_drop_bonuses import saved_bonus
from .gameplay_battle_session import battle_state, create_session, session_view
from .gameplay_battle_stages import build_stage
from .gameplay_catalog import GameplayError, number
from .gameplay_main_repeat import merged_drops, skipped_result
from .gameplay_party_state import layout
from .gameplay_receipts import operation_digest
from .gameplay_records import find, rows
from .gameplay_routes import route
from .gameplay_side_story_state import (
    prepare_side_story, progress, require_stage, require_story, stage_progress, stage_statuses,
    stages, story_completed, story_open, writable_stage,
)
from .gameplay_stamina import settle_stamina
from .gameplay_statistics import record


def listing(game, body):
    data = progress(game)
    return {"side_stories": [{"m_side_story_id": story["id"], "is_new": int(story["id"] not in data["seen_stories"]),
                              "is_completed": int(story_completed(game, story["id"], data))}
                             for story in sorted(game.catalog.rows("m_side_stories"), key=lambda row: (row["order_no"], row["id"]))
                             if story_open(game, story)]}


def top_view(game, story_id, *, mark_seen=False):
    require_story(game, story_id)
    data = progress(game)
    statuses = stage_statuses(game, story_id, data)
    result = []
    for stage in stages(game, story_id):
        entry = stage_progress(data, stage["id"])
        can_skip = stage["type"] == 3 and bool(entry["clears"])
        result.append({"m_side_story_stage_id": stage["id"], "status": statuses[stage["id"]],
                       "is_reward_received": int(entry["reward_received"]),
                       "skip_extra_drop_probability": stored_drop_probability(game, stage["master_id"], kind="side_story",
                           ability_bonus=saved_bonus(game, "side_story", stage["master_id"], entry["party_no"])) if can_skip else 0,
                       "skip_party_characters": list(entry["party_characters"]) if can_skip else []})
        if mark_seen and statuses[stage["id"]]:
            writable_stage(data, stage["id"])["seen"] = True
    if mark_seen and story_id not in data["seen_stories"]:
        data["seen_stories"].append(story_id)
    return {"side_story_stages": result}


def top(game, body):
    return top_view(game, number(body["m_side_story_id"]), mark_seen=True)


def complete_stage(game, data, stage, *, party_no=1, characters=None, count=1):
    entry = writable_stage(data, stage["id"])
    first = []
    if not entry["reward_received"]:
        rewards = [row for row in game.catalog.rows("m_side_story_rewards")
                   if row["group_id"] == stage["m_side_story_reward_group_id"]]
        first = grant_display_rewards(game, rewards)
        entry["reward_received"] = True
    entry["clears"] = number(entry["clears"] + count, 1, 2 ** 31 - 1)
    entry["seen"] = True
    if characters is not None:
        entry.update(party_no=party_no, party_characters=list(characters))
    record(game.progress, game.now, 280000, count, target=stage["id"])
    return first


def clear_story(game, body):
    stage, data = require_stage(game, body["m_side_story_stage_id"], (1,))
    if stage_progress(data, stage["id"])["clears"]:
        return {"first_rewards": []}
    if battle_state(game)["active"] is not None:
        raise GameplayError("Finish or abandon the active battle before completing a story")
    scripts = [row for row in game.catalog.rows("m_novel_side_stories") if row["m_novel_side_story_id"] == stage["master_id"]]
    if not scripts or any(not row["script_id"] for row in scripts):
        raise GameplayError("Side-story stage has no bound novel scripts", 503, 5036)
    return {"first_rewards": complete_stage(game, data, stage)}


def party_characters(game, party_no):
    party = find(game.state, "t_exploration_parties", party_no=party_no)
    if party is None:
        raise GameplayError("Battle party does not exist")
    members, _ = layout(game, party["id"])
    character_ids = [member["t_character_id"] for member in sorted(members, key=lambda row: row["position_no"]) if member["t_character_id"]]
    characters = {row["id"]: row["m_character_id"] for row in rows(game.state, "t_characters")}
    if any(identifier not in characters for identifier in character_ids):
        raise GameplayError("Battle party contains an unowned character")
    return [characters[identifier] for identifier in character_ids]


def start(game, body):
    stage, _ = require_stage(game, body["m_side_story_stage_id"], (2, 3))
    definition = game.catalog.get("m_side_story_quests", stage["master_id"])
    party_no = number(body["party_no"], 1, 10)
    return create_session(game, definition, kind="side_story", quest_id=definition["id"], party_no=party_no,
                          stamina=number(stage["stamina"], 0, 999),
                          context={"stage_id": stage["id"], "characters": party_characters(game, party_no)})


def clear(game, body):
    digest = operation_digest("api/side-story/clear-quest", "POST", body)
    battle = battle_state(game)
    if battle["last_result"] is not None and battle["last_result"]["digest"] == digest:
        return copy.deepcopy(battle["last_result"]["response"])
    session = battle["active"]
    if session is None or session["kind"] != "side_story":
        raise GameplayError("No side-story battle is active")
    stage = game.catalog.get("m_side_story_stages", session["context"]["stage_id"])
    if stage["type"] not in (2, 3) or stage["master_id"] != session["battle_id"]:
        raise GameplayError("Stored side-story battle binding is invalid")
    data = progress(game)
    result = settle_battle(game, session, body["stage_results"])["battle_result"]
    result["first_rewards"] = complete_stage(game, data, stage, party_no=session["party_no"], characters=session["context"]["characters"])
    result.update(clear_rewards=[])
    battle["active"] = None
    battle["last_result"] = {"digest": digest, "response": copy.deepcopy(result)}
    return result


def abandon(game, body):
    number(body["play_time"], 0, 86400)
    data = battle_state(game)
    if data["active"] is not None and data["active"]["kind"] != "side_story":
        raise GameplayError("A different battle is active")
    data["active"] = None
    return session_view(None)


def skip(game, body):
    count = number(body["skip_count"], 1, 100)
    stage, data = require_stage(game, body["m_side_story_stage_id"], (3,))
    entry = stage_progress(data, stage["id"])
    if not entry["clears"]:
        raise GameplayError("Clear this side-story repeat quest before skipping")
    battle = battle_state(game)
    if battle["active"] is not None:
        raise GameplayError("Finish or abandon the current battle before skipping")
    definition = game.catalog.get("m_side_story_quests", stage["master_id"])
    first_id = number(battle["next_id"], 1, 2 ** 31 - 1 - count)
    cost = number(stage["stamina"], 0, 999)
    spent = cost * count
    stamina = settle_stamina(game.state, game.catalog, game.user_id, game.now)
    if stamina["amount"] < spent:
        raise GameplayError("Insufficient stamina", 409, 4093)
    stamina["amount"] -= spent
    if spent:
        record(game.progress, game.now, 130001, spent)
    status = rows(game.state, "t_statuses")[0]
    before_exp, before_gold = status["character_exp"], status["gold"]
    from .gameplay_drop_bonuses import saved_bonus
    ability_bonus = saved_bonus(game, "side_story", definition["id"], entry["party_no"])
    drops, event_drops = [], []
    for identifier in range(first_id, first_id + count):
        generated = build_stage(game, definition["stage_type"], definition["stage_id"], identifier,
                                kind="side_story", quest_id=definition["id"], party_no=entry["party_no"])
        rewards = attach_drops(game, definition["id"], generated, identifier, kind="side_story", ability_bonus=ability_bonus)
        session = {"kind": "side_story", "battle_id": definition["id"], "stage_type": definition["stage_type"],
                   "stage_detail": generated, "reward_index": rewards, "party_no": entry["party_no"], "ability_extra_drop": ability_bonus,
                   "stamina_spent": cost, "time_limit": definition["time_limit"], "result_schema": "exploration",
                   "context": {"stage_id": stage["id"], "characters": entry["party_characters"]}}
        result = settle_battle(game, session, skipped_result(generated, definition["stage_type"]))["battle_result"]
        drops.extend(result["drops"])
        event_drops.extend(result["event_drops"])
    battle["next_id"] = first_id + count
    complete_stage(game, data, stage, count=count)
    return {"grant_gold": status["gold"] - before_gold, "grant_character_exp": status["character_exp"] - before_exp,
            "before_character_exp": before_exp, "after_character_exp": status["character_exp"], "drops": merged_drops(drops),
            "first_rewards": [], "clear_rewards": [], "event_drops": merged_drops(event_drops), "skip_count": count}


def replay_response(game, path, response):
    if path == "api/side-story/list":
        return {**response, **listing(game, {})}
    if path == "api/side-story/top" and response.get("side_story_stages"):
        stage = game.catalog.get("m_side_story_stages", response["side_story_stages"][0]["m_side_story_stage_id"])
        return {**response, **top_view(game, stage["m_side_story_id"])}
    return response


SIDE_STORY_ROUTES = {
    "api/side-story/list": route("GET", "", listing),
    "api/side-story/top": route("POST", "m_side_story_id", top),
    "api/side-story/clear-story": route("POST", "m_side_story_stage_id", clear_story),
    "api/side-story/start-quest": route("POST", "m_side_story_stage_id party_no", start),
    "api/side-story/clear-quest": route("POST", "stage_results", clear),
    "api/side-story/close-quest": route("POST", "play_time", abandon),
    "api/side-story/surrender-quest": route("POST", "play_time", abandon),
    "api/side-story/skip-quest": route("POST", "m_side_story_stage_id skip_count", skip),
}
