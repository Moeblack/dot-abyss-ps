from __future__ import annotations

import copy

from .gameplay_battle_rewards import grant_display_rewards, settle_battle
from .gameplay_battle_session import battle_state, create_session, session_view
from .gameplay_catalog import GameplayError, number
from .gameplay_main_repeat import merged_drops, skipped_result
from .gameplay_mode_supplies import daily_supply, spend_supply
from .gameplay_receipts import feature_data, operation_digest
from .gameplay_records import find, put, rows
from .gameplay_routes import route
from .gameplay_statistics import record as record_statistic
from .gameplay_access import require_feature


def progress(game):
    data = feature_data(game, "union")
    if not data:
        data["first_claims"] = []
    if set(data) != {"first_claims"} or not isinstance(data["first_claims"], list):
        raise GameplayError("Stored union battle progress is invalid")
    return data


def prepare_union(game):
    if "m_union_request_stages" not in game.catalog.tables:
        return
    for kind in sorted({row["m_union_type_id"] for row in game.catalog.rows("m_union_request_stages")}):
        number(kind, 1, 5)
        if find(game.state, "t_union_requests", m_union_type_id=kind) is None:
            put(game.state, "t_union_requests", {"m_union_type_id": kind}, {"last_party_no": 1, "stage": 1})
        daily_supply(game, kind + 6, game.rules["union_daily_attempts"])


def union_record(game, kind):
    prepare_union(game)
    kind = number(kind, 1, 5)
    current = find(game.state, "t_union_requests", m_union_type_id=kind)
    if current is None:
        raise GameplayError("Unknown union battle type")
    weekday = (game.now.weekday() + 1) % 7
    if not any(row["m_union_type_id"] == kind and row["weekday"] % 7 == weekday for row in game.catalog.rows("m_union_request_opens")):
        raise GameplayError("This union battle is not open today")
    return current


def stage_rewards(game, definition, repeat):
    claims = progress(game)["first_claims"]
    first = []
    if not repeat and definition["id"] not in claims:
        first = [row for row in game.catalog.rows("m_union_request_rewards")
                 if row["m_union_request_stage_id"] == definition["id"] and not row["is_repeat_reward"]]
        claims.append(definition["id"])
    repeated = [row for row in game.catalog.rows("m_union_request_rewards")
                if row["m_union_request_stage_id"] == definition["id"] and row["is_repeat_reward"]]
    return grant_display_rewards(game, first), grant_display_rewards(game, repeated)


def record_attempts(game, kind, count=1):
    record_statistic(game.progress, game.now, 170001, count)
    record_statistic(game.progress, game.now, 170001, count, kind)


def start(game, body):
    identifier = number(body["m_union_request_stage_id"])
    active = battle_state(game)["active"]
    if active is None or active["kind"] != "union" or active["quest_id"] != identifier:
        require_feature(game, 4)
    definition = game.catalog.get("m_union_request_stages", identifier)
    current = union_record(game, definition["m_union_type_id"])
    if definition["stage"] != current["stage"]:
        raise GameplayError("Union battle stage is not the current stage")
    data = battle_state(game)
    starting = data["active"] is None
    if starting:
        spend_supply(game, definition["m_union_type_id"] + 6, game.rules["union_daily_attempts"])
    response = create_session(game, definition, kind="union", quest_id=identifier,
                              party_no=number(body["party_no"], 1, 10), context={"union_type": definition["m_union_type_id"]})
    if starting:
        record_attempts(game, definition["m_union_type_id"])
    current["last_party_no"] = body["party_no"]
    return response


def finish(game, session, stage_results, *, repeat=False):
    definition = game.catalog.get("m_union_request_stages", session["quest_id"])
    current = find(game.state, "t_union_requests", m_union_type_id=definition["m_union_type_id"])
    status = rows(game.state, "t_statuses")[0]
    before_exp, before_gold = status["character_exp"], status["gold"]
    result = settle_battle(game, session, stage_results)["battle_result"]
    first, repeated = stage_rewards(game, definition, repeat)
    result.update(first_rewards=first, clear_rewards=repeated, grant_gold=status["gold"] - before_gold,
                  grant_character_exp=status["character_exp"] - before_exp, before_character_exp=before_exp,
                  after_character_exp=status["character_exp"])
    result.pop("event_drops", None)
    if not repeat:
        current["stage"] = max(current["stage"], definition["stage"] + 1)
    result["is_explore_start"] = 0
    return result


def clear(game, body):
    digest = operation_digest("api/union-request/clear-quest", "POST", body)
    data = battle_state(game)
    previous = data["last_result"]
    if previous is not None and previous["digest"] == digest:
        return copy.deepcopy(previous["response"])
    session = data["active"]
    if session is None or session["kind"] != "union":
        raise GameplayError("No union battle is active")
    result = finish(game, session, body["stage_results"])
    data["active"] = None
    data["last_result"] = {"digest": digest, "response": copy.deepcopy(result)}
    return result


def close(game, body):
    number(body["play_time"], 0, 86400)
    data = battle_state(game)
    if data["active"] is not None and data["active"]["kind"] != "union":
        raise GameplayError("A different battle is active")
    data["active"] = None
    return session_view(None)


def skip(game, body):
    require_feature(game, 4)
    kind, count = number(body["union_type"], 1, 5), number(body["exec_count"], 1, 100)
    current = union_record(game, kind)
    stages = [row for row in game.catalog.rows("m_union_request_stages") if row["m_union_type_id"] == kind and row["stage"] < current["stage"]]
    if not stages:
        raise GameplayError("Clear a union stage before using repeat")
    definition = max(stages, key=lambda row: row["stage"])
    data = battle_state(game)
    if data["active"] is not None:
        raise GameplayError("A battle is already active")
    spend_supply(game, kind + 6, game.rules["union_daily_attempts"], count)
    status = rows(game.state, "t_statuses")[0]
    before_exp, before_gold = status["character_exp"], status["gold"]
    drops, rewards = [], []
    for _ in range(count):
        create_session(game, definition, kind="union", quest_id=definition["id"], party_no=current["last_party_no"],
                       context={"union_type": kind}, validate_members=False)
        session = data["active"]
        result = finish(game, session, skipped_result(session["stage_detail"], session["stage_type"]), repeat=True)
        drops.extend(result["drops"] or [])
        rewards.extend(result["clear_rewards"] or [])
        data["active"] = None
    record_attempts(game, kind, count)
    return {"grant_gold": status["gold"] - before_gold, "grant_character_exp": status["character_exp"] - before_exp,
            "before_character_exp": before_exp, "after_character_exp": status["character_exp"],
            "drops": merged_drops(drops), "first_rewards": [], "clear_rewards": merged_drops(rewards)}


UNION_ROUTES = {
    "api/union-request/start-quest": route("POST", "m_union_request_stage_id party_no", start),
    "api/union-request/clear-quest": route("POST", "stage_results", clear),
    "api/union-request/close-quest": route("POST", "play_time", close),
    "api/union-request/surrender-quest": route("POST", "play_time", close),
    "api/union-request/repeat-quest": route("POST", "union_type exec_count", skip),
}
