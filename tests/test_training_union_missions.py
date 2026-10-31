import json
from datetime import timedelta

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog
from dotabyss_ps.gameplay_missions import Missions
from dotabyss_ps.gameplay_shop import recommendation_eligible
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_training_encounter_modes import edit, prepare, victory


def evaluator(context, now=NOW):
    saved, progress = map(json.loads, snapshot(context))
    catalog = GameplayCatalog(context.tables, context.schema)
    return Missions(saved, progress, catalog, now, context.identity["user_id"])


def value(context, situation, target=0, *, kind=1, now=NOW):
    return evaluator(context, now).value({"situation_type": situation, "target_id": target,
                                         "sub_target_id": 0, "mission_type": kind})


def test_training_missions_read_imported_quest_status_and_score(setup_gameplay):
    c = setup_gameplay
    prepare(c, chapters=True)
    call(c, "api/training-center")

    def imported(saved, progress):
        progress["features"]["statistics"]["data"]["totals"] = {}
        for row in saved["records"]["t_coaching_quests"]["up"]:
            row["status"] = 99 if row["m_coaching_quest_id"] == 10001001 else 2
        row = saved["records"]["t_tryout_quests"]["up"][0]
        row.update(status=2, high_score=3000000)

    edit(c, imported)
    before = snapshot(c)
    assert value(c, 260100, 10001001) == 1
    assert value(c, 260100, 10001002) == 0
    assert value(c, 260100, 99999999) == 0
    assert value(c, 270000, 30001001) == 3000000
    assert value(c, 270000, 30001002) == 0
    assert snapshot(c) == before


def test_coaching_clear_and_exemption_advance_the_real_mission(setup_gameplay):
    c = setup_gameplay
    prepare(c, chapters=True)
    call(c, "api/training-center/coaching/start-quest", {"m_coaching_quest_id": 10001001, "party_no": 0})
    mission = evaluator(c).catalog.get("m_missions", 70113)
    assert not evaluator(c).completed(mission)
    body = victory(c)
    call(c, "api/training-center/coaching/clear-quest", body)
    assert evaluator(c).completed(mission)
    before = snapshot(c)
    call(c, "api/training-center/coaching/clear-quest", body)
    assert snapshot(c) == before
    edit(c, lambda saved, _: next(row for row in saved["records"]["t_coaching_quests"]["up"]
                                 if row["m_coaching_quest_id"] == 10001002).update(is_exempted=1))
    assert value(c, 260100, 10001002) == 0
    call(c, "api/training-center/coaching/receive", {"m_coaching_quest_ids": [10001002]})
    assert value(c, 260100, 10001002) == 1


def test_tryout_exit_score_advances_missions_without_requiring_victory(setup_gameplay):
    c = setup_gameplay
    prepare(c, chapters=True)
    call(c, "api/training-center/tryout/start-quest", {"m_tryout_quest_id": 30001001, "party_no": 1})
    call(c, "api/training-center/tryout/close-quest", {"stage_results": {"play_time": 120, "total_damage": 3000000}})
    mission = evaluator(c).catalog.get("m_missions", 102702)
    assert evaluator(c).completed(mission)
    call(c, "api/training-center/tryout/start-quest", {"m_tryout_quest_id": 30001001, "party_no": 1})
    call(c, "api/training-center/tryout/surrender-quest", {"stage_results": {"play_time": 10, "total_damage": 1}})
    assert value(c, 270000, 30001001) == 3000000


def test_side_story_missions_are_not_misidentified_as_recollection(setup_gameplay):
    c = setup_gameplay
    prepare(c, chapters=True)
    call(c, "api/training-center")
    edit(c, lambda saved, _: [row.update(status=99) for row in saved["records"]["t_recollection_quests"]["up"]])
    checked = evaluator(c)
    recollections = {row["id"] for row in checked.catalog.rows("m_recollection_quests")}
    missions = [row for row in checked.catalog.rows("m_missions") if row["situation_type"] == 280000]
    assert len(missions) == 4
    assert all(row["target_id"] not in recollections for row in missions)
    assert all(checked.value(row) == 0 for row in missions)


def test_union_attempts_count_start_and_skip_once_with_union_target(setup_gameplay):
    c = setup_gameplay
    prepare(c)
    start = {"m_union_request_stage_id": 10001, "party_no": 1}
    call(c, "api/union-request/start-quest", start)
    assert value(c, 170001) == value(c, 170001, 1) == 1
    assert value(c, 170001, 2) == 0
    call(c, "api/union-request/start-quest", start)
    assert value(c, 170001, 1) == 1
    call(c, "api/union-request/surrender-quest", {"play_time": 1})
    assert value(c, 170002) == 0
    call(c, "api/union-request/start-quest", start)
    body = victory(c)
    call(c, "api/union-request/clear-quest", body)
    call(c, "api/union-request/clear-quest", body)
    assert value(c, 170001, 1) == 2
    assert value(c, 170002) == value(c, 170002, 10001) == value(c, 170100, 1) == 1
    assert value(c, 170002, 10002) == 0
    before = snapshot(c)
    call(c, "api/union-request/repeat-quest", {"union_type": 1, "exec_count": 2}, expected=409)
    assert snapshot(c) == before
    headers = {**c.headers, "x-request-id": "union-mission-repeat"}
    call(c, "api/union-request/repeat-quest", {"union_type": 1, "exec_count": 1}, headers=headers)
    before = snapshot(c)
    call(c, "api/union-request/repeat-quest", {"union_type": 1, "exec_count": 1}, headers=headers)
    assert snapshot(c) == before
    assert value(c, 170001) == value(c, 170001, 1) == 3
    assert value(c, 170002) == 1
    assert value(c, 170001, 1, kind=3) == 3
    assert value(c, 170001, 1, kind=3, now=NOW + timedelta(days=7)) == 0


def test_imported_union_stages_cover_missions_and_all_stage_offer_conditions(setup_gameplay):
    c = setup_gameplay
    prepare(c)

    def imported(saved, progress):
        progress["features"]["statistics"]["data"]["totals"] = {}
        for row in saved["records"]["t_union_requests"]["up"]:
            row["stage"] = {1: 4, 2: 71}.get(row["m_union_type_id"], 1)

    edit(c, imported)
    before = snapshot(c)
    assert value(c, 170002) == 73
    assert value(c, 170100, 1) == 3
    assert value(c, 170100, 2) == 70
    assert value(c, 170100, 3) == 0
    assert value(c, 170002, 10003) == value(c, 170002, 20070) == 1
    assert value(c, 170002, 10004) == value(c, 170002, 99999) == 0
    checked = evaluator(c)
    game = Gameplay(checked.state, checked.progress, checked.catalog, checked.user_id, NOW)
    offers = [row for row in checked.catalog.rows("m_payment_recommends") if row["condition_type"] == 7]
    assert len(offers) == 45
    for offer in offers:
        stage = checked.catalog.get("m_union_request_stages", offer["condition_id"])
        expected = stage["m_union_type_id"] == 2 or stage["m_union_type_id"] == 1 and stage["stage"] <= 3
        assert recommendation_eligible(game, offer) == expected
    assert snapshot(c) == before
