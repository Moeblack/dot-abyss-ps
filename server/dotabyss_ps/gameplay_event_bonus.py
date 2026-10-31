from __future__ import annotations

import copy

from .gameplay_battle_rewards import stored_drop_probability, grant_display_rewards, settle_battle
from .gameplay_battle_session import battle_state, create_session, session_view
from .gameplay_catalog import GameplayError, number
from .gameplay_effects import extra_drop_bonus
from .gameplay_event_effects import boost_event_rewards, context_characters
from .gameplay_event_rewards import randomizer
from .gameplay_events import event_available, event_data, record_event_clear, require_event
from .gameplay_main_repeat import skipped_result
from .gameplay_receipts import operation_digest
from .gameplay_records import find, put, rows
from .gameplay_routes import route


def setting(game, event_id):
    selected = [row for row in game.catalog.rows("m_event_bonus_quests") if row["m_event_id"] == event_id]
    if len(selected) != 1:
        raise GameplayError("This event has no bonus quest", 400, 4007)
    return selected[0]


def bonus_record(game, event_id):
    definition = setting(game, event_id)
    record = find(game.state, "t_event_bonus_quests", m_event_id=event_id)
    if record is None:
        record = put(game.state, "t_event_bonus_quests", {"m_event_id": event_id}, {
            "probability": number(definition["default_probability"], 0, 1000),
            "m_event_bonus_quest_stage_id": None, "can_skip": 0}, game.user_id)
    return record


def prepare_event_bonus(game):
    if "m_event_bonus_quests" not in game.catalog.tables:
        return
    for definition in game.catalog.rows("m_event_bonus_quests"):
        event_id = definition["m_event_id"]
        if find(game.state, "t_events", m_event_id=event_id):
            bonus_record(game, event_id)


def trigger_bonus(game, *, chapter_id=0, difficulty=0, side_story_stage_id=0, party_characters=()):
    if not chapter_id and not side_story_stage_id:
        return []
    appeared = []
    for definition in game.catalog.rows("m_event_bonus_quests"):
        event_id = definition["m_event_id"]
        event = game.catalog.get("m_events", event_id)
        if not find(game.state, "t_events", m_event_id=event_id) or not event_available(game, event_id, event):
            continue
        current = bonus_record(game, event_id)
        if current["m_event_bonus_quest_stage_id"]:
            continue
        chapters = [row for row in game.catalog.rows("m_event_bonus_quest_chapters")
                    if row["m_event_bonus_quest_id"] == definition["id"]
                    and (row["m_side_story_stage_id"] == side_story_stage_id if side_story_stage_id
                         else row["m_chapter_id"] == chapter_id and row["difficulty"] == difficulty and not row["m_side_story_stage_id"])]
        chapter_ids = {row["id"] for row in chapters}
        candidates = [row for row in game.catalog.rows("m_event_bonus_quest_stages") if row["m_event_bonus_quest_chapter_id"] in chapter_ids]
        if not candidates:
            continue
        data = event_data(game, event_id).setdefault("bonus", {"rolls": 0})
        roll = number(data.get("rolls"), 0)
        chooser = randomizer(game, event_id, "bonus", roll)
        data["rolls"] = roll + 1
        members = set(party_characters)
        character_bonus = sum(row["bonus_ratio"] for row in game.catalog.rows("m_event_special_effect_occurs")
                              if row["m_event_id"] == event_id and row["m_character_id"] in members)
        character_bonus = character_bonus * game.rules["event_bonus_character_rate_scale"] // 1000
        threshold = min(1000, number(current["probability"], 0, 1000) + character_bonus)
        if chooser.randrange(1000) >= threshold:
            current["probability"] = min(1000, current["probability"] + number(definition["increase_probability"], 0, 1000))
            continue
        selected = chooser.choice(sorted(candidates, key=lambda row: row["id"]))
        previous = find(game.state, "t_event_bonus_quest_stages", m_event_id=event_id, stage_id=selected["stage_id"])
        current.update(probability=definition["default_probability"], m_event_bonus_quest_stage_id=selected["id"],
                       can_skip=int(previous is not None and previous["can_skip"]))
        appeared.append({"m_event_bonus_quest_stage_id": selected["id"]})
    return appeared


def pending(game, event_id):
    require_event(game, event_id)
    record = bonus_record(game, event_id)
    if not record["m_event_bonus_quest_stage_id"]:
        raise GameplayError("No bonus quest is currently available")
    definition = game.catalog.get("m_event_bonus_quest_stages", record["m_event_bonus_quest_stage_id"])
    return record, definition


def party_characters(game, party_no):
    party = find(game.state, "t_exploration_parties", party_no=party_no)
    identifiers = {row["t_character_id"] for row in rows(game.state, "t_exploration_party_characters")
                   if party and row["t_exploration_party_id"] == party["id"] and row["t_character_id"]}
    return [row["m_character_id"] for row in rows(game.state, "t_characters") if row["id"] in identifiers]


def start(game, body):
    event_id = number(body["m_event_id"])
    _, definition = pending(game, event_id)
    party_no = number(body["party_no"], 1, 10)
    return create_session(game, definition, kind="event_bonus", quest_id=definition["id"], party_no=party_no,
                          context={"event_id": event_id, "kind": "event_bonus", "party_characters": party_characters(game, party_no)}, result_schema="exploration")


def finish(game, session, stage_results, *, preserve_skip=False):
    event_id = session["context"]["event_id"]
    current = bonus_record(game, event_id)
    definition = game.catalog.get("m_event_bonus_quest_stages", session["quest_id"])
    if current["m_event_bonus_quest_stage_id"] != session["quest_id"]:
        raise GameplayError("Bonus quest no longer matches the active battle")
    status = rows(game.state, "t_statuses")[0]
    before_exp, before_gold = status["character_exp"], status["gold"]
    result = settle_battle(game, session, stage_results)["battle_result"]
    rewards = [row for row in game.catalog.rows("m_event_bonus_quest_rewards") if row["m_event_bonus_quest_stage_id"] == session["quest_id"]]
    rewards = boost_event_rewards(game, event_id, rewards, context_characters(session["context"]))
    result["clear_rewards"] = grant_display_rewards(game, rewards)
    result.update(grant_gold=status["gold"] - before_gold, grant_character_exp=status["character_exp"] - before_exp,
                  before_character_exp=before_exp, after_character_exp=status["character_exp"], is_explore_start=0)
    result.pop("event_drops", None)
    if not preserve_skip:
        put(game.state, "t_event_bonus_quest_stages", {"m_event_id": event_id, "stage_id": definition["stage_id"]}, {
            "can_skip": 1, "skip_extra_drop_probability": stored_drop_probability(game, session["quest_id"], kind="event_bonus", ability_bonus=session.get("ability_extra_drop", 0)),
            "skip_additional_drop_rate": extra_drop_bonus(game), "skip_party_characters": session["context"]["party_characters"]}, game.user_id)
    current.update(m_event_bonus_quest_stage_id=None, can_skip=0)
    record_event_clear(game, game.catalog.get("m_events", event_id), session["quest_id"], stage_results, session["context"])
    return result


def clear(game, body):
    digest = operation_digest("api/event/bonus-quest/clear-quest", "POST", body)
    data = battle_state(game)
    if data["active"] is None and data["last_result"] and data["last_result"]["digest"] == digest:
        return copy.deepcopy(data["last_result"]["response"])
    session = data["active"]
    if session is None or session["kind"] != "event_bonus":
        raise GameplayError("No bonus quest battle is active")
    result = finish(game, session, body["stage_results"])
    data["active"] = None
    data["last_result"] = {"digest": digest, "response": copy.deepcopy(result)}
    return result


def close(game, body):
    number(body["play_time"], 0, 86400)
    data = battle_state(game)
    if data["active"] is not None and data["active"]["kind"] != "event_bonus":
        raise GameplayError("A different battle is active")
    data["active"] = None
    return session_view(None)


def give_up(game, body):
    event_id = number(body["m_event_id"])
    require_event(game, event_id, allow_closed=True)
    data = battle_state(game)
    if data["active"] is not None and data["active"]["kind"] == "event_bonus" and data["active"]["context"]["event_id"] == event_id:
        raise GameplayError("Close the bonus quest battle before giving it up")
    bonus_record(game, event_id).update(m_event_bonus_quest_stage_id=None, can_skip=0)
    return {}


def skip(game, body):
    event_id = number(body["m_event_id"])
    data = battle_state(game)
    digest = operation_digest("api/event/bonus-quest/skip-quest", "POST", body)
    current = bonus_record(game, event_id)
    if not current["m_event_bonus_quest_stage_id"] and data["last_result"] and data["last_result"]["digest"] == digest:
        return copy.deepcopy(data["last_result"]["response"])
    if data["active"] is not None:
        raise GameplayError("A battle is already active")
    _, definition = pending(game, event_id)
    previous = find(game.state, "t_event_bonus_quest_stages", m_event_id=event_id, stage_id=definition["stage_id"])
    if previous is None or not previous["can_skip"]:
        raise GameplayError("Clear this bonus quest before skipping it")
    create_session(game, definition, kind="event_bonus", quest_id=definition["id"], party_no=1, validate_members=False,
                   context={"event_id": event_id, "kind": "event_bonus", "is_skip": True,
                            "party_characters": list(previous["skip_party_characters"])}, result_schema="exploration")
    session = data["active"]
    result_data = skipped_result(session["stage_detail"], session["stage_type"])
    if session["stage_type"] == 2:
        result_data.update(total_damage=0, passed_floor_parts=[], clear_rank=0)
    result = finish(game, session, result_data, preserve_skip=True)
    result.pop("is_explore_start", None)
    data["active"] = None
    data["last_result"] = {"digest": digest, "response": copy.deepcopy(result)}
    return result


EVENT_BONUS_ROUTES = {
    "api/event/bonus-quest/start-quest": route("POST", "m_event_id party_no", start),
    "api/event/bonus-quest/clear-quest": route("POST", "stage_results", clear),
    "api/event/bonus-quest/close-quest": route("POST", "play_time", close),
    "api/event/bonus-quest/surrender-quest": route("POST", "play_time", close),
    "api/event/bonus-quest/giveup": route("POST", "m_event_id", give_up),
    "api/event/bonus-quest/skip-quest": route("POST", "m_event_id", skip),
}
