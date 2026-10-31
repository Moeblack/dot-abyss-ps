from __future__ import annotations

from .gameplay_battle_rewards import grant_display_rewards, settle_battle
from .gameplay_battle_session import battle_state, create_session
from .gameplay_catalog import GameplayError, number
from .gameplay_economy import consume
from .gameplay_events import event_data, event_supply, record_event_clear, require_event
from .gameplay_event_battle_common import active, context, elapsed, finish, recovery_counter, replay
from .gameplay_main_repeat import merged_drops
from .gameplay_mining_stage import skipped_mining_result
from .gameplay_receipts import operation_digest
from .gameplay_records import find, put, rows
from .gameplay_routes import route


def miner_data(game, event_id):
    return event_data(game, event_id).setdefault("miner", {"clears": {}, "last_party_no": 1})


def record(game, identifier):
    return find(game.state, "t_event_miner_quests", m_event_miner_quest_id=identifier)


def released(game, identifier):
    for condition in game.catalog.rows("m_event_miner_quest_restricts"):
        if condition["m_event_miner_quest_id"] != identifier:
            continue
        if condition["restrict_type"] != 1:
            raise GameplayError("Unsupported mining quest restriction", 503, 5036)
        previous = record(game, condition["restrict_param_1"])
        if previous is None or previous["status"] != 99:
            return False
    return True


def prepare_miner(game, event):
    event_id = event["id"]
    definition = next((row for row in game.catalog.rows("m_event_miners") if row["m_event_id"] == event_id), None)
    if definition is None:
        raise GameplayError("Mining event supply master is missing", 503, 5036)
    miner_data(game, event_id)
    event_supply(game, event_id, initial=definition["initial_supply"], capacity=definition["capacity_supply"], recovery=definition["daily_recovery_supply"])
    recovery_counter(game, event_id, game.rules.get("event_miner_recovery_limit", 3))
    for quest in game.catalog.rows("m_event_miner_quests"):
        if quest["m_event_id"] != event_id:
            continue
        row = record(game, quest["id"])
        if row is None:
            row = put(game.state, "t_event_miner_quests", {"m_event_miner_quest_id": quest["id"]}, {
                "status": 0, "clear_time": 0, "clear_rank": 0, "skip_extra_drop_probability": 0,
                "skip_party_characters": [], "last_cleared_at": "", "created_at": game.now.strftime("%Y-%m-%d %H:%M:%S"), "updated_at": game.now.isoformat()})
        if row["status"] == 0 and released(game, quest["id"]):
            row["status"] = 1


def quest(game, identifier):
    definition = game.catalog.get("m_event_miner_quests", number(identifier))
    event = require_event(game, definition["m_event_id"], {1})
    prepare_miner(game, event)
    row = record(game, identifier)
    if not row["status"]:
        raise GameplayError("Mining quest is locked")
    return definition, event, row


def check(game, body):
    event = require_event(game, number(body["m_event_id"]), {1})
    prepare_miner(game, event)
    identifiers = {row["id"] for row in game.catalog.rows("m_event_miner_quests") if row["m_event_id"] == event["id"]}
    for row in rows(game.state, "t_event_miner_quests"):
        if row["m_event_miner_quest_id"] in identifiers and row["status"] == 1:
            row["status"] = 2
    return {}


def start(game, body, *, is_skip=False):
    definition, event, row = quest(game, body["m_event_miner_quest_id"])
    party_no = number(body["party_no"], 1, 10)
    previous = battle_state(game)["active"]
    supply = event_supply(game, event["id"])
    if previous is None and supply["amount"] < 1:
        raise GameplayError("No mining attempts remain")
    metadata = context(game, event, party_no)
    if is_skip:
        metadata["is_skip"] = True
    response = create_session(game, definition, kind="event_miner", quest_id=definition["id"], party_no=party_no,
                              context=metadata)
    if previous is None:
        supply["amount"] -= 1
    miner_data(game, event["id"])["last_party_no"] = party_no
    return response


def complete(game, session, results):
    _, event = active(game, "event_miner")
    definition = game.catalog.get("m_event_miner_quests", session["quest_id"])
    row = record(game, definition["id"])
    if row is None:
        raise GameplayError("Saved mining battle has no quest progress")
    status = rows(game.state, "t_statuses")[0]
    before_gold, before_exp = status["gold"], status["character_exp"]
    result = settle_battle(game, session, results)["battle_result"]
    rewards = [entry for entry in game.catalog.rows("m_event_miner_quest_rewards") if entry["m_event_miner_quest_id"] == definition["id"]]
    result["first_rewards"] = grant_display_rewards(game, [entry for entry in rewards if entry["is_first_reward"]]) if row["status"] != 99 else []
    result["clear_rewards"] = grant_display_rewards(game, [entry for entry in rewards if not entry["is_first_reward"]])
    row.update(status=99,
               clear_rank=max(row["clear_rank"], results["clear_rank"]), skip_party_characters=list(map(int, session["context"]["members"])),
               last_cleared_at=game.now.isoformat(), updated_at=game.now.isoformat())
    if results["play_time"] > 0:
        row["clear_time"] = min(row["clear_time"] or results["play_time"], results["play_time"])
    data = miner_data(game, event["id"])
    data["clears"][str(definition["id"])] = data["clears"].get(str(definition["id"]), 0) + 1
    record_event_clear(game, event, definition["id"], results, session["context"])
    prepare_miner(game, event)
    result.pop("event_drops", None)
    result.update(grant_gold=status["gold"] - before_gold, grant_character_exp=status["character_exp"] - before_exp,
                  before_character_exp=before_exp, after_character_exp=status["character_exp"])
    return result


def clear(game, body):
    digest = operation_digest("api/event/miner/clear-quest", "POST", body)
    previous = replay(game, digest)
    if previous is not None:
        return previous
    session, event = active(game, "event_miner")
    return finish(game, digest, complete(game, session, body["stage_results"]))


def abandon(game, body):
    digest = operation_digest("api/event/miner/abandon", "POST", body)
    previous = replay(game, digest)
    if previous is not None:
        return previous
    session, event = active(game, "event_miner")
    elapsed(game, session, body["play_time"])
    return finish(game, digest, {})


def recovery(game, body):
    event = require_event(game, number(body["m_event_id"]), {1})
    prepare_miner(game, event)
    amount = number(body["recovery_count"], 1, 100)
    counter, display = recovery_counter(game, event["id"], game.rules.get("event_miner_recovery_limit", 3))
    supply = event_supply(game, event["id"])
    if amount > display["amount"] or supply["amount"] + amount > supply["capacity"]:
        raise GameplayError("Mining recovery exceeds remaining purchases or supply capacity")
    consume(game, [{"content_type": 10, "content_id": 0, "amount": amount * game.rules.get("event_miner_recovery_cost", 50)}])
    supply["amount"] += amount
    counter["used"] += amount
    display["amount"] -= amount
    return {}


def skip(game, body):
    definition, event, row = quest(game, body["m_event_miner_quest_id"])
    count = number(body["skip_count"], 1, 100)
    if row["status"] != 99 or battle_state(game)["active"] is not None:
        raise GameplayError("Mining quest must be cleared and no battle may be active")
    supply = event_supply(game, event["id"])
    if supply["amount"] < count:
        raise GameplayError("Insufficient mining attempts")
    status = rows(game.state, "t_statuses")[0]
    before_exp, before_gold = status["character_exp"], status["gold"]
    drops, rewards = [], []
    for _ in range(count):
        start(game, {"m_event_miner_quest_id": definition["id"], "party_no": miner_data(game, event["id"])["last_party_no"]}, is_skip=True)
        session = battle_state(game)["active"]
        result = skipped_mining_result(session["stage_detail"], row["clear_rank"])
        response = complete(game, session, result)
        drops.extend(response["drops"])
        rewards.extend(response["clear_rewards"])
        battle_state(game)["active"] = None
    return {"grant_gold": status["gold"] - before_gold, "grant_character_exp": status["character_exp"] - before_exp,
            "before_character_exp": before_exp, "after_character_exp": status["character_exp"],
            "drops": merged_drops(drops), "first_rewards": [], "clear_rewards": merged_drops(rewards)}


MINER_ROUTES = {
    "api/event/miner/check-quest": route("POST", "m_event_id", check),
    "api/event/miner/start-quest": route("POST", "m_event_miner_quest_id party_no", start),
    "api/event/miner/clear-quest": route("POST", "stage_results", clear),
    "api/event/miner/close-quest": route("POST", "play_time", abandon),
    "api/event/miner/surrender-quest": route("POST", "play_time", abandon),
    "api/event/miner/recovery": route("POST", "m_event_id recovery_count", recovery),
    "api/event/miner/skip-quest": route("POST", "m_event_miner_quest_id skip_count", skip),
}
