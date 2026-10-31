import copy
import json

from dotabyss_ps.gameplay_mining_stage import skipped_mining_result
from dotabyss_ps.gameplay_records import put
from dotabyss_ps.gameplay_catalog import GameplayCatalog
from test_economy_features import update
from test_gameplay import call, gameplay_master, party_body, setup_gameplay, snapshot


def prepare(context, event_id, feature):
    call(context, "api/user")
    call(context, "api/party/exploration/edit", party_body())
    update(context, lambda state, _: put(state, "t_feature_locks", {"m_feature_lock_id": feature}, {"is_unlocked": True}))
    call(context, "api/event/join", {"m_event_id": event_id})


def test_miner_http_failure_route_and_invalid_drop_are_atomic(setup_gameplay):
    context = setup_gameplay
    prepare(context, 100001, 1001)
    started = call(context, "api/event/miner/start-quest", {"m_event_miner_quest_id": 100001101, "party_no": 1})
    stage = json.loads(started["stage_detail"])
    result = skipped_mining_result(stage, 1)
    assert any(row["is_target"] and row["sid"] not in result["destroyed_enemies"] for row in stage["enemies"])
    bad = copy.deepcopy(result)
    bad["drop_items"].append(999999999)
    before = snapshot(context)
    call(context, "api/event/miner/clear-quest", {"stage_results": bad}, expected=400)
    assert snapshot(context) == before
    won = call(context, "api/event/miner/clear-quest", {"stage_results": result})
    assert won["first_rewards"]
    before = snapshot(context)
    call(context, "api/event/miner/clear-quest", {"stage_results": result})
    assert snapshot(context) == before
    update(context, lambda state, _: state["records"]["t_statuses"]["up"][0].update(free_virtual_money=0))
    before = snapshot(context)
    call(context, "api/event/miner/recovery", {"m_event_id": 100001, "recovery_count": 1}, expected=409)
    assert snapshot(context) == before


def test_mutant_http_partial_progress_and_epoch_rejection(setup_gameplay):
    context = setup_gameplay
    prepare(context, 1050003, 1002)
    call(context, "api/event/disaster/start-quest", {"m_event_disaster_quest_id": 1101, "party_no": 1})
    progress = json.loads(snapshot(context)[1])
    session = progress["features"]["battle"]["data"]["active"]
    body = {"play_time": 1, "phase_no": 1, "remain_hp": session["context"]["remain_hp"] - 50,
            "boss_damage": 50, "battle_detail": []}
    bad = {**body, "boss_damage": 0}
    before = snapshot(context)
    call(context, "api/event/disaster/close-quest", bad, expected=400)
    assert snapshot(context) == before
    response = call(context, "api/event/disaster/close-quest", body)
    assert response["status"] == 0
    call(context, "api/event/disaster/start-quest", {"m_event_disaster_quest_id": 1101, "party_no": 1})
    update(context, lambda _, progress: progress["features"]["events"]["data"]["instances"]["1050003"].update(epoch=2))
    before = snapshot(context)
    call(context, "api/event/disaster/close-quest", body, expected=409)
    assert snapshot(context) == before


def test_mutant_defensive_fuel_is_atomic_and_not_charged_again_on_retry(setup_gameplay):
    context = setup_gameplay
    prepare(context, 1050003, 1002)
    catalog = GameplayCatalog(context.tables, context.schema)
    definition = next(row for row in catalog.rows("m_defensive_units") if row["type"] != 900 and row["require_cost"] > 0
                      and any(level["m_defensive_unit_id"] == row["id"] and level["level"] == 1 for level in catalog.rows("m_defensive_unit_levels")))
    def seed(state, _):
        owner = state["records"]["t_statuses"]["up"][0]["user_id"]
        unit = put(state, "t_defensive_units", {"m_defensive_unit_id": definition["id"]}, {"level": 1}, owner)
        party_id = next(row["id"] for row in state["records"]["t_exploration_parties"]["up"] if row["party_no"] == 1)
        party = put(state, "t_disaster_parties", {"disaster_type": 3, "m_disaster_quest_id": 1101, "m_disaster_boss_id": 0, "t_exploration_party_id": party_id}, {}, owner)
        put(state, "t_disaster_party_defensive_units", {"t_disaster_party_id": party["id"], "t_defensive_unit_id": unit["id"]}, {"placement_line": 0, "placement_index": 0}, owner)
        put(state, "t_items", {"m_item_id": 130001}, {"amount": 0})
    update(context, seed)
    request = {"m_event_disaster_quest_id": 1101, "party_no": 1}
    before = snapshot(context)
    call(context, "api/event/disaster/start-quest", request, expected=409)
    assert snapshot(context) == before
    update(context, lambda state, _: put(state, "t_items", {"m_item_id": 130001}, {"amount": definition["require_cost"] * 2}))
    first = call(context, "api/event/disaster/start-quest", request)
    after = snapshot(context)
    assert call(context, "api/event/disaster/start-quest", request)["stage_detail"] == first["stage_detail"]
    assert snapshot(context) == after
