import json
from datetime import timedelta
from pathlib import Path

import pytest

from dotabyss_ps.gameplay import EXTRA_ROUTES
from dotabyss_ps.gameplay_catalog import GameplayCatalog, available
from dotabyss_ps.gameplay_records import put
from dotabyss_ps.gameplay_statistics import record
from test_economy_features import give, update
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records, seed


def evaluator(context):
    from dotabyss_ps.gameplay_missions import Missions
    state, progress = map(json.loads, snapshot(context))
    return Missions(state, progress, GameplayCatalog(context.tables, context.schema), NOW, context.identity["user_id"])


def counter(context, situation, *, kind=1, target=0, sub=0):
    return evaluator(context).value({"situation_type": situation, "mission_type": kind, "target_id": target, "sub_target_id": sub})


def active_pass_id(context):
    catalog = GameplayCatalog(context.tables, context.schema)
    return next(r["id"] for r in catalog.rows("m_mission_passes") if available(r, NOW))


def test_registered_gameplay_methods_and_fields_match_native_contracts():
    document = json.loads((Path(__file__).parents[1] / "artifacts/ida/pc-1.15.0/api-contracts.json").read_text("utf-8"))
    native = {r["route"]: r for r in document["requests"] if r.get("verified") and "route" in r}
    for path, entry in EXTRA_ROUTES.items():
        assert path in native
        assert entry.method == native[path]["method"], path
        assert set(entry.fields) - set(entry.path_fields) == {f["name"] for f in native[path]["request_fields"]} - set(entry.path_fields), path


def test_counters_track_actions_and_not_replays_or_failed_charges(setup_gameplay):
    c = setup_gameplay
    give(c, [{"content_type": 170, "content_id": 1, "amount": 400}])
    headers = {**c.headers, "x-request-id": "counter-exchange"}
    body = {"m_exchange_lineup_id": 101, "exchange_count": 1}
    call(c, "api/exchange/execute", body, headers=headers)
    call(c, "api/exchange/execute", body, headers=headers)
    assert counter(c, 60000) == counter(c, 60000, kind=2) == 1
    before = snapshot(c)
    call(c, "api/exchange/execute", {**body, "exchange_count": 2}, expected=409)
    assert snapshot(c) == before
    seed(c, items={140001: 4})
    call(c, "api/character/gift/give", {"t_character_id": 1, "use_items": [{"m_item_id": 140001, "amount": 2}]})
    assert counter(c, 40400) == 2
    assert counter(c, 40103, target=1) >= 1


def test_equipment_history_survives_disassembly_and_period_rollover(setup_gameplay, monkeypatch):
    c = setup_gameplay
    give(c, [{"content_type": 70, "content_id": 11010110, "amount": 1}])
    identifier = records(c)["t_weapons"]["up"][0]["id"]
    call(c, "api/equipment/disassembly/execute", {"t_weapon_ids": [identifier], "t_armor_ids": [], "t_accessory_ids": []})
    assert counter(c, 80000) == counter(c, 80000, kind=2) == counter(c, 90000) == 1
    assert not records(c)["t_weapons"]["up"]
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=2))
    listing = call(c, "api/mission/list")
    assert next(r for r in listing["daily"] if r["m_mission_id"] == 2006)["value"] == 0
    assert next(r for r in listing["weekly"] if r["m_mission_id"] == 3015)["value"] == 0
    assert next(r for r in listing["achievement"] if r["m_mission_id"] == 101401)["value"] == 1


def test_gacha_and_tavern_produce_real_mission_progress(setup_gameplay):
    c = setup_gameplay
    call(c, "api/gacha/exec", {"m_gacha_id": 10101, "exec_count": 1, "use_gacha_ticket": 0})
    assert counter(c, 250000) == 10
    listing = call(c, "api/tavern")
    identifier = listing["tavern_daily_cards"][0]["id"]
    call(c, "api/tavern/set/characters", {"set_info": [{"m_tavern_work_id": 100, "m_character_id": 1200002}]})
    call(c, "api/tavern/skip/work", {"use_ticket": 0, "t_tavern_daily_card_id": identifier, "exec_count": 1})
    assert counter(c, 200000) == 1
    assert counter(c, 200001) > 0
    call(c, "api/mission/reward/receive", {"m_mission_ids": [2003]})


def test_plan_claim_advances_step_and_unlocks_research_without_double_reward(setup_gameplay):
    c = setup_gameplay
    listing = call(c, "api/plan-mission/list/1001", method="GET")
    assert [r["m_mission_id"] for r in listing["limited"]] == [200010010]
    before = snapshot(c)
    call(c, "api/plan-mission/reward/receive", {"m_plan_id": 1001, "m_mission_ids": [200010010]}, expected=409)
    assert snapshot(c) == before
    update(c, lambda state, progress: record(progress, NOW, 130001, 240))
    body = {"m_plan_id": 1001, "m_mission_ids": [200010010]}
    call(c, "api/plan-mission/reward/receive", body)
    before = snapshot(c)
    call(c, "api/plan-mission/reward/receive", body)
    assert snapshot(c) == before
    assert next(r["step"] for r in records(c)["t_plans"]["up"] if r["m_plan_id"] == 1001) == 1
    call(c, "api/research/upgrade", {"m_building_id": 100, "skill_type": 1000})
    assert counter(c, 180000, target=100, sub=1000) == 1
    assert counter(c, 230001, target=1001) == 1


def test_sync_levels_use_full_cost_schedule_and_grade_limits(setup_gameplay):
    c = setup_gameplay
    seed(c, gold=100000000, character_exp=100000000, items={180001: 100000, 51003: 1})
    assert records(c)["t_level_synchros"]["up"][0]["level"] == 30
    headers = {**c.headers, "x-request-id": "sync-up"}
    body = {"exec_count": 2}
    result = call(c, "api/level-synchro/level-up", body, headers=headers)
    assert call(c, "api/level-synchro/level-up", body, headers=headers) == result
    data = records(c)
    catalog = GameplayCatalog(c.tables, c.schema)
    gold = sum(r["amount"] for r in catalog.rows("m_level_synchro_level_up_costs") if r["level"] in (31, 32) and r["content_type"] == 50)
    assert data["t_statuses"]["up"][0]["gold"] == 100000000 - gold
    assert data["t_level_synchros"]["up"][0]["level"] == 32
    assert all(r["level"] == 1 for r in data["t_characters"]["up"])
    before = snapshot(c)
    call(c, "api/level-synchro/level-up", {"exec_count": 29}, expected=409)
    call(c, "api/level-synchro/upgrade", expected=409)
    assert snapshot(c) == before
    update(c, lambda state, progress: state["records"]["t_level_synchros"]["up"][0].update(level=60))
    call(c, "api/level-synchro/upgrade")
    assert records(c)["t_level_synchros"]["up"][0]["grade"] == 2


@pytest.mark.parametrize("points,expected_levels", [(0, 0), (99, 0), (100, 1), (350, 3), (5000, 50), (5300, 50)])
def test_pass_top_excludes_unreached_and_loop_levels(setup_gameplay, points, expected_levels):
    c = setup_gameplay
    pass_id = active_pass_id(c)
    body = {"m_mission_pass_id": pass_id}
    if points:
        give(c, [{"content_type": 181, "content_id": pass_id, "amount": points}])
    result = call(c, "api/mission-pass/top", body)
    assert result["t_mission_pass_level_rewards"] == [
        {"level": level, "is_normal_received": 0, "is_premium_received": 0}
        for level in range(1, expected_levels + 1)]
    assert result["t_mission_pass"]["loop_pass_point"] == max(0, points - 5000)
    assert json.loads(snapshot(c)[1])["features"]["mission_pass"]["data"][str(pass_id)]["rewards"] == {}


def test_pass_top_keeps_claims_when_admin_lowers_and_restores_points(setup_gameplay):
    from dotabyss_ps.admin_actions import apply_action
    from dotabyss_ps.gameplay import Gameplay

    c = setup_gameplay
    pass_id = active_pass_id(c)
    body = {"m_mission_pass_id": pass_id}
    catalog = GameplayCatalog(c.tables, c.schema)

    def edit(**values):
        update(c, lambda state, progress: apply_action(
            Gameplay(state, progress, catalog, c.identity["user_id"], NOW),
            "pass.set", {"master_id": pass_id, **values}))

    edit(points=300)
    call(c, "api/mission-pass/level/reward/receive", {**body, "levels": [
        {"level": 1, "receive_normal": 1, "receive_premium": 0}]})
    assert call(c, "api/mission-pass/top", body)["t_mission_pass_level_rewards"][0] == {
        "level": 1, "is_normal_received": 1, "is_premium_received": 0}
    edit(premium=True)
    selection = {**body, "levels": [
        {"level": level, "receive_normal": 1, "receive_premium": 1} for level in range(1, 4)]}
    call(c, "api/mission-pass/level/reward/receive", selection)
    claimed = call(c, "api/mission-pass/top", body)["t_mission_pass_level_rewards"]
    assert claimed == [{"level": level, "is_normal_received": 1, "is_premium_received": 1} for level in range(1, 4)]
    receipts = json.loads(snapshot(c)[1])["features"]["mission_pass"]["data"][str(pass_id)]["rewards"]
    for points, visible in ((100, claimed[:1]), (0, []), (300, claimed)):
        edit(points=points)
        assert call(c, "api/mission-pass/top", body)["t_mission_pass_level_rewards"] == visible
        assert json.loads(snapshot(c)[1])["features"]["mission_pass"]["data"][str(pass_id)]["rewards"] == receipts
    before = snapshot(c)
    call(c, "api/mission-pass/level/reward/receive", selection)
    assert snapshot(c) == before


@pytest.mark.parametrize("level_count", [1, 50])
def test_pass_bulk_accepts_native_split_tracks_and_is_idempotent(setup_gameplay, level_count):
    c = setup_gameplay
    pass_id = active_pass_id(c)
    body = {"m_mission_pass_id": pass_id}
    give(c, [{"content_type": 180, "content_id": pass_id, "amount": 1},
             {"content_type": 181, "content_id": pass_id, "amount": level_count * 100}])
    selection = {**body, "levels": [
        {"level": level, "receive_normal": normal, "receive_premium": premium}
        for level in range(1, level_count + 1) for normal, premium in ((1, 0), (0, 1))]}
    call(c, "api/mission-pass/level/reward/receive", selection)
    assert call(c, "api/mission-pass/top", body)["t_mission_pass_level_rewards"] == [
        {"level": level, "is_normal_received": 1, "is_premium_received": 1}
        for level in range(1, level_count + 1)]
    before = snapshot(c)
    call(c, "api/mission-pass/level/reward/receive", selection)
    assert snapshot(c) == before


@pytest.mark.parametrize("first,second", [((1, 0), (1, 0)), ((0, 1), (0, 1)), ((1, 1), (0, 1)), ((1, 0), (1, 1))])
def test_pass_bulk_rejects_overlapping_tracks_atomically(setup_gameplay, first, second):
    c = setup_gameplay
    pass_id = active_pass_id(c)
    give(c, [{"content_type": 180, "content_id": pass_id, "amount": 1},
             {"content_type": 181, "content_id": pass_id, "amount": 100}])
    before = snapshot(c)
    call(c, "api/mission-pass/level/reward/receive", {"m_mission_pass_id": pass_id, "levels": [
        {"level": 1, "receive_normal": normal, "receive_premium": premium}
        for normal, premium in (first, second)]}, expected=400)
    assert snapshot(c) == before


def test_pass_levels_premium_and_loop_rewards_are_persistent_and_atomic(setup_gameplay):
    c = setup_gameplay
    seed(c, free_virtual_money=1000)
    pass_id = active_pass_id(c)
    body = {"m_mission_pass_id": pass_id}
    call(c, "api/mission-pass/level/up", {**body, "level_up_count": 3})
    assert call(c, "api/mission-pass/top", body)["t_mission_pass"]["total_pass_point"] == 300
    selection = {**body, "levels": [{"level": 1, "receive_normal": 1, "receive_premium": 1}]}
    before = snapshot(c)
    call(c, "api/mission-pass/level/reward/receive", selection, expected=409)
    assert snapshot(c) == before
    give(c, [{"content_type": 180, "content_id": pass_id, "amount": 1}])
    call(c, "api/mission-pass/level/reward/receive", selection)
    before = snapshot(c)
    call(c, "api/mission-pass/level/reward/receive", selection)
    assert snapshot(c) == before
    give(c, [{"content_type": 181, "content_id": pass_id, "amount": 5000}])
    call(c, "api/mission-pass/loop-reward/receive", body)
    top = call(c, "api/mission-pass/top", body)
    assert top["t_mission_pass_loop_reward"]["received_count"] == 3
    assert top["t_mission_pass"]["loop_pass_point"] == 0
    before = snapshot(c)
    call(c, "api/mission-pass/loop-reward/receive", body)
    assert snapshot(c) == before


def test_pass_missions_reject_foreign_ids_and_grant_points(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    catalog = GameplayCatalog(c.tables, c.schema)
    pass_id = active_pass_id(c)
    mission = next(r for r in catalog.rows("m_missions") if r["m_mission_pass_id"] == pass_id and r["situation_type"] == 60000)
    update(c, lambda state, progress: record(progress, NOW, 60000, mission["require_value"]))
    before = snapshot(c)
    call(c, "api/mission-pass/mission/reward/receive", {"m_mission_pass_id": pass_id, "m_mission_ids": [2001]}, expected=400)
    assert snapshot(c) == before
    call(c, "api/mission-pass/mission/reward/receive", {"m_mission_pass_id": pass_id, "m_mission_ids": [mission["id"]]})
    assert call(c, "api/mission-pass/top", {"m_mission_pass_id": pass_id})["t_mission_pass"]["total_pass_point"] > 0


def test_special_completion_requires_every_claim_and_is_only_granted_once(setup_gameplay):
    c = setup_gameplay
    call(c, "api/special-mission/top", {"m_special_mission_id": 101})
    before = snapshot(c)
    call(c, "api/special-mission/complete-reward/receive", {"m_special_mission_id": 101}, expected=409)
    assert snapshot(c) == before
    catalog = GameplayCatalog(c.tables, c.schema)
    required = [r["m_mission_id"] for r in catalog.rows("m_special_mission_days") if r["m_special_mission_id"] == 101]
    update(c, lambda state, progress: progress["mission_claims"].update({f"once:{identifier}": [] for identifier in required}))
    call(c, "api/special-mission/complete-reward/receive", {"m_special_mission_id": 101})
    before = snapshot(c)
    call(c, "api/special-mission/complete-reward/receive", {"m_special_mission_id": 101})
    assert snapshot(c) == before
    assert records(c)["t_special_missions"]["up"][0]["completed_at"]
