from __future__ import annotations

import copy
import json

from .gameplay_battle_rewards import attach_drops, settle_battle, stage_entities
from .gameplay_battle_session import battle_state, create_session, session_view
from .gameplay_battle_stages import build_stage
from .gameplay_catalog import GameplayError, number
from .gameplay_receipts import operation_digest
from .gameplay_records import rows
from .gameplay_routes import route
from .gameplay_stamina import settle_stamina
from .gameplay_statistics import record
from .gameplay_story_progress import completed, prepare_story_state


def repeat_quest(game, area_id):
    area_id = number(area_id)
    game.catalog.get("m_chapter_areas", area_id)
    quests = [row for row in game.catalog.rows("m_chapter_quests") if row["m_chapter_area_id"] == area_id]
    if not quests or not all(completed(game, quest["id"]) for quest in quests):
        raise GameplayError("Complete this chapter area before repeating its battles")
    repeats = [row for row in game.catalog.rows("m_chapter_repeat_quests") if row["m_chapter_area_id"] == area_id]
    if len(repeats) != 1:
        raise GameplayError("Chapter area does not have one bound repeat quest", 503, 5036)
    repeat = repeats[0]
    definition = game.catalog.get("m_chapter_quest_battles", repeat["m_chapter_quest_battle_id"])
    return repeat, definition


def start(game, body):
    repeat, definition = repeat_quest(game, body["m_chapter_area_id"])
    return create_session(game, definition, kind="repeat", quest_id=definition["id"],
                          chapter_area_id=repeat["m_chapter_area_id"], party_no=number(body["party_no"], 1, 10),
                          stamina=number(repeat["stamina"], 0, 999))


def clear(game, body):
    digest = operation_digest("api/main-story/repeat/clear-battle", "POST", body)
    data = battle_state(game)
    previous = data["last_result"]
    if previous is not None and previous["digest"] == digest:
        return copy.deepcopy(previous["response"])
    active = data["active"]
    if active is None or active["kind"] != "repeat":
        raise GameplayError("No repeat battle is active")
    result = settle_battle(game, active, body["stage_results"])
    response = {"battle_result": result["battle_result"]}
    data["active"] = None
    data["last_result"] = {"digest": digest, "response": copy.deepcopy(response)}
    return response


def abandon(game, body):
    number(body["play_time"], 0, 86400000)
    data = battle_state(game)
    if data["active"] is not None and data["active"]["kind"] != "repeat":
        raise GameplayError("This endpoint can only close a repeat battle")
    data["active"] = None
    return session_view(None)


def skipped_result(stage, stage_type):
    enemies, _, floors, _ = stage_entities(stage)
    treasure_ranks = [rank for floor in floors.values()
                      for rank in (floor.get("role_option", {}).get("treasure_battle") or {}).get("ranks", [])]
    chosen_rank = max((rank["rank"] for rank in treasure_ranks), default=0)
    treasure_drops = {sid for rank in treasure_ranks for sid in rank["drops"]}
    chosen_drops = {sid for rank in treasure_ranks if rank["rank"] == chosen_rank for sid in rank["drops"]}
    result = {"play_time": 0, "destroyed_enemies": sorted(enemies),
              "drop_items": [row["sid"] for row in stage["drops"] if row["sid"] not in treasure_drops or row["sid"] in chosen_drops],
              "mana_gem_count": 0, "top_fc_damage": 0}
    if stage_type == 1:
        result.update(total_damage=0, passed_floor_parts=sorted(floors), clear_rank=chosen_rank)
    return result


def merged_drops(drops):
    merged = {}
    for reward in drops:
        key = json.dumps({field: value for field, value in reward.items() if field != "amount"},
                         sort_keys=True, ensure_ascii=True, separators=(",", ":"))
        if key not in merged:
            merged[key] = copy.deepcopy(reward)
        else:
            merged[key]["amount"] = number(merged[key]["amount"] + reward["amount"], 1, 2 ** 31 - 1)
    return list(merged.values())


def skip(game, body):
    count = number(body["skip_count"], 1, 100)
    repeat, definition = repeat_quest(game, body["m_chapter_area_id"])
    data = battle_state(game)
    if data["active"] is not None:
        raise GameplayError("Finish or abandon the current battle before skipping")
    first_id = number(data["next_id"], 1, 2 ** 31 - 1 - count)
    stamina = number(repeat["stamina"], 0, 999)
    spent = stamina * count
    parameter = settle_stamina(game.state, game.catalog, game.user_id, game.now)
    if parameter["amount"] < spent:
        raise GameplayError("Insufficient stamina", 409, 4093)
    parameter["amount"] -= spent
    if spent:
        record(game.progress, game.now, 130001, spent)
    status = rows(game.state, "t_statuses")[0]
    before_exp, before_gold = status["character_exp"], status["gold"]
    party_no = prepare_story_state(game)["last_party_no"]
    from .gameplay_drop_bonuses import saved_bonus
    ability_bonus = saved_bonus(game, "repeat", definition["id"], party_no)
    drops, event_drops = [], []
    for identifier in range(first_id, first_id + count):
        stage = build_stage(game, definition["stage_type"], definition["stage_id"], identifier, party_no=party_no)
        reward_index = attach_drops(game, definition["id"], stage, identifier, ability_bonus=ability_bonus)
        session = {"kind": "repeat", "battle_id": definition["id"], "stage_type": definition["stage_type"],
                   "stage_detail": stage, "reward_index": reward_index, "party_no": party_no, "ability_extra_drop": ability_bonus,
                   "stamina_spent": stamina, "time_limit": definition["time_limit"], "m_chapter_area_id": repeat["m_chapter_area_id"]}
        result = settle_battle(game, session, skipped_result(stage, definition["stage_type"]))
        drops.extend(result["battle_result"]["drops"])
        event_drops.extend(result["battle_result"]["event_drops"])
    data["next_id"] = first_id + count
    return {"grant_gold": status["gold"] - before_gold, "grant_character_exp": status["character_exp"] - before_exp,
            "before_character_exp": before_exp, "after_character_exp": status["character_exp"],
            "drops": merged_drops(drops), "first_rewards": [], "clear_rewards": [], "event_drops": merged_drops(event_drops)}


REPEAT_ROUTES = {
    "api/main-story/repeat/start-battle": route("POST", "m_chapter_area_id party_no", start),
    "api/main-story/repeat/clear-battle": route("POST", "stage_results", clear),
    "api/main-story/repeat/close-battle": route("POST", "play_time", abandon),
    "api/main-story/repeat/surrender-battle": route("POST", "play_time", abandon),
    "api/main-story/repeat/skip-battle": route("POST", "m_chapter_area_id skip_count", skip),
}
