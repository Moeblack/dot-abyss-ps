import copy
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog
from dotabyss_ps.gameplay_disaster import is_disaster_quest_cleared
from dotabyss_ps.gameplay_records import find, put, rows
from test_gameplay import NOW, call, gameplay_master, party_body, setup_gameplay, snapshot


def mutate(context, action):
    state, progress = map(json.loads, snapshot(context))
    action(state, progress)
    with context.store.connection() as db:
        db.execute("UPDATE game_states SET contents=? WHERE profile_id=?", (json.dumps(state), context.identity["user_id"]))
        db.execute("UPDATE gameplay_progress SET contents=? WHERE profile_id=?", (json.dumps(progress), context.identity["user_id"]))


def records(context):
    return json.loads(snapshot(context)[0])["records"]


def active(context):
    return json.loads(snapshot(context)[1])["features"]["battle"]["data"]["active"]


def victory(session):
    stage = session["stage_detail"]
    return {"stage_results": {"play_time": 1,
        "destroyed_enemies": [row["sid"] for row in stage["enemies"]] + [stage["boss_enemy"]["sid"]],
        "drop_items": [row["sid"] for row in stage["drops"]], "mana_gem_count": 0, "top_fc_damage": 0}}


@pytest.fixture
def disaster(setup_gameplay):
    context = setup_gameplay
    call(context, "api/user")
    mutate(context, lambda state, _: put(state, "t_feature_locks", {"m_feature_lock_id": 3}, {"is_unlocked": True}))
    call(context, "api/party/exploration/edit", party_body())
    return context


def test_refresh_populates_three_normal_slots_and_preserves_initial_facilities(disaster):
    result = call(disaster, "api/disaster/refresh")
    assert {row["slot"] for row in result["appears"]} == {1, 2, 3}
    data = records(disaster)
    assert {row["slot"] for row in data["t_disaster_slots"]["up"]} == {0, 1, 2, 3}
    assert next(row for row in data["t_disaster_slots"]["up"] if row["slot"] == 0)["m_disaster_quest_id"] == 0
    assert {row["m_defensive_unit_id"] for row in data["t_defensive_units"]["up"]} >= {1002, 2001, 3001, 3002, 4001, 5001, 6001, 9000}
    before = snapshot(disaster)
    assert call(disaster, "api/disaster/refresh")["appears"] == []
    assert snapshot(disaster) == before


def test_disaster_routes_require_unlocked_feature(setup_gameplay):
    call(setup_gameplay, "api/user")
    before = snapshot(setup_gameplay)
    call(setup_gameplay, "api/disaster/refresh", expected=409)
    assert snapshot(setup_gameplay) == before


def test_start_restart_clear_isolated_session_and_daily_skip(disaster):
    call(disaster, "api/disaster/refresh")
    started = call(disaster, "api/disaster/start-quest", {"slot": 1, "party_no": 1})
    assert (started["quest_type"], started["stage_type"], started["status"]) == (4, 2, 1)
    session = active(disaster)
    assert started["quest_id"] == session["quest_id"]
    with TestClient(create_app(disaster.settings)) as client:
        status = client.get("/api/battle-session/status", headers=disaster.headers).json()["contents"]
    assert status["stage_detail"] == started["stage_detail"]
    result = call(disaster, "api/disaster/clear-quest", victory(session))
    assert result["clear_rewards"][0]["content_type"] == 52
    stable = snapshot(disaster)
    assert call(disaster, "api/disaster/clear-quest", victory(session))["clear_rewards"] == result["clear_rewards"]
    assert snapshot(disaster) == stable
    status = records(disaster)["t_disaster_statuses"]["up"][0]
    assert status["disaster_point"] == 15
    assert status["skippable_disaster_level"] == 1
    skipped = call(disaster, "api/disaster/skip-quest", {"slot": 2}, headers={**disaster.headers, "x-request-id": "disaster-skip"})
    assert skipped["clear_rewards"]
    assert records(disaster)["t_disaster_slots"]["up"][0]["m_disaster_quest_id"]
    stable = snapshot(disaster)
    call(disaster, "api/disaster/skip-quest", {"slot": 2}, headers={**disaster.headers, "x-request-id": "disaster-skip"})
    assert snapshot(disaster) == stable


def test_normal_progress_unlocks_special_boss_and_advances_level(disaster):
    call(disaster, "api/disaster/refresh")
    for slot in (1, 2, 0):
        call(disaster, "api/disaster/start-quest", {"slot": slot, "party_no": 1})
        call(disaster, "api/disaster/clear-quest", victory(active(disaster)))
    status = records(disaster)["t_disaster_statuses"]["up"][0]
    assert (status["disaster_level"], status["disaster_level_record_high"], status["disaster_point"]) == (2, 2, 0)
    progress = json.loads(snapshot(disaster)[1])
    assert progress["features"]["statistics"]["data"]["totals"]["160100:0:0"] == 3
    assert progress["features"]["statistics"]["data"]["totals"]["160000:0:0"] == 2


def test_invalid_result_and_other_mode_close_roll_back(disaster):
    call(disaster, "api/disaster/refresh")
    call(disaster, "api/disaster/start-quest", {"slot": 1, "party_no": 1})
    body = victory(active(disaster))
    body["stage_results"]["destroyed_enemies"].append(9223372036854770000)
    before = snapshot(disaster)
    call(disaster, "api/disaster/clear-quest", body, expected=400)
    call(disaster, "api/main-story/close-battle", {"play_time": 1}, expected=409)
    assert snapshot(disaster) == before


def test_surrender_keeps_slot_and_rejects_old_battle_ids(disaster):
    call(disaster, "api/disaster/refresh")
    call(disaster, "api/disaster/start-quest", {"slot": 1, "party_no": 1})
    old = active(disaster)
    call(disaster, "api/disaster/surrender-quest", {"play_time": 1})
    call(disaster, "api/disaster/start-quest", {"slot": 1, "party_no": 1})
    assert active(disaster)["id"] != old["id"]
    before = snapshot(disaster)
    call(disaster, "api/disaster/clear-quest", victory(old), expected=400)
    assert snapshot(disaster) == before


def test_concurrent_clear_does_not_duplicate_rewards_or_progress(disaster):
    call(disaster, "api/disaster/refresh")
    call(disaster, "api/disaster/start-quest", {"slot": 1, "party_no": 1})
    body = victory(active(disaster))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call(disaster, "api/disaster/clear-quest", body), range(2)))
    assert results[0]["clear_rewards"] == results[1]["clear_rewards"]
    assert records(disaster)["t_disaster_statuses"]["up"][0]["disaster_point"] == 15


def test_free_summons_cap_and_ticket_consumption(disaster):
    call(disaster, "api/disaster/refresh")
    body = {"m_disaster_boss_id": 2027101, "use_items": []}
    for _ in range(5):
        assert call(disaster, "api/disaster/summon", body)["appears"][0]["m_disaster_boss_id"] == 2027101
    before = snapshot(disaster)
    call(disaster, "api/disaster/summon", body, expected=409)
    assert snapshot(disaster) == before
    mutate(disaster, lambda state, _: put(state, "t_items", {"m_item_id": 120001}, {"amount": 1}))
    body["use_items"] = [{"m_item_id": 120001, "amount": 1}]
    call(disaster, "api/disaster/summon", body)
    assert next(row for row in records(disaster)["t_items"]["up"] if row["m_item_id"] == 120001)["amount"] == 0


def test_new_server_day_refreshes_slots_and_invalidates_old_skip(disaster):
    call(disaster, "api/disaster/refresh")
    call(disaster, "api/disaster/start-quest", {"slot": 1, "party_no": 1})
    call(disaster, "api/disaster/clear-quest", victory(active(disaster)))
    state, progress = map(json.loads, snapshot(disaster))
    game = Gameplay(state, progress, GameplayCatalog(disaster.tables, disaster.schema), disaster.identity["user_id"], NOW + timedelta(days=1))
    assert len(game.execute("api/disaster/refresh", "POST", {})["appears"]) == 3
    assert rows(state, "t_disaster_statuses")[0]["skippable_disaster_level"] == 0
    from dotabyss_ps.gameplay_catalog import GameplayError
    with pytest.raises(GameplayError):
        game.execute("api/disaster/skip-quest", "POST", {"slot": 1})


def test_cross_day_active_battle_remains_settleable(disaster):
    call(disaster, "api/disaster/refresh")
    call(disaster, "api/disaster/start-quest", {"slot": 1, "party_no": 1})
    session = active(disaster)
    state, progress = map(json.loads, snapshot(disaster))
    game = Gameplay(state, progress, GameplayCatalog(disaster.tables, disaster.schema), disaster.identity["user_id"], NOW + timedelta(days=1))
    assert game.execute("api/disaster/refresh", "POST", {})["appears"] == []
    game.execute("api/disaster/clear-quest", "POST", victory(session))
    assert rows(state, "t_disaster_statuses")[0]["skippable_disaster_level"] == 1
    assert is_disaster_quest_cleared(game, session["quest_id"])


def test_patrol_cost_and_level_limits(disaster):
    call(disaster, "api/disaster/refresh")
    def prepare(state, progress):
        rows(state, "t_disaster_statuses")[0].update(disaster_level=2, disaster_level_record_high=2, disaster_point=15)
        rows(state, "t_statuses")[0]["gold"] = 20000
    mutate(disaster, prepare)
    before_gold = records(disaster)["t_statuses"]["up"][0]["gold"]
    result = call(disaster, "api/disaster/patrol", {"disaster_level": 0})
    assert (result["before_disaster_level"], result["after_disaster_level"]) == (2, 1)
    assert records(disaster)["t_statuses"]["up"][0]["gold"] == before_gold - 10000
    before = snapshot(disaster)
    call(disaster, "api/disaster/patrol", {"disaster_level": 36}, expected=409)
    assert snapshot(disaster) == before


def test_formation_owned_units_and_start_fuel_are_transactional(disaster):
    call(disaster, "api/disaster/refresh")
    quest_id = next(row for row in records(disaster)["t_disaster_slots"]["up"] if row["slot"] == 1)["m_disaster_quest_id"]
    def add(state, progress):
        put(state, "t_defensive_units", {"m_defensive_unit_id": 4002}, {"level": 1}, disaster.identity["user_id"])
        put(state, "t_items", {"m_item_id": 130001}, {"amount": 5})
    mutate(disaster, add)
    unit = next(row for row in records(disaster)["t_defensive_units"]["up"] if row["m_defensive_unit_id"] == 4002)
    party = records(disaster)["t_exploration_parties"]["up"][0]
    body = {"disaster_type": 2, "m_disaster_quest_id": quest_id, "t_exploration_party_id": party["id"],
            "t_disaster_party_defensive_units": [{"placement_line": 1, "placement_index": 1, "t_defensive_unit_id": unit["id"]}]}
    call(disaster, "api/party/disaster/edit/all", body)
    before = snapshot(disaster)
    call(disaster, "api/party/disaster/edit/all", body)
    assert snapshot(disaster) == before
    started = call(disaster, "api/disaster/start-quest", {"slot": 1, "party_no": 1})
    assert json.loads(started["stage_detail"])["defensive_units"][0]["m_defensive_unit_id"] == 4002
    assert next(row for row in records(disaster)["t_items"]["up"] if row["m_item_id"] == 130001)["amount"] == 0
    before = snapshot(disaster)
    call(disaster, "api/disaster/start-quest", {"slot": 1, "party_no": 1})
    assert snapshot(disaster) == before


@pytest.mark.parametrize("damage,status", [
    (lambda body: body["t_disaster_party_defensive_units"][0].update(t_defensive_unit_id=99999), 409),
    (lambda body: body["t_disaster_party_defensive_units"][0].update(placement_line=999), 400),
    (lambda body: body["t_disaster_party_defensive_units"].append(dict(body["t_disaster_party_defensive_units"][0])), 400),
    (lambda body: body.update(t_exploration_party_id=99999), 409),
])
def test_invalid_defensive_formation_preserves_existing_state(disaster, damage, status):
    call(disaster, "api/disaster/refresh")
    data = records(disaster)
    unit = next(row for row in data["t_defensive_units"]["up"] if row["m_defensive_unit_id"] == 4001)
    body = {"disaster_type": 2, "m_disaster_quest_id": data["t_disaster_slots"]["up"][1]["m_disaster_quest_id"],
            "t_exploration_party_id": data["t_exploration_parties"]["up"][0]["id"],
            "t_disaster_party_defensive_units": [{"placement_line": 1, "placement_index": 1, "t_defensive_unit_id": unit["id"]}]}
    call(disaster, "api/party/disaster/edit/all", body)
    before = snapshot(disaster)
    damage(body)
    call(disaster, "api/party/disaster/edit/all", body, expected=status)
    assert snapshot(disaster) == before


def test_insufficient_defensive_fuel_rolls_back_generated_session(disaster):
    call(disaster, "api/disaster/refresh")
    mutate(disaster, lambda state, _: put(state, "t_defensive_units", {"m_defensive_unit_id": 4002}, {"level": 1}, disaster.identity["user_id"]))
    data = records(disaster)
    unit = next(row for row in data["t_defensive_units"]["up"] if row["m_defensive_unit_id"] == 4002)
    body = {"disaster_type": 2, "m_disaster_quest_id": data["t_disaster_slots"]["up"][1]["m_disaster_quest_id"],
            "t_exploration_party_id": data["t_exploration_parties"]["up"][0]["id"],
            "t_disaster_party_defensive_units": [{"placement_line": 1, "placement_index": 1, "t_defensive_unit_id": unit["id"]}]}
    call(disaster, "api/party/disaster/edit/all", body)
    before = snapshot(disaster)
    call(disaster, "api/disaster/start-quest", {"slot": 1, "party_no": 1}, expected=409)
    assert snapshot(disaster) == before


def test_all_disaster_quest_definitions_build_valid_results(gameplay_master):
    from dotabyss_ps.gameplay_battle_rewards import attach_drops, validate_stage_results
    from dotabyss_ps.gameplay_battle_stages import build_stage
    from dotabyss_ps.gameplay_disaster_party import quest_boss
    from dotabyss_ps.newgame import build_initial_state

    schema, tables = gameplay_master
    catalog = GameplayCatalog(tables, schema)
    state = build_initial_state(tables, schema, 7, "Disaster master test", "2026-10-31 10:00:00")
    game = Gameplay(state, {}, catalog, 7, NOW)
    game.prepare()
    definitions = catalog.rows("m_disaster_quests")
    assert len(definitions) == 269
    for index, definition in enumerate(definitions, 1):
        stage = build_stage(game, definition["stage_type"], definition["stage_id"], index,
                            kind="disaster", quest_id=definition["id"], defensive_type=2,
                            defensive_quest_id=definition["id"], defensive_boss_id=quest_boss(game, definition["id"]))
        attach_drops(game, definition["id"], stage, index, kind="disaster")
        result = victory({"stage_detail": stage})["stage_results"]
        validate_stage_results(stage, result, 2, time_limit=definition["time_limit"])
