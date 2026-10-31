from __future__ import annotations

import copy

from .gameplay_battle_rewards import grant_display_rewards, settle_battle
from .gameplay_battle_session import battle_state, create_session, session_view
from .gameplay_catalog import GameplayError, identifiers, number
from .gameplay_library import chapter_complete
from .gameplay_receipts import feature_data, operation_digest
from .gameplay_records import find, put, rows
from .gameplay_routes import route

KINDS = ("coaching", "recollection", "tryout")


def progress(game):
    data = feature_data(game, "training")
    if not data:
        data["reward_claims"] = {}
    if set(data) != {"reward_claims"} or not isinstance(data["reward_claims"], dict):
        raise GameplayError("Stored training progress is invalid")
    return data


def quest_record(game, kind, identifier):
    return find(game.state, f"t_{kind}_quests", **{f"m_{kind}_quest_id": identifier})


def released(game, kind, identifier):
    for condition in game.catalog.rows(f"m_{kind}_quest_restricts"):
        if condition[f"m_{kind}_quest_id"] != identifier:
            continue
        target = condition["restrict_param_1"]
        if condition["restrict_type"] == 1:
            record = quest_record(game, kind, target)
            if record is None or record["status"] != 99:
                return False
        elif condition["restrict_type"] == 2:
            if not chapter_complete(game, target):
                return False
        else:
            raise GameplayError("Unsupported training release condition", 503, 5036)
    return True


def prepare_training(game):
    if "m_coaching_quests" not in game.catalog.tables:
        return
    claims = progress(game)["reward_claims"]
    for kind in KINDS:
        for definition in game.catalog.rows(f"m_{kind}_quests"):
            identifier = definition["id"]
            record = quest_record(game, kind, identifier)
            if record is None:
                extra = {"is_exempted": 0} if kind == "coaching" else {"high_score": 0} if kind == "tryout" else {}
                record = put(game.state, f"t_{kind}_quests", {f"m_{kind}_quest_id": identifier}, {"status": 0, **extra}, game.user_id)
            if record["status"] == 99:
                if kind == "coaching":
                    claims.setdefault(str(identifier), {"legacy": True})
                continue
            if record["status"] == 0 and released(game, kind, identifier):
                record["status"] = 1
            if kind == "coaching" and record["status"]:
                from .gameplay_disaster import is_disaster_quest_cleared

                if definition["ref_m_disaster_quest_id"] and is_disaster_quest_cleared(game, definition["ref_m_disaster_quest_id"]):
                    record["is_exempted"] = 1


def listing(game, body):
    prepare_training(game)
    return {"training_center_version": {kind: 1 for kind in KINDS}}


def check(game, body):
    prepare_training(game)
    selected = []
    for kind in KINDS:
        for identifier in identifiers(body[f"m_{kind}_quest_ids"]):
            game.catalog.get(f"m_{kind}_quests", identifier)
            record = quest_record(game, kind, identifier)
            if record is None or record["status"] == 0:
                raise GameplayError("Training quest is not open")
            selected.append(record)
    for record in selected:
        if record["status"] == 1:
            record["status"] = 2
    return {}


def start(game, body, kind):
    prepare_training(game)
    identifier = number(body[f"m_{kind}_quest_id"])
    definition = game.catalog.get(f"m_{kind}_quests", identifier)
    record = quest_record(game, kind, identifier)
    if record is None or not record["status"]:
        raise GameplayError("Training quest is not open")
    return create_session(game, definition, kind=kind, quest_id=identifier,
                          party_no=number(body["party_no"], 0 if definition.get("m_fixed_party_id") else 1, 10),
                          context={}, result_schema="exploration")


def coaching_rewards(game, identifier):
    claims = progress(game)["reward_claims"]
    if str(identifier) in claims:
        return []
    rewards = [row for row in game.catalog.rows("m_coaching_quest_rewards") if row["m_coaching_quest_id"] == identifier]
    received = grant_display_rewards(game, rewards)
    claims[str(identifier)] = {"received_at": game.now.isoformat()}
    return received


def clear(game, body, kind):
    route_path = f"api/training-center/{kind}/clear-quest"
    digest = operation_digest(route_path, "POST", body)
    data = battle_state(game)
    if data["last_result"] is not None and data["last_result"]["digest"] == digest:
        return copy.deepcopy(data["last_result"]["response"])
    session = data["active"]
    if session is None or session["kind"] != kind:
        raise GameplayError("No matching training battle is active")
    status = rows(game.state, "t_statuses")[0]
    before_exp, before_gold = status["character_exp"], status["gold"]
    result = settle_battle(game, session, body["stage_results"])["battle_result"]
    record = quest_record(game, kind, session["quest_id"])
    if record is None:
        raise GameplayError("Training battle record is missing")
    if kind == "coaching":
        result["first_rewards"] = coaching_rewards(game, session["quest_id"])
    if kind == "tryout":
        record["high_score"] = max(record["high_score"], number(body["stage_results"]["total_damage"], 0, 2 ** 31 - 1))
    record["status"] = 99
    result.update(grant_gold=status["gold"] - before_gold, grant_character_exp=status["character_exp"] - before_exp,
                  before_character_exp=before_exp, after_character_exp=status["character_exp"])
    result.pop("event_drops", None)
    data["active"] = None
    prepare_training(game)
    data["last_result"] = {"digest": digest, "response": copy.deepcopy(result)}
    return result


def close(game, body, kind):
    data = battle_state(game)
    active = data["active"]
    if active is not None and active["kind"] != kind:
        raise GameplayError("A different battle is active")
    if kind == "tryout":
        result = body["stage_results"]
        if not isinstance(result, dict) or set(result) != {"play_time", "total_damage"}:
            raise GameplayError("Invalid training result", 400, 4007)
        elapsed = number(result["play_time"], 0, 86400)
        score = number(result["total_damage"], 0, 2 ** 31 - 1)
        if active is not None:
            if active["time_limit"] and elapsed > active["time_limit"] + 10:
                raise GameplayError("Training result exceeds the time limit", 400, 4007)
            record = quest_record(game, kind, active["quest_id"])
            record["high_score"] = max(record["high_score"], score)
    else:
        number(body["play_time"], 0, 86400)
    data["active"] = None
    return session_view(None)


def receive(game, body):
    prepare_training(game)
    selected = identifiers(body["m_coaching_quest_ids"])
    if not selected:
        raise GameplayError("No training rewards were selected", 400, 4007)
    for identifier in selected:
        game.catalog.get("m_coaching_quests", identifier)
        record = quest_record(game, "coaching", identifier)
        if record is None or record["status"] != 99 and not record["is_exempted"]:
            raise GameplayError("Training reward is not available")
    rewards = []
    for identifier in selected:
        rewards.extend(coaching_rewards(game, identifier))
        quest_record(game, "coaching", identifier)["status"] = 99
    prepare_training(game)
    return {"first_rewards": [{key: reward[key] for key in ("content_type", "content_id", "amount")} for reward in rewards]}


TRAINING_ROUTES = {
    "api/training-center": route("GET", "", listing),
    "api/training-center/check": route("POST", "m_coaching_quest_ids m_recollection_quest_ids m_tryout_quest_ids", check),
    "api/training-center/coaching/receive": route("POST", "m_coaching_quest_ids", receive),
}
for _kind in KINDS:
    TRAINING_ROUTES[f"api/training-center/{_kind}/start-quest"] = route("POST", f"m_{_kind}_quest_id party_no", lambda game, body, kind=_kind: start(game, body, kind))
    TRAINING_ROUTES[f"api/training-center/{_kind}/clear-quest"] = route("POST", "stage_results", lambda game, body, kind=_kind: clear(game, body, kind))
    for _action in ("close", "surrender"):
        TRAINING_ROUTES[f"api/training-center/{_kind}/{_action}-quest"] = route("POST", "stage_results" if _kind == "tryout" else "play_time", lambda game, body, kind=_kind: close(game, body, kind))
