from __future__ import annotations

import copy

from .gameplay_battle_rewards import grant_display_rewards, settle_battle, validate_stage_results
from .gameplay_battle_session import battle_state, create_session, session_view
from .gameplay_catalog import GameplayError, number
from .gameplay_economy import consume
from .gameplay_disaster_party import formation_fuel
from .gameplay_events import event_data, event_supply, record_event_clear, require_event
from .gameplay_event_battle_common import active, context, elapsed, epoch, finish, recovery_counter, replay
from .gameplay_main_repeat import merged_drops, skipped_result
from .gameplay_receipts import operation_digest
from .gameplay_presents import enqueue
from .gameplay_records import find, put, rows
from .gameplay_routes import route


def mutant_data(game, event_id):
    return event_data(game, event_id).setdefault("mutant", {"quests": {}, "best_score": 0, "best_detail": [], "ranking_claimed": False})


def quest_data(game, definition):
    return mutant_data(game, definition["m_event_id"])["quests"].setdefault(str(definition["id"]), {
        "version": 1, "damage": 0, "details": [], "clears": 0, "last_party_no": 1})


def phases(game, definition):
    stage = game.catalog.get("m_defend_stages", definition["stage_id"])
    enemy = game.catalog.get("m_enemies", stage["m_enemy_id"])
    entries = sorted([row for row in game.catalog.rows("m_disaster_boss_phases") if row["m_enemy_id"] == enemy["id"]], key=lambda row: row["phase"])
    if not entries or [row["phase"] for row in entries] != list(range(1, len(entries) + 1)):
        raise GameplayError("Mutant boss phases are missing or non-contiguous", 503, 5036)
    return [game.catalog.get("m_enemy_parameters", row["m_enemy_parameter_id"])["hp"] for row in entries], enemy["enemy_unit_type"] == 1102


def record(game, identifier):
    return find(game.state, "t_event_disaster_quests", m_event_disaster_quest_id=identifier)


def released(game, identifier):
    required = [row["m_event_disaster_quest_id_restrict"] for row in game.catalog.rows("m_event_disaster_quest_restricts")
                if row["m_event_disaster_quest_id"] == identifier]
    return all((record(game, key) or {}).get("is_cleared") for key in required)


def prepare_mutant(game, event):
    event_supply(game, event["id"], initial=3, capacity=3, recovery=3)
    recovery_counter(game, event["id"], game.rules.get("event_mutant_recovery_limit", 3))
    for definition in game.catalog.rows("m_event_disaster_quests"):
        if definition["m_event_id"] != event["id"]:
            continue
        quest_data(game, definition)
        row = record(game, definition["id"])
        if row is None:
            hp, infinite = phases(game, definition)
            row = put(game.state, "t_event_disaster_quests", {"m_event_disaster_quest_id": definition["id"]}, {
                "is_cleared": 0, "status": 0, "challenge_count": 0, "phase_no": 1, "remain_hp": hp[0],
                "used_character_list": [], "skip_extra_drop_probability": 0, "skip_additional_drop_rate": 0, "skip_party_characters": []})
        if row["status"] == 0 and released(game, definition["id"]):
            row["status"] = 1


def quest(game, identifier):
    definition = game.catalog.get("m_event_disaster_quests", number(identifier))
    event = require_event(game, definition["m_event_id"], {2})
    prepare_mutant(game, event)
    row = record(game, identifier)
    if not row["status"]:
        raise GameplayError("Mutant quest is locked")
    return definition, event, row


def check(game, body):
    event = require_event(game, number(body["m_event_id"]), {2})
    prepare_mutant(game, event)
    for definition in game.catalog.rows("m_event_disaster_quests"):
        if definition["m_event_id"] == event["id"] and record(game, definition["id"])["status"] == 1:
            record(game, definition["id"])["status"] = 2
    return {}


def start(game, body, *, is_skip=False):
    definition, event, row = quest(game, body["m_event_disaster_quest_id"])
    party_no = number(body["party_no"], 1, 10)
    previous = battle_state(game)["active"]
    data = quest_data(game, definition)
    metadata = context(game, event, party_no)
    metadata.update(quest_version=data["version"], phase_no=row["phase_no"], remain_hp=row["remain_hp"])
    if is_skip:
        metadata["is_skip"] = True
    if previous is None:
        if row["challenge_count"] >= definition["challenge_limit"] or row["remain_hp"] <= 0:
            raise GameplayError("Reset this mutant encounter before another attempt")
        if definition["is_sortie_restrict"] and set(map(int, metadata["members"])) & set(row["used_character_list"]):
            raise GameplayError("A selected character has already fought this mutant encounter")
        if event_supply(game, event["id"])["amount"] < 1:
            raise GameplayError("No mutant attempts remain")
    response = create_session(game, definition, kind="event_mutant", quest_id=definition["id"], party_no=party_no,
                              context=metadata, defensive_type=3, defensive_quest_id=definition["id"])
    if previous is None:
        fuel = formation_fuel(game, party_no, definition["id"], disaster_type=3)
        if fuel:
            consume(game, [{"content_type": 30, "content_id": 130001, "amount": fuel}])
        row["challenge_count"] += 1
        event_supply(game, event["id"])["amount"] -= 1
    data["last_party_no"] = party_no
    return response


def validate_detail(session, details):
    if not isinstance(details, list) or len(details) > 10:
        raise GameplayError("Invalid mutant damage details", 400, 4007)
    seen = set()
    for row in details:
        if not isinstance(row, dict) or set(row) != {"m_character_id", "level", "damage"}:
            raise GameplayError("Invalid mutant damage entry", 400, 4007)
        identifier = number(row["m_character_id"])
        if identifier in seen or str(identifier) not in session["context"]["members"]:
            raise GameplayError("Mutant damage entry is not a frozen party member", 400, 4007)
        seen.add(identifier)
        if number(row["level"]) != session["context"]["members"][str(identifier)]:
            raise GameplayError("Mutant damage entry level differs from the frozen party", 400, 4007)
        number(row["damage"], 0, 2 ** 31 - 1)


def saved_battle(game):
    session, event = active(game, "event_mutant")
    definition = game.catalog.get("m_event_disaster_quests", session["quest_id"])
    row = record(game, definition["id"])
    if row is None:
        raise GameplayError("Saved mutant battle has no quest progress")
    if quest_data(game, definition)["version"] != session["context"]["quest_version"]:
        raise GameplayError("Mutant encounter was reset after this battle started")
    return session, event, definition, row


def current_battle(game, body):
    session, event, definition, row = saved_battle(game)
    validate_detail(session, body["battle_detail"])
    damage = number(body["boss_damage"], 0, 2 ** 31 - 1)
    return session, event, definition, row, damage


def apply_damage_record(game, session, definition, row, damage, details):
    data = quest_data(game, definition)
    data["damage"] = number(data["damage"] + damage, 0, 2 ** 63 - 1)
    data["details"].append(copy.deepcopy(details))
    data["details"] = data["details"][-3:]
    row["used_character_list"] = sorted(set(row["used_character_list"]) | set(map(int, session["context"]["members"])))
    row["skip_party_characters"] = list(map(int, session["context"]["members"]))
    overall = mutant_data(game, definition["m_event_id"])
    if definition["division"] == 2 and data["damage"] > overall["best_score"]:
        overall["best_score"] = data["damage"]
        overall["best_detail"] = [{"index": index, **entry} for index, entries in enumerate(data["details"], 1) for entry in entries]


def progress_damage(game, definition, session, target_phase, target_hp):
    hp, infinite = phases(game, definition)
    first_phase = session["context"]["phase_no"]
    target_phase = number(target_phase, first_phase, first_phase + 1000 if infinite else len(hp))
    maximum = hp[(target_phase - 1) % len(hp)]
    target_hp = number(target_hp, 1, maximum)
    consumed = session["context"]["remain_hp"] - target_hp if target_phase == first_phase else session["context"]["remain_hp"] + sum(
        hp[(phase - 1) % len(hp)] for phase in range(first_phase + 1, target_phase)) + maximum - target_hp
    if consumed < 0:
        raise GameplayError("Mutant remaining HP cannot increase within an attempt", 400, 4007)
    return consumed


def close(game, body):
    digest = operation_digest("api/event/disaster/close-quest", "POST", body)
    previous = replay(game, digest)
    if previous is not None:
        return previous
    session, event, definition, row, damage = current_battle(game, body)
    elapsed(game, session, body["play_time"])
    consumed = progress_damage(game, definition, session, body["phase_no"], body["remain_hp"])
    if damage < consumed:
        raise GameplayError("Mutant phase progress exceeds reported boss damage", 400, 4007)
    row.update(phase_no=body["phase_no"], remain_hp=body["remain_hp"])
    apply_damage_record(game, session, definition, row, damage, body["battle_detail"])
    return finish(game, digest, session_view(None))


def clear(game, body):
    digest = operation_digest("api/event/disaster/clear-quest", "POST", body)
    previous = replay(game, digest)
    if previous is not None:
        return previous
    session, event, definition, row, damage = current_battle(game, body)
    hp, infinite = phases(game, definition)
    required = session["context"]["remain_hp"] + sum(hp[session["context"]["phase_no"]:])
    validate_stage_results(session["stage_detail"], body["stage_results"], 2, time_limit=session["time_limit"], result_schema="disaster")
    if infinite or damage < required:
        raise GameplayError("Mutant clear does not defeat all remaining boss phases", 400, 4007)
    result = settle_battle(game, session, body["stage_results"])["battle_result"]
    first = [entry for entry in game.catalog.rows("m_event_disaster_quest_rewards")
             if entry["m_event_disaster_quest_id"] == definition["id"] and (not entry["is_first_reward"] or not row["is_cleared"])]
    result["first_rewards"] = grant_display_rewards(game, [entry for entry in first if entry["is_first_reward"]])
    result["clear_rewards"] = grant_display_rewards(game, [entry for entry in first if not entry["is_first_reward"]])
    row.update(is_cleared=1, status=99, phase_no=len(hp), remain_hp=0)
    apply_damage_record(game, session, definition, row, damage, body["battle_detail"])
    quest_data(game, definition)["clears"] += 1
    record_event_clear(game, event, definition["id"], {**body["stage_results"], "total_damage": damage}, session["context"])
    result.pop("event_drops", None)
    result.pop("grant_gold", None)
    prepare_mutant(game, event)
    return finish(game, digest, result)


def surrender(game, body):
    digest = operation_digest("api/event/disaster/surrender-quest", "POST", body)
    previous = replay(game, digest)
    if previous is not None:
        return previous
    session, event, definition, row = saved_battle(game)
    elapsed(game, session, body["play_time"])
    apply_damage_record(game, session, definition, row, 0, [])
    return finish(game, digest, session_view(None))


def on_battle_suspended(game, session):
    definition = game.catalog.get("m_event_disaster_quests", session["quest_id"])
    event = game.catalog.get("m_events", definition["m_event_id"])
    row = record(game, definition["id"])
    if (row is not None and session["context"].get("event_epoch") == epoch(game, event)
            and session["context"].get("quest_version") == quest_data(game, definition)["version"]):
        row["used_character_list"] = sorted(set(row["used_character_list"]) | set(map(int, session["context"]["members"])))


def reset(game, body):
    definition, event, row = quest(game, body["m_event_disaster_quest_id"])
    if battle_state(game)["active"] is not None:
        raise GameplayError("Finish the active battle before resetting a mutant encounter")
    data = quest_data(game, definition)
    hp, infinite = phases(game, definition)
    if row["challenge_count"] == 0 and row["phase_no"] == 1 and row["remain_hp"] == hp[0] and not row["used_character_list"] and not data["damage"]:
        return {}
    row.update(challenge_count=0, phase_no=1, remain_hp=hp[0], used_character_list=[])
    data.update(version=data["version"] + 1, damage=0, details=[])
    return {}


def recovery(game, body):
    event = require_event(game, number(body["m_event_id"]), {2})
    prepare_mutant(game, event)
    counter, display = recovery_counter(game, event["id"], game.rules.get("event_mutant_recovery_limit", 3))
    supply = event_supply(game, event["id"])
    if display["amount"] < 1 or supply["amount"] >= supply["capacity"]:
        raise GameplayError("Mutant recovery is exhausted or attempts are full")
    consume(game, [{"content_type": 10, "content_id": 0, "amount": game.rules.get("event_mutant_recovery_cost", 50)}])
    counter["used"] += 1
    display["amount"] -= 1
    supply["amount"] += 1
    return {}


def ranking(game, body):
    event = require_event(game, number(body["m_event_id"]), {2}, allow_closed=True)
    data = mutant_data(game, event["id"])
    status = rows(game.state, "t_statuses")[0]
    profile = rows(game.state, "t_profiles")[0]
    participating = data["best_score"] > 0
    own = {"rank": 1 if participating else 0, "percentile": 10000 if participating else 0,
           "score": data["best_score"], "battle_detail": copy.deepcopy(data["best_detail"])}
    entries = [{"rank": 1, "user_id": game.user_id, "user_name": status["name"], "user_rank": status["user_rank"],
                "favorite_m_character_skin_id": profile["favorite_m_character_skin_id"], "score": data["best_score"]}] if participating else []
    claims = data.setdefault("ranking_claims", {})
    key = f"{event['id']}:{epoch(game, event)}"
    if participating and game.rules.get("event_mutant_ranking_reward_on_query", 1) and key not in claims:
        candidates = [row for row in game.catalog.rows("m_event_disaster_rankings")
                      if row["m_event_id"] == event["id"] and row["reward_type"] == 1 and row["range_to"] >= 1]
        if not candidates:
            raise GameplayError("Local mutant ranking reward tier is missing", 503, 5036)
        tier = min(candidates, key=lambda row: row["range_to"])
        rewards = [row for row in game.catalog.rows("m_event_disaster_ranking_rewards") if row["reward_group_id"] == tier["reward_group_id"]]
        if not rewards:
            raise GameplayError("Local mutant ranking reward contents are missing", 503, 5036)
        enqueue(game, [{field: row[field] for field in ("content_type", "content_id", "amount")} for row in rewards],
                title="Local mutant ranking")
        claims[key] = {"rank": 1, "reward_group_id": tier["reward_group_id"], "at": game.now.isoformat()}
    return {"ranking": entries, "own": own}


def skip(game, body):
    definition, event, row = quest(game, body["m_event_disaster_quest_id"])
    count = number(body["skip_count"], 1, 100)
    if not row["is_cleared"] or battle_state(game)["active"] is not None:
        raise GameplayError("Mutant quest must be cleared and no battle may be active")
    if event_supply(game, event["id"])["amount"] < count:
        raise GameplayError("Insufficient mutant attempts")
    status = rows(game.state, "t_statuses")[0]
    before_exp, before_gold = status["character_exp"], status["gold"]
    drops, rewards = [], []
    for _ in range(count):
        reset(game, {"m_event_disaster_quest_id": definition["id"]})
        start(game, {"m_event_disaster_quest_id": definition["id"], "party_no": quest_data(game, definition)["last_party_no"]}, is_skip=True)
        session = battle_state(game)["active"]
        hp, infinite = phases(game, definition)
        result = clear(game, {"stage_results": skipped_result(session["stage_detail"], 2), "boss_damage": sum(hp), "battle_detail": []})
        drops.extend(result["drops"])
        rewards.extend(result["clear_rewards"])
    return {"grant_gold": status["gold"] - before_gold, "grant_character_exp": status["character_exp"] - before_exp,
            "before_character_exp": before_exp, "after_character_exp": status["character_exp"],
            "drops": merged_drops(drops), "first_rewards": [], "clear_rewards": merged_drops(rewards)}


MUTANT_ROUTES = {
    "api/event/disaster/check-quest": route("POST", "m_event_id", check),
    "api/event/disaster/start-quest": route("POST", "m_event_disaster_quest_id party_no", start),
    "api/event/disaster/clear-quest": route("POST", "stage_results boss_damage battle_detail", clear),
    "api/event/disaster/close-quest": route("POST", "play_time phase_no remain_hp boss_damage battle_detail", close),
    "api/event/disaster/surrender-quest": route("POST", "play_time", surrender),
    "api/event/disaster/reset-quest": route("POST", "m_event_disaster_quest_id", reset),
    "api/event/disaster/recovery": route("POST", "m_event_id", recovery),
    "api/event/disaster/skip-quest": route("POST", "m_event_disaster_quest_id skip_count", skip),
    "api/event/disaster/ranking/{0}": route("GET", "m_event_id", ranking, "m_event_id"),
}
