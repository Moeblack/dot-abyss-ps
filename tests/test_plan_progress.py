import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import msgpack
import pytest

from dotabyss_ps.gameplay_catalog import GameplayCatalog
from dotabyss_ps.gameplay_statistics import record
from dotabyss_ps.masterdata import MasterCatalog
from test_economy_features import give, update
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records
from test_progression_features import counter


FIRST_MISSION = 200010010
SECOND_MISSION = 200010011


def plan(context, identifier=1001):
    return next(row for row in records(context)["t_plans"]["up"] if row["m_plan_id"] == identifier)


def mission_list(context, identifier=1001):
    return call(context, f"api/plan-mission/list/{identifier}", method="GET")["limited"]


def receive(context, missions, **kwargs):
    return call(context, "api/plan-mission/reward/receive", {"m_plan_id": 1001, "m_mission_ids": missions}, **kwargs)


def stamina_spent(context, amount):
    update(context, lambda state, progress: record(progress, NOW, 130001, amount))


def reward_amount(context):
    return sum(row["amount"] for row in records(context)["t_items"]["up"] if row["m_item_id"] == 51001)


@pytest.fixture
def two_stage_plan(setup_gameplay):
    context = setup_gameplay
    tables = copy.deepcopy(context.tables)

    def append_copy(table, source_id, **values):
        columns = [column["name"] for column in context.schema["tables"][table]]
        rows = [dict(zip(columns, row)) for row in tables[table]]
        assert all(row["id"] != values["id"] for row in rows)
        source = next(row for row in rows if row["id"] == source_id)
        source.update(values)
        tables[table].append([source[column] for column in columns])

    append_copy("m_plan_steps", 10010, id=10011, step=2, m_mission_id=SECOND_MISSION)
    append_copy("m_missions", FIRST_MISSION, id=SECOND_MISSION, require_value=480, require_mission_id=0)
    payload = context.settings.data_dir / "two-stage-master.msgpack"
    payload.write_bytes(msgpack.packb(tables))
    master = MasterCatalog(context.settings.data_dir, context.store)
    context.record = master.stage(payload, context.settings.new_game_schema, "Two-stage plan contract fixture")
    master.activate(context.record["revision"])
    context.tables = tables
    return context


def test_initial_plan_progress_matches_official_user_capture(setup_gameplay):
    context = setup_gameplay
    capture = Path(__file__).parents[1] / "captures/20261031-025842598-a3a7a9e5c32249308ff906a6c0089b4f/records/00000002.json"
    document = json.loads(capture.read_text(encoding="utf-8-sig"))
    official = document["response"]["contents"]["records"]["t_plans"]["up"]
    expected = {row["m_plan_id"]: row["step"] for row in official}
    assert expected == {1001: 0, 5001: 0}
    actual = {row["m_plan_id"]: row["step"] for row in records(context)["t_plans"]["up"]}
    assert actual == expected
    assert counter(context, 230001, target=1001) == 0
    assert counter(context, 230001, target=5001) == 0
    assert {row["m_mission_id"] for row in mission_list(context, 0)} == {FIRST_MISSION, 200050010}


def test_plan_unlock_reward_starts_uncompleted_and_does_not_reset_existing_progress(setup_gameplay):
    context = setup_gameplay
    owned = {row["m_plan_id"] for row in records(context)["t_plans"]["up"]}
    catalog = GameplayCatalog(context.tables, context.schema)
    identifier = next(row["m_plan_id"] for row in catalog.rows("m_plan_steps") if row["m_plan_id"] not in owned)
    give(context, [{"content_type": 131, "content_id": identifier, "amount": 1}])
    assert plan(context, identifier)["step"] == 0
    before = snapshot(context)
    give(context, [{"content_type": 131, "content_id": identifier, "amount": 1}])
    assert snapshot(context) == before
    stamina_spent(context, 240)
    receive(context, [FIRST_MISSION])
    before = snapshot(context)
    give(context, [{"content_type": 131, "content_id": 1001, "amount": 1}])
    assert snapshot(context) == before
    assert plan(context)["step"] == 1


def test_two_stage_progress_exposes_next_mission_and_keeps_completed_history(two_stage_plan):
    context = two_stage_plan
    assert plan(context)["step"] == 0
    assert [row["m_mission_id"] for row in mission_list(context)] == [FIRST_MISSION]
    stamina_spent(context, 240)
    first = receive(context, [FIRST_MISSION])
    assert first["records"]["t_plans"]["up"][0]["step"] == 1
    assert plan(context)["step"] < 2
    assert counter(context, 230001, target=1001) == 1
    missions = mission_list(context)
    assert [(row["m_mission_id"], row["is_received"]) for row in missions] == [(FIRST_MISSION, 1), (SECOND_MISSION, 0)]
    assert {FIRST_MISSION, SECOND_MISSION} <= {row["m_mission_id"] for row in mission_list(context, 0)}
    stamina_spent(context, 240)
    second = receive(context, [SECOND_MISSION])
    assert second["records"]["t_plans"]["up"][0]["step"] == 2
    assert plan(context)["step"] == 2
    assert counter(context, 230001, target=1001) == 2
    assert reward_amount(context) == 2
    assert [(row["m_mission_id"], row["is_received"]) for row in mission_list(context)] == [(FIRST_MISSION, 1), (SECOND_MISSION, 1)]


def test_later_stage_cannot_be_claimed_first_even_when_its_condition_is_met(two_stage_plan):
    context = two_stage_plan
    stamina_spent(context, 480)
    before = snapshot(context)
    receive(context, [SECOND_MISSION], expected=409)
    assert snapshot(context) == before
    assert plan(context)["step"] == 0
    assert reward_amount(context) == 0


def test_failed_batch_rolls_back_earlier_claim_and_successful_batch_can_replay(two_stage_plan):
    context = two_stage_plan
    stamina_spent(context, 240)
    headers = {**context.headers, "x-request-id": "two-stage-batch"}
    before = snapshot(context)
    receive(context, [SECOND_MISSION, FIRST_MISSION], headers=headers, expected=409)
    assert snapshot(context) == before
    stamina_spent(context, 240)
    result = receive(context, [SECOND_MISSION, FIRST_MISSION], headers=headers)
    assert result["records"]["t_plans"]["up"][0]["step"] == 2
    assert reward_amount(context) == 2
    before = snapshot(context)
    assert receive(context, [SECOND_MISSION, FIRST_MISSION], headers=headers) == result
    assert snapshot(context) == before
    receive(context, [FIRST_MISSION, SECOND_MISSION])
    assert snapshot(context) == before


@pytest.mark.parametrize("request_ids", [("same-claim", "same-claim"), ("claim-a", "claim-b")])
def test_concurrent_plan_claims_grant_one_reward(setup_gameplay, request_ids):
    context = setup_gameplay
    stamina_spent(context, 240)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda request_id: receive(context, [FIRST_MISSION], headers={**context.headers, "x-request-id": request_id}), request_ids))
    assert plan(context)["step"] == 1
    assert reward_amount(context) == 1
    assert counter(context, 230001, target=1001) == 1
    if request_ids[0] == request_ids[1]:
        assert results[0] == results[1]
    before = snapshot(context)
    receive(context, [FIRST_MISSION])
    assert snapshot(context) == before


def test_research_plan_requirement_needs_claim_before_upgrade(setup_gameplay):
    context = setup_gameplay
    stamina_spent(context, 240)
    listing = call(context, "api/research")["building_skills"]
    requirement = next(row for row in listing if row["m_building_skill_level_id"] == 100000)
    assert requirement["is_upgradable"] == 0
    before = snapshot(context)
    call(context, "api/research/upgrade", {"m_building_id": 100, "skill_type": 1000}, expected=409)
    assert snapshot(context) == before
    receive(context, [FIRST_MISSION])
    listing = call(context, "api/research")["building_skills"]
    requirement = next(row for row in listing if row["m_building_skill_level_id"] == 100000)
    assert requirement["is_upgradable"] == 1
    call(context, "api/research/upgrade", {"m_building_id": 100, "skill_type": 1000})
    assert next(row for row in records(context)["t_building_skills"]["up"] if row["skill_type"] == 1000)["skill_level"] == 1
