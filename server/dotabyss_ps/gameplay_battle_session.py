from __future__ import annotations

import copy
import json

from .gameplay_catalog import GameplayError, number, parse_timestamp
from .gameplay_battle_modes import battle_mode
from .gameplay_party_state import layout, validate_layout
from .gameplay_receipts import feature_data, operation_digest
from .gameplay_records import find, rows
from .gameplay_drop_bonuses import party_bonus, saved_bonus
from .gameplay_routes import route
from .gameplay_stamina import settle_stamina
from .gameplay_statistics import record
from .gameplay_story_progress import available_quests, clear_quest, prepare_story_state, quest_row, sequences


def battle_state(game):
    data = feature_data(game, "battle")
    if not data:
        data.update(next_id=1, active=None, last_result=None, tutorial_completed=[])
    if set(data) != {"next_id", "active", "last_result", "tutorial_completed"}:
        raise GameplayError("Stored battle state is invalid")
    number(data["next_id"], 1, 2 ** 31 - 1)
    if data["active"] is not None:
        session = data["active"]
        required = {"id", "kind", "quest_type", "quest_id", "m_chapter_quest_id", "m_chapter_area_id", "m_tutorial_quest_id", "battle_id",
                    "stage_type", "stage_id", "stage_detail", "reward_index", "party_no", "sequence", "start_at", "stamina_spent", "time_limit"}
        optional = {"settlement", "context", "party_detail", "session_detail", "result_schema", "party_characters", "ability_extra_drop"}
        if not isinstance(session, dict) or set(session) - required - optional or required - set(session):
            raise GameplayError("Stored battle session is invalid")
        mode = battle_mode(session["kind"])
        if session["quest_type"] != mode.quest_type or session["stage_type"] not in (1, 2, 3):
            raise GameplayError("Stored battle session type is invalid")
        for field in ("id", "quest_type", "quest_id", "stage_type", "stage_id"):
            number(session[field])
        number(session["party_no"], 0 if session["kind"] in ("tutorial", "coaching", "recollection", "tryout", "event_training") else 1, 10)
        for field in ("m_chapter_quest_id", "m_chapter_area_id", "m_tutorial_quest_id", "battle_id", "sequence", "stamina_spent", "time_limit"):
            number(session[field], 0)
        if not isinstance(session["stage_detail"], dict) or not isinstance(session["reward_index"], dict):
            raise GameplayError("Stored battle stage is invalid")
        for field in ("context", "party_detail", "session_detail"):
            if session.get(field) is not None and not isinstance(session[field], dict):
                raise GameplayError("Stored battle metadata is invalid")
        if session.get("result_schema") not in (None, "exploration", "disaster"):
            raise GameplayError("Stored battle result schema is invalid")
        if "ability_extra_drop" in session:
            number(session["ability_extra_drop"], 0, 1000)
        try:
            parse_timestamp(session["start_at"])
        except (TypeError, ValueError) as error:
            raise GameplayError("Stored battle start time is invalid") from error
    last = data["last_result"]
    if last is not None and (not isinstance(last, dict) or set(last) != {"digest", "response"} or not isinstance(last["response"], dict)):
        raise GameplayError("Stored battle receipt is invalid")
    if not isinstance(data["tutorial_completed"], list) or any(type(value) is not int or value not in (1, 2, 3) for value in data["tutorial_completed"]):
        raise GameplayError("Stored tutorial battle history is invalid")
    return data


def session_view(session):
    if session is None:
        return {"status": 0, "quest_type": 0, "quest_id": 0, "stage_type": 0,
                "stage_detail": None, "party_detail": None, "session_detail": None, "start_at": None}
    return {"status": 1, "quest_type": session["quest_type"], "quest_id": session["quest_id"], "stage_type": session["stage_type"],
            "stage_detail": json.dumps(session["stage_detail"], ensure_ascii=False, separators=(",", ":"), allow_nan=False),
            "party_detail": json.dumps(session.get("party_detail") or {"party_no": session["party_no"]}, ensure_ascii=False, separators=(",", ":"), allow_nan=False),
            "session_detail": json.dumps(session["session_detail"], ensure_ascii=False, separators=(",", ":"), allow_nan=False) if session.get("session_detail") is not None else None,
            "start_at": session["start_at"]}


def status(game, body):
    return session_view(battle_state(game)["active"])


def validate_party(game, party_no, fixed_party_id, tutorial=False):
    number(party_no, 0 if tutorial and fixed_party_id else 1, rows(game.state, "t_statuses")[0]["max_party_count"])
    if fixed_party_id:
        game.catalog.get("m_fixed_parties", fixed_party_id)
        return
    party = find(game.state, "t_exploration_parties", party_no=party_no)
    if party is None:
        raise GameplayError("Battle party does not exist")
    members, gems = layout(game, party["id"])
    if not any(member["t_character_id"] for member in members):
        raise GameplayError("Battle party is empty")
    validate_layout(game, members, gems)


def create_session(game, definition, *, kind, quest_id, party_no, chapter_quest_id=0, chapter_area_id=0, sequence=0, stamina=0,
                   context=None, result_schema=None, party_detail=None, session_detail=None, stage_detail=None,
                   reward_index=None, validate_members=True, defensive_type=None, defensive_quest_id=0, ability_bonus=None):
    from .gameplay_battle_stages import build_stage
    from .gameplay_battle_rewards import attach_drops

    mode = battle_mode(kind)
    context = {} if context is None else context
    if not isinstance(context, dict) or any(value is not None and not isinstance(value, dict) for value in (party_detail, session_detail, stage_detail, reward_index)):
        raise GameplayError("Invalid battle metadata")
    result_schema = mode.result_schema if result_schema is None else result_schema
    if result_schema not in (None, "exploration", "disaster"):
        raise GameplayError("Invalid battle result schema")
    event_id = context.get("event_id", context.get("m_event_id", 0))
    if event_id and kind.startswith("event_"):
        from .gameplay_event_effects import context_characters
        if "members" in context or "party_characters" in context:
            context = {**context, "party_characters": context_characters(context)}
        event = find(game.state, "t_events", m_event_id=event_id)
        context = {**context, "effects_enabled": event["is_event_stage_effect_enabled"] if event else 0}
    data = battle_state(game)
    active = data["active"]
    if active is not None and active["kind"].startswith("event_") and "members" in active.get("context", {}):
        from .gameplay_event_effects import context_characters
        active["context"]["party_characters"] = context_characters(active["context"])
    if active is not None:
        if (active["kind"] == kind and active["quest_id"] == quest_id and active["m_chapter_quest_id"] == chapter_quest_id
                and active["m_chapter_area_id"] == chapter_area_id and active["party_no"] == party_no and active["sequence"] == sequence
                and active.get("context", {}) == context):
            return session_view(active)
        raise GameplayError("Finish or abandon the current battle before starting another")
    if validate_members:
        validate_party(game, party_no, definition.get("m_fixed_party_id", 0), kind in ("tutorial", "coaching", "recollection", "tryout", "event_training"))
    else:
        number(party_no, 1, 10)
    identifier = number(data["next_id"], 1, 2 ** 31 - 2)
    quest_type = mode.quest_type
    stage = copy.deepcopy(stage_detail) if stage_detail is not None else build_stage(
        game, definition["stage_type"], definition["stage_id"], identifier, party_no=party_no,
        fixed_fort_id=definition.get("m_fixed_fort_id", 0), kind=kind, quest_id=quest_id,
        defensive_type=defensive_type, defensive_quest_id=defensive_quest_id,
        defensive_boss_id=context.get("boss_id", 0) if kind == "disaster" else 0)
    if ability_bonus is None:
        ability_bonus = (saved_bonus(game, kind, definition["id"], party_no) if context.get("is_skip")
                         else 0 if definition.get("m_fixed_party_id", 0) or party_detail is not None
                         else party_bonus(game, party_no))
    ability_bonus = number(ability_bonus, 0, 1000)
    rewards = copy.deepcopy(reward_index) if reward_index is not None else attach_drops(
        game, definition["id"], stage, identifier, kind=kind, ability_bonus=ability_bonus) if mode.drop_table else {}
    if event_id and kind.startswith("event_"):
        from .gameplay_event_effects import boost_event_drops, context_characters

        boost_event_drops(game, event_id, stage, rewards, context_characters(context), identifier)
    spent = number(stamina, 0, 999)
    if spent:
        parameter = settle_stamina(game.state, game.catalog, game.user_id, game.now)
        if parameter["amount"] < spent:
            raise GameplayError("Insufficient stamina", 409, 4093)
        parameter["amount"] -= spent
        record(game.progress, game.now, 130001, spent)
    data["next_id"] = identifier + 1
    data["active"] = {
        "id": identifier, "kind": kind, "quest_type": quest_type, "quest_id": quest_id,
        "m_chapter_quest_id": chapter_quest_id, "m_chapter_area_id": chapter_area_id, "m_tutorial_quest_id": quest_id if kind == "tutorial" else 0,
        "battle_id": definition["id"], "stage_type": definition["stage_type"], "stage_id": definition["stage_id"],
        "stage_detail": stage, "reward_index": rewards, "party_no": party_no, "sequence": sequence,
        "start_at": game.now.isoformat(), "stamina_spent": spent, "time_limit": definition.get("time_limit", 0),
        "context": copy.deepcopy(context), "result_schema": result_schema, "ability_extra_drop": ability_bonus,
        "party_detail": copy.deepcopy(party_detail), "session_detail": copy.deepcopy(session_detail),
    }
    if kind in ("main", "repeat"):
        prepare_story_state(game)["last_party_no"] = party_no
    if kind in ("main", "repeat", "side_story"):
        from .gameplay_event_battle_link import battle_characters

        data["active"]["party_characters"] = battle_characters(game, party_no)
    return session_view(data["active"])


def start_main(game, body):
    quest_id, party_no = number(body["m_chapter_quest_id"]), number(body["party_no"], 1, 10)
    quest = game.catalog.get("m_chapter_quests", quest_id)
    row = quest_row(game, quest_id)
    step = next((step for step in sequences(game, quest_id) if step["sequence"] == row["sequence"]), None)
    if step is None or not step["m_chapter_quest_battle_id"]:
        raise GameplayError("The current main-story sequence is not a battle")
    definition = game.catalog.get("m_chapter_quest_battles", step["m_chapter_quest_battle_id"])
    return create_session(game, definition, kind="main", quest_id=definition["id"], party_no=party_no,
                          chapter_quest_id=quest_id, chapter_area_id=quest["m_chapter_area_id"], sequence=step["sequence"], stamina=quest["stamina"])


def start_tutorial(game, body):
    from .gameplay_tutorial import validate_tutorial_battle

    quest_id, party_no = number(body["m_tutorial_quest_id"]), number(body["party_no"], 0, 10)
    validate_tutorial_battle(game, quest_id)
    definition = game.catalog.get("m_tutorial_quests", quest_id)
    return create_session(game, definition, kind="tutorial", quest_id=quest_id, party_no=party_no)


def finish_tutorial_battle(game, quest_id):
    data = battle_state(game)
    active = data["active"]
    if active is None:
        if quest_id in data["tutorial_completed"]:
            return
        raise GameplayError("The required tutorial battle has not been started")
    if active["kind"] != "tutorial" or active["quest_id"] != quest_id:
        raise GameplayError("A different battle is still active")
    if quest_id not in data["tutorial_completed"]:
        data["tutorial_completed"].append(quest_id)
    data["active"] = None


def clear_main(game, body):
    from .gameplay_battle_rewards import settle_battle

    quest_id = number(body["m_chapter_quest_id"])
    digest = operation_digest("api/main-story/clear-battle", "POST", body)
    data = battle_state(game)
    if data["last_result"] is not None and data["last_result"]["digest"] == digest:
        return copy.deepcopy(data["last_result"]["response"])
    active = data["active"]
    if active is None or active["kind"] != "main" or active["m_chapter_quest_id"] != quest_id:
        raise GameplayError("No matching main-story battle is active")
    row = quest_row(game, quest_id)
    if row["sequence"] != active["sequence"]:
        raise GameplayError("Main-story progress changed during battle")
    before_available = available_quests(game)
    from .gameplay_effects import settle_before_effect_change
    settle_before_effect_change(game)
    result = settle_battle(game, active, body["stage_results"])
    row["sequence"] += 1
    data["active"] = None
    if row["sequence"] > sequences(game, quest_id)[-1]["sequence"]:
        result.update(clear_quest(game, {"m_chapter_quest_id": quest_id}, before_available))
    data["last_result"] = {"digest": digest, "response": copy.deepcopy(result)}
    return result


def abandon(game, body):
    number(body["play_time"], 0, 86400000)
    data = battle_state(game)
    if data["active"] is not None and data["active"]["kind"] != "main":
        raise GameplayError("This endpoint cannot close a tutorial battle")
    data["active"] = None
    return session_view(None)


def suspend(game, body):
    number(body["is_force"], 0, 1)
    data = battle_state(game)
    if data["active"] is not None and data["active"]["kind"] == "nether":
        from .gameplay_nether_battle import on_battle_suspended

        on_battle_suspended(game, data["active"])
    elif data["active"] is not None and data["active"]["kind"] == "event_mutant":
        from .gameplay_event_mutant import on_battle_suspended

        on_battle_suspended(game, data["active"])
    data["active"] = None
    return session_view(None)


def replay_response(game, path, response):
    has_session_view = all(field in response for field in ("quest_type", "stage_detail", "party_detail", "start_at"))
    if has_session_view and path.endswith(("/start-battle", "/start-quest")):
        active = battle_state(game)["active"]
        if (active is None or active["quest_id"] != response.get("quest_id") or active["start_at"] != response.get("start_at")
                or session_view(active)["stage_detail"] != response.get("stage_detail")):
            raise GameplayError("This battle start request belongs to an ended session")
    result = copy.deepcopy(response)
    if has_session_view and (path in ("api/battle-session/status", "api/battle-session/suspend") or path.endswith(("/close-battle", "/surrender-battle", "/close-quest", "/surrender-quest"))):
        result.update(session_view(battle_state(game)["active"]))
    return result


BATTLE_ROUTES = {
    "api/battle-session/status": route("GET", "", status),
    "api/battle-session/suspend": route("POST", "is_force", suspend),
    "api/main-story/start-battle": route("POST", "m_chapter_quest_id party_no", start_main),
    "api/main-story/clear-battle": route("POST", "m_chapter_quest_id stage_results", clear_main),
    "api/main-story/close-battle": route("POST", "play_time", abandon),
    "api/main-story/surrender-battle": route("POST", "play_time", abandon),
    "api/main-story/clear": route("POST", "m_chapter_quest_id", clear_quest),
    "api/tutorial/start-quest": route("POST", "m_tutorial_quest_id party_no", start_tutorial),
}
