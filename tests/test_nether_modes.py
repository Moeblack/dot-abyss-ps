import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from dotabyss_ps.gameplay_catalog import GameplayCatalog
from dotabyss_ps.gameplay_records import put
from test_economy_features import update as mutate
from test_gameplay import call, gameplay_master, party_body, setup_gameplay, snapshot
from test_main_story_battles import active_session


def state(context):
    return json.loads(snapshot(context)[0])


def progress(context):
    return json.loads(snapshot(context)[1])


def nether(context):
    return state(context)["records"]["t_nethers"]["up"][0]


def start(context):
    call(context, "api/user")
    call(context, "api/party/exploration/edit", party_body())
    return call(context, "api/nether/start", {"m_nether_id": 1, "m_nether_map_id": 1, "party_no": 1,
                                              "use_ticket": 0, "start_floor_level": 1})


def coordinate(floor):
    return {key: floor[key] for key in ("m_nether_id", "m_nether_map_id", "floor_level", "floor_index")}


def choose(context, kind, level=1):
    catalog = GameplayCatalog(context.tables, context.schema)
    data = progress(context)["features"]["nether"]["data"]["run"]
    candidates = [row for row in data["floors"] if row["floor_level"] == level and catalog.get("m_nether_map_floors", row["m_nether_map_floor_id"])["type"] == kind]
    if candidates:
        return candidates[0]
    template = next(row for row in catalog.rows("m_nether_map_floors") if row["type"] == kind and row["min_order"] <= level <= row["max_order"])
    table = "m_nether_floor_battles" if kind in (1, 2, 3) else "m_nether_floor_shops" if kind == 6 else "m_nether_floor_events"
    content = next(row for row in catalog.rows(table) if row["m_nether_map_floor_id"] == template["id"])
    selected = next(row for row in data["floors"] if row["floor_level"] == level)
    selected.update(m_nether_map_floor_id=template["id"], extend_id=content["id"])
    mutate(context, lambda _, p: p["features"]["nether"]["data"]["run"].update(floors=data["floors"]))
    return selected


def victory(context, floor, ratio=900):
    session = active_session(context)
    stage = session["stage_detail"]
    return {**coordinate(floor), "character_hps": [{"m_character_id": row["m_character_id"], "current_hp_ratio": ratio}
                                                  for row in session["party_detail"]["characters"]],
            "stage_results": {"play_time": 1, "destroyed_enemies": [row["sid"] for row in stage["enemies"]],
                              "drop_items": [row["sid"] for row in stage["drops"]], "mana_gem_count": 0,
                              "top_fc_damage": 100, "total_damage": 1000,
                              "passed_floor_parts": [row["sid"] for row in stage["floor_parts"]], "clear_rank": 0}}


def clear_codes(context):
    while nether(context)["m_nether_code_ids"]:
        identifier = nether(context)["m_nether_code_ids"][0]
        owned = [row for row in state(context)["records"]["t_nether_codes"]["up"] if row["amount"]]
        current = next((row for row in owned if row["m_nether_code_id"] == identifier), None)
        remove = owned[0]["m_nether_code_id"] if len(owned) >= 8 and current is None else 0
        call(context, "api/nether/fix-code", {"m_nether_id": 1, "m_nether_map_id": 1,
             "m_nether_code_id": identifier, "remove_m_nether_code_id": remove})


def test_nether_listing_start_frozen_party_battle_restore_and_return(setup_gameplay):
    context = setup_gameplay
    listing = call(context, "api/nether")
    assert listing["party_detail"] is None
    initial = start(context)
    assert len({row["floor_level"] for row in initial["t_nether_map_floors"]}) == 150
    floor = choose(context, 1)
    call(context, "api/nether/update", coordinate(floor))
    started = call(context, "api/nether/start-battle", coordinate(floor))
    assert started["quest_type"] == 110
    party = json.loads(started["party_detail"])
    assert len(party["characters"]) == 3
    assert party["characters"][0]["hp"] > 0
    stable = snapshot(context)
    assert call(context, "api/nether/start-battle", coordinate(floor))["stage_detail"] == started["stage_detail"]
    assert snapshot(context) == stable
    with TestClient(create_app(context.settings)) as restarted:
        response = restarted.get("/api/battle-session/status", headers=context.headers).json()["contents"]
    assert response["stage_detail"] == started["stage_detail"] and response["party_detail"] == started["party_detail"]
    body = victory(context, floor)
    won = call(context, "api/nether/clear-battle", body)
    assert won["t_nether_map_floor"] is None
    assert all(row["current_hp_ratio"] == 900 for row in won["t_nether_characters"])
    stable = snapshot(context)
    assert call(context, "api/nether/clear-battle", body)["drops"] == won["drops"]
    assert snapshot(context) == stable
    clear_codes(context)
    response = call(context, "api/nether/result", {"m_nether_id": 1, "m_nether_map_id": 1, "use_item_flag": 0})
    assert response["result_score"] == 100
    stable = snapshot(context)
    call(context, "api/nether/result", {"m_nether_id": 1, "m_nether_map_id": 1, "use_item_flag": 0})
    assert snapshot(context) == stable


@pytest.mark.parametrize("damage", [lambda body: body["character_hps"].pop(),
                                    lambda body: body["character_hps"][0].update(current_hp_ratio=1001),
                                    lambda body: body["stage_results"].update(drop_items=[999999999])])
def test_nether_invalid_battle_result_is_atomic(setup_gameplay, damage):
    context = setup_gameplay
    start(context)
    floor = choose(context, 1)
    call(context, "api/nether/update", coordinate(floor))
    call(context, "api/nether/start-battle", coordinate(floor))
    body = victory(context, floor)
    damage(body)
    before = snapshot(context)
    call(context, "api/nether/clear-battle", body, expected=400)
    assert snapshot(context) == before


def test_nether_suspend_retries_same_floor_without_charging_entry(setup_gameplay):
    context = setup_gameplay
    start(context)
    floor = choose(context, 1)
    call(context, "api/nether/update", coordinate(floor))
    first = call(context, "api/nether/start-battle", coordinate(floor))
    call(context, "api/battle-session/suspend", {"is_force": 1})
    assert nether(context)["status"] == 2
    second = call(context, "api/nether/start-battle", coordinate(floor))
    assert first["stage_detail"] != second["stage_detail"]
    assert progress(context)["features"]["nether"]["data"]["free_entries"] == {"2026-10-31": 1}


def test_nether_shop_purchases_are_bound_to_floor_and_stock(setup_gameplay):
    context = setup_gameplay
    start(context)
    catalog = GameplayCatalog(context.tables, context.schema)
    level = min(row["min_order"] for row in catalog.rows("m_nether_map_floors") if row["type"] == 6)
    mutate(context, lambda s, _: s["records"]["t_nethers"]["up"][0].update(floor_level=level - 1, m_nether_map_floor_id=0))
    floor = choose(context, 6, level)
    call(context, "api/nether/update", coordinate(floor))
    item = next(row for row in catalog.rows("m_nether_floor_shop_contents") if row["m_nether_floor_shop_id"] == floor["extend_id"])
    mutate(context, lambda s, _: s["records"]["t_nethers"]["up"][0].update(nether_gold=100000))
    body = {"shop_item_amount": [{"m_nether_floor_shop_content_id": item["id"], "amount": 1}]}
    response = call(context, "api/nether/update-shop", body)
    assert response["shop_histories"] == body["shop_item_amount"]
    stable = snapshot(context)
    call(context, "api/nether/update-shop", body, expected=409)
    assert snapshot(context) == stable


def test_nether_sphere_upgrade_costs_and_stale_level_rollback(setup_gameplay):
    context = setup_gameplay
    call(context, "api/nether")
    catalog = GameplayCatalog(context.tables, context.schema)
    sphere = next(row for row in catalog.rows("m_nether_spheres") if row["type"] == 1 and row["level"] == 1)
    costs = [row for row in catalog.rows("m_nether_sphere_costs") if row["m_nether_sphere_id"] == sphere["id"]]
    def seed(s, p):
        for key in p["features"]["nether"]["data"]["points"]:
            p["features"]["nether"]["data"]["points"][key] = 10000
        for cost in costs:
            if cost["content_type"] == 30:
                put(s, "t_items", {"m_item_id": cost["content_id"]}, {"amount": 10000})
    mutate(context, seed)
    before = call(context, "api/nether")["t_nether_point"]["max_nether_code"]
    body = {"m_nether_id": 1, "m_nether_map_id": 1, "m_nether_sphere_board_id": sphere["m_nether_sphere_board_id"],
            "m_nether_sphere_id": sphere["id"], "current_level": 0}
    response = call(context, "api/nether/upgrade-sphere", body)
    assert response["t_nether_sphere_level"]["level"] == 1
    assert response["t_nether_point"]["max_nether_code"] > before
    stable = snapshot(context)
    call(context, "api/nether/upgrade-sphere", body, expected=409)
    assert snapshot(context) == stable


@pytest.mark.parametrize("rare", [0, 1])
def test_nether_skip_ticket_consumption_and_code_draws(setup_gameplay, rare):
    context = setup_gameplay
    start(context)
    item_id = 200008 if rare else 200007
    mutate(context, lambda s, _: put(s, "t_items", {"m_item_id": item_id}, {"amount": 1}))
    body = {"m_nether_id": 1, "m_nether_map_id": 1, "start_floor_level": 1, "is_rare": rare, "party_no": 1, "lock_items": []}
    headers = {**context.headers, "x-request-id": "nether-skip"}
    response = call(context, "api/nether/skip", body, headers=headers)
    assert nether(context)["floor_level"] == 10
    assert bool(response["drops"]) == bool(rare)
    assert progress(context)["features"]["nether"]["data"]["run"]["code_draws"] == (6 if rare else 3)
    stable = snapshot(context)
    call(context, "api/nether/skip", body, headers=headers)
    assert snapshot(context) == stable
    clear_codes(context)
    assert not nether(context)["m_nether_code_ids"]


def test_nether_loop_stamina_mission_uses_actual_stamina_and_receipts(setup_gameplay):
    context = setup_gameplay
    call(context, "api/nether")
    mutate(context, lambda _, p: p["features"]["statistics"]["data"]["totals"].update({"130001:0:0": 1200}))
    body = {"m_mission_ids": [93000001]}
    headers = {**context.headers, "x-request-id": "nether-ticket-reward"}
    call(context, "api/nether-mission/reward/receive", body, headers=headers)
    stable = snapshot(context)
    call(context, "api/nether-mission/reward/receive", body, headers=headers)
    assert snapshot(context) == stable
    call(context, "api/nether-mission/reward/receive", body)
    assert progress(context)["features"]["nether"]["data"]["ticket_claims"]["93000001"] == 2
    stable = snapshot(context)
    call(context, "api/nether-mission/reward/receive", body, expected=409)
    assert snapshot(context) == stable


def test_nether_event_resolution_is_once(setup_gameplay):
    context = setup_gameplay
    start(context)
    floor = choose(context, 4)
    call(context, "api/nether/update", coordinate(floor))
    mutate(context, lambda s, p: (s["records"]["t_nethers"]["up"][0].update(nether_gold=100000, treasure_key=100),
           p["features"]["nether"]["data"]["run"].update(hp={k: 700 for k in p["features"]["nether"]["data"]["run"]["hp"]})))
    body = {**coordinate(floor), "select_number": 1, "m_nether_code_id": 0}
    response = call(context, "api/nether/update-event", body)
    assert response["t_nether_map_floor"] is None
    stable = snapshot(context)
    call(context, "api/nether/update-event", body)
    assert snapshot(context) == stable
