import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from dotabyss_ps.gameplay_catalog import JST
from test_gameplay import NOW, call, gameplay_master, party_body, setup_gameplay, snapshot
from test_battle_stages import assert_links, battle_catalog, game_for


def table(context, name):
    fields = [field["name"] for field in context.schema["tables"][name]]
    return [dict(zip(fields, row)) for row in context.tables[name]]


def records(context):
    return json.loads(snapshot(context)[0])["records"]


def edit(context, change):
    state, progress = map(json.loads, snapshot(context))
    change(state, progress)
    with context.store.connection() as db:
        db.execute("UPDATE game_states SET contents=? WHERE profile_id=?", (json.dumps(state), context.identity["user_id"]))
        db.execute("UPDATE gameplay_progress SET contents=? WHERE profile_id=?", (json.dumps(progress), context.identity["user_id"]))


def prepare(context, chapters=False):
    call(context, "api/user")
    call(context, "api/party/exploration/edit", party_body())
    if chapters:
        areas = {row["id"]: row for row in table(context, "m_chapter_areas")}
        steps = table(context, "m_chapter_quest_sequences")
        rows = [{"id": index, "m_chapter_quest_id": quest["id"], "m_chapter_area_id": quest["m_chapter_area_id"],
                 "m_chapter_id": areas[quest["m_chapter_area_id"]]["m_chapter_id"],
                 "sequence": max(row["sequence"] for row in steps if row["m_chapter_quest_id"] == quest["id"]) + 1}
                for index, quest in enumerate(table(context, "m_chapter_quests"), 1)]
        edit(context, lambda state, _: state["records"]["t_chapter_quests"].update(up=rows))


def active(context):
    return json.loads(snapshot(context)[1])["features"]["battle"]["data"]["active"]


def victory(context):
    from dotabyss_ps.gameplay_main_repeat import skipped_result

    session = active(context)
    stage = session["stage_detail"]
    result = skipped_result(stage, session["stage_type"])
    result.update(play_time=1, top_fc_damage=100)
    if session.get("result_schema") == "exploration" and session["stage_type"] == 2:
        result.update(total_damage=1000, passed_floor_parts=[], clear_rank=0)
    return {"stage_results": result}


def clock(monkeypatch, when):
    import dotabyss_ps.gameplay as gameplay

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return when.astimezone(tz)

    monkeypatch.setattr(gameplay, "datetime", Clock)


def supply(context, kind):
    return next(row for row in records(context)["t_timed_parameters"]["up"] if row["type"] == kind)


def encounter_spawn(context, monkeypatch):
    prepare(context)
    call(context, "api/idle-exploration", {"sequence": ""})
    call(context, "api/party/idle-exploration/edit", {"party_no": 1, "characters": [{"position_no": 1, "t_character_id": 1, "t_weapon_id": 0}]})
    area = min(row["m_idle_exploration_map_area_id"] for row in records(context)["t_idle_exploration_map_areas"]["up"])
    call(context, "api/party/idle-exploration/assign", {"party_no": 1, "assign_to": area})
    clock(monkeypatch, NOW + timedelta(hours=1))
    call(context, "api/idle-exploration", {"sequence": ""})
    return records(context)["t_idle_exploration_encounters"]["up"][0]


def test_training_restrictions_status_and_fixed_party_battle(setup_gameplay):
    context = setup_gameplay
    prepare(context)
    top = call(context, "api/training-center")
    assert set(top["training_center_version"]) == {"coaching", "recollection", "tryout"}
    call(context, "api/training-center/coaching/start-quest", {"m_coaching_quest_id": 10001001, "party_no": 0}, expected=409)
    prepare(context, chapters=True)
    call(context, "api/training-center/check", {"m_coaching_quest_ids": [10001001], "m_recollection_quest_ids": [], "m_tryout_quest_ids": []})
    response = call(context, "api/training-center/coaching/start-quest", {"m_coaching_quest_id": 10001001, "party_no": 0})
    assert response["quest_type"] == 9 and response["stage_type"] == 2
    body = victory(context)
    won = call(context, "api/training-center/coaching/clear-quest", body)
    assert won["first_rewards"]
    before = snapshot(context)
    assert call(context, "api/training-center/coaching/clear-quest", body)["first_rewards"] == won["first_rewards"]
    assert snapshot(context) == before
    assert next(row for row in records(context)["t_coaching_quests"]["up"] if row["m_coaching_quest_id"] == 10001001)["status"] == 99
    assert next(row for row in records(context)["t_coaching_quests"]["up"] if row["m_coaching_quest_id"] == 10001002)["status"] == 1


def test_training_reward_receive_requires_exemption_and_is_once(setup_gameplay):
    context = setup_gameplay
    prepare(context, chapters=True)
    call(context, "api/training-center")
    body = {"m_coaching_quest_ids": [10001001]}
    call(context, "api/training-center/coaching/receive", body, expected=409)
    edit(context, lambda state, _: next(row for row in state["records"]["t_coaching_quests"]["up"] if row["m_coaching_quest_id"] == 10001001).update(is_exempted=1))
    assert call(context, "api/training-center/coaching/receive", body)["first_rewards"]
    before = snapshot(context)
    assert call(context, "api/training-center/coaching/receive", body)["first_rewards"] == []
    assert snapshot(context) == before


def test_tryout_score_on_exit_never_fakes_a_victory(setup_gameplay):
    context = setup_gameplay
    prepare(context, chapters=True)
    call(context, "api/training-center/tryout/start-quest", {"m_tryout_quest_id": 30001001, "party_no": 1})
    response = call(context, "api/training-center/tryout/close-quest", {"stage_results": {"play_time": 120, "total_damage": 987654}})
    assert response["status"] == 0
    row = records(context)["t_tryout_quests"]["up"][0]
    assert row["high_score"] == 987654 and row["status"] != 99
    call(context, "api/training-center/tryout/start-quest", {"m_tryout_quest_id": 30001001, "party_no": 1})
    call(context, "api/training-center/tryout/surrender-quest", {"stage_results": {"play_time": 10, "total_damage": 1}})
    assert records(context)["t_tryout_quests"]["up"][0]["high_score"] == 987654


def test_recollection_resume_and_wrong_training_close(setup_gameplay):
    context = setup_gameplay
    prepare(context, chapters=True)
    started = call(context, "api/training-center/recollection/start-quest", {"m_recollection_quest_id": 20001001, "party_no": 1})
    with TestClient(create_app(context.settings)) as client:
        assert client.get("/api/battle-session/status", headers=context.headers).json()["contents"]["stage_detail"] == started["stage_detail"]
    before = snapshot(context)
    call(context, "api/training-center/coaching/close-quest", {"play_time": 0}, expected=409)
    assert snapshot(context) == before
    assert not call(context, "api/training-center/recollection/clear-quest", victory(context))["first_rewards"]


def test_union_progress_supply_and_repeat_rewards(setup_gameplay):
    context = setup_gameplay
    prepare(context)
    start = {"m_union_request_stage_id": 10001, "party_no": 1}
    response = call(context, "api/union-request/start-quest", start)
    assert response["quest_type"] == 6 and response["quest_id"] == 10001
    assert supply(context, 7)["amount"] == 2
    call(context, "api/union-request/start-quest", start)
    assert supply(context, 7)["amount"] == 2
    body = victory(context)
    won = call(context, "api/union-request/clear-quest", body)
    assert won["first_rewards"]
    assert next(row for row in records(context)["t_union_requests"]["up"] if row["m_union_type_id"] == 1)["stage"] == 2
    before = snapshot(context)
    call(context, "api/union-request/clear-quest", body)
    assert snapshot(context) == before
    skipped = call(context, "api/union-request/repeat-quest", {"union_type": 1, "exec_count": 2})
    assert skipped["first_rewards"] == [] and skipped["clear_rewards"]
    assert supply(context, 7)["amount"] == 0


def test_union_daily_reset_and_closed_weekday(setup_gameplay, monkeypatch):
    context = setup_gameplay
    prepare(context)
    call(context, "api/union-request/start-quest", {"m_union_request_stage_id": 10001, "party_no": 1})
    call(context, "api/union-request/close-quest", {"play_time": 0})
    assert supply(context, 7)["amount"] == 2
    clock(monkeypatch, NOW + timedelta(days=1))
    call(context, "api/union-request/start-quest", {"m_union_request_stage_id": 10001, "party_no": 1}, expected=409)
    clock(monkeypatch, NOW + timedelta(days=7))
    call(context, "api/union-request/start-quest", {"m_union_request_stage_id": 10001, "party_no": 1})
    assert supply(context, 7)["amount"] == 2


def test_union_skip_insufficient_attempts_rolls_back(setup_gameplay):
    context = setup_gameplay
    prepare(context)
    call(context, "api/union-request/start-quest", {"m_union_request_stage_id": 10001, "party_no": 1})
    call(context, "api/union-request/clear-quest", victory(context))
    before = snapshot(context)
    call(context, "api/union-request/repeat-quest", {"union_type": 1, "exec_count": 3}, expected=409)
    assert snapshot(context) == before


def test_union_weekday_opens_at_midnight_before_attempt_reset(setup_gameplay, monkeypatch):
    context = setup_gameplay
    clock(monkeypatch, NOW.replace(hour=2))
    prepare(context)
    call(context, "api/union-request/start-quest", {"m_union_request_stage_id": 30001, "party_no": 1})
    assert active(context)["quest_type"] == 6


def test_encounters_appear_once_expire_and_never_reuse_ids(setup_gameplay, monkeypatch):
    context = setup_gameplay
    first = encounter_spawn(context, monkeypatch)
    before_ids = [row["id"] for row in records(context)["t_idle_exploration_encounters"]["up"]]
    call(context, "api/idle-exploration", {"sequence": ""})
    assert [row["id"] for row in records(context)["t_idle_exploration_encounters"]["up"]] == before_ids
    clock(monkeypatch, NOW + timedelta(hours=8))
    call(context, "api/idle-exploration", {"sequence": ""})
    current = records(context)["t_idle_exploration_encounters"]["up"]
    assert current and len(current) <= 6
    assert all(row["id"] > first["id"] for row in current)


def test_encounter_clear_unlocks_skip_and_consumes_supply_once(setup_gameplay, monkeypatch):
    context = setup_gameplay
    row = encounter_spawn(context, monkeypatch)
    start = {"t_idle_exploration_encounter_id": row["id"], "party_no": 1}
    call(context, "api/idle-exploration/encounter/skip-quest", {"t_idle_exploration_encounter_id": row["id"]}, expected=409)
    call(context, "api/idle-exploration/encounter/start-quest", start)
    assert supply(context, 4)["amount"] == 2
    call(context, "api/idle-exploration/encounter/start-quest", start)
    assert supply(context, 4)["amount"] == 2
    body = victory(context)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call(context, "api/idle-exploration/encounter/clear-quest", body), range(2)))
    assert results[0]["clear_rewards"] == results[1]["clear_rewards"]
    assert not any(item["id"] == row["id"] for item in records(context)["t_idle_exploration_encounters"]["up"])
    edit(context, lambda state, _: state["records"]["t_idle_exploration_encounters"]["up"].append({**row, "id": 999}))
    result = call(context, "api/idle-exploration/encounter/skip-quest", {"t_idle_exploration_encounter_id": 999})
    assert result["skip_count"] == 1 and supply(context, 4)["amount"] == 1
    before = snapshot(context)
    call(context, "api/idle-exploration/encounter/skip-quest", {"t_idle_exploration_encounter_id": 999})
    assert snapshot(context) == before


def test_started_encounter_survives_expiry_and_rejects_different_context(setup_gameplay, monkeypatch):
    context = setup_gameplay
    row = encounter_spawn(context, monkeypatch)
    edit(context, lambda state, _: state["records"]["t_idle_exploration_encounters"]["up"].append({**row, "id": 999}))
    call(context, "api/idle-exploration/encounter/start-quest", {"t_idle_exploration_encounter_id": row["id"], "party_no": 1})
    before = snapshot(context)
    call(context, "api/idle-exploration/encounter/start-quest", {"t_idle_exploration_encounter_id": 999, "party_no": 1}, expected=409)
    assert snapshot(context) == before
    body = victory(context)
    clock(monkeypatch, datetime.fromisoformat(row["end_at"]).astimezone(JST))
    call(context, "api/idle-exploration/encounter/clear-quest", body)
    before = snapshot(context)
    call(context, "api/idle-exploration/encounter/clear-quest", body)
    assert snapshot(context) == before


def test_abandoning_expired_encounter_removes_its_entry(setup_gameplay, monkeypatch):
    context = setup_gameplay
    row = encounter_spawn(context, monkeypatch)
    call(context, "api/idle-exploration/encounter/start-quest", {"t_idle_exploration_encounter_id": row["id"], "party_no": 1})
    clock(monkeypatch, datetime.fromisoformat(row["end_at"]).astimezone(JST))
    call(context, "api/idle-exploration/encounter/close-quest", {"play_time": 1})
    assert active(context) is None
    assert not any(value["id"] == row["id"] for value in records(context)["t_idle_exploration_encounters"]["up"])


def test_encounter_supply_purchase_cost_limit_and_request_replay(setup_gameplay):
    context = setup_gameplay
    prepare(context)
    def empty(state, _):
        next(row for row in state["records"]["t_timed_parameters"]["up"] if row["type"] == 4)["amount"] = 0
        state["records"]["t_statuses"]["up"][0]["free_virtual_money"] = 1000
    edit(context, empty)
    headers = {**context.headers, "x-request-id": "encounter-recovery"}
    call(context, "api/idle-exploration/encounter/recovery-supply", {"recovery_count": 2}, headers=headers)
    assert supply(context, 4)["amount"] == 2 and supply(context, 14)["amount"] == 1
    assert records(context)["t_statuses"]["up"][0]["free_virtual_money"] == 900
    before = snapshot(context)
    call(context, "api/idle-exploration/encounter/recovery-supply", {"recovery_count": 2}, headers=headers)
    assert snapshot(context) == before
    call(context, "api/idle-exploration/encounter/recovery-supply", {"recovery_count": 2}, expected=409)
    assert snapshot(context) == before


@pytest.mark.parametrize("kind,master", [("coaching", "m_coaching_quests"), ("recollection", "m_recollection_quests"),
                                         ("tryout", "m_tryout_quests"), ("encounter", "m_encounter_quests"),
                                         ("union", "m_union_request_stages")])
def test_all_mode_stage_definitions_have_valid_native_references(battle_catalog, kind, master):
    from dotabyss_ps.gameplay_battle_stages import build_stage

    game = game_for(battle_catalog)
    for index, definition in enumerate(battle_catalog.rows(master), 1):
        stage = build_stage(game, definition["stage_type"], definition["stage_id"], index, kind=kind, quest_id=definition["id"],
                            fixed_fort_id=definition.get("m_fixed_fort_id", 0), party_no=0 if definition.get("m_fixed_party_id") else 1)
        assert_links(stage)
        for entity in stage["enemies"] + ([stage["boss_enemy"]] if stage.get("boss_enemy") else []):
            enemy = battle_catalog.get("m_enemies", entity["m_enemy_id"])
            assert battle_catalog.get("m_enemy_parameters", enemy["m_enemy_parameter_id"])["hp"] > 0
