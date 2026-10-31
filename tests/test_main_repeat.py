import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError
from dotabyss_ps.gameplay_records import put
from test_economy_features import update
from test_gameplay import call, gameplay_master, party_body, setup_gameplay, snapshot
from test_main_story_battles import active_session, first_battle, table, victory


def prepare_repeat(context):
    call(context, "api/user")
    call(context, "api/party/exploration/edit", party_body())
    catalog = GameplayCatalog(context.tables, context.schema)
    repeat = catalog.rows("m_chapter_repeat_quests")[0]
    area = catalog.get("m_chapter_areas", repeat["m_chapter_area_id"])

    def finish_area(state, _):
        for quest in catalog.rows("m_chapter_quests"):
            if quest["m_chapter_area_id"] != area["id"]:
                continue
            sequence = max(row["sequence"] for row in catalog.rows("m_chapter_quest_sequences")
                           if row["m_chapter_quest_id"] == quest["id"]) + 1
            put(state, "t_chapter_quests", {"m_chapter_quest_id": quest["id"]}, {
                "m_chapter_id": area["m_chapter_id"], "m_chapter_area_id": area["id"], "sequence": sequence,
            })
        next(row for row in state["records"]["t_timed_parameters"]["up"] if row["type"] == 5)["amount"] = 500

    update(context, finish_area)
    return repeat


def state(context):
    return json.loads(snapshot(context)[0])


def progress(context):
    return json.loads(snapshot(context)[1])


def stamina(context):
    return next(row["amount"] for row in state(context)["records"]["t_timed_parameters"]["up"] if row["type"] == 5)


def first_claims(context):
    return copy.deepcopy(progress(context)["features"].get("battle_rewards"))


def test_repeat_start_resume_clear_and_distinct_next_session(setup_gameplay):
    context = setup_gameplay
    repeat = prepare_repeat(context)
    before_stamina = stamina(context)
    before_story = copy.deepcopy(state(context)["records"]["t_chapter_quests"])
    before_claims = first_claims(context)
    body = {"m_chapter_area_id": repeat["m_chapter_area_id"], "party_no": 1}
    started = call(context, "api/main-story/repeat/start-battle", body)
    session = active_session(context)
    assert started["quest_type"] == 3
    assert started["quest_id"] == repeat["m_chapter_quest_battle_id"]
    assert session["m_chapter_area_id"] == repeat["m_chapter_area_id"]
    assert stamina(context) == before_stamina - repeat["stamina"]
    stable = snapshot(context)
    assert call(context, "api/main-story/repeat/start-battle", body)["stage_detail"] == started["stage_detail"]
    assert snapshot(context) == stable
    with TestClient(create_app(context.settings)) as restarted:
        resumed = restarted.get("/api/battle-session/status", headers=context.headers).json()["contents"]
    assert resumed["stage_detail"] == started["stage_detail"]
    result = call(context, "api/main-story/repeat/clear-battle", {"stage_results": victory(session)["stage_results"]})
    assert result["battle_result"]["first_rewards"] == []
    assert active_session(context) is None
    assert state(context)["records"]["t_chapter_quests"] == before_story
    assert first_claims(context) == before_claims
    call(context, "api/main-story/repeat/start-battle", body)
    assert active_session(context)["id"] != session["id"]


def test_repeat_and_skip_require_complete_area(setup_gameplay):
    context = setup_gameplay
    call(context, "api/user")
    before = snapshot(context)
    call(context, "api/main-story/repeat/start-battle", {"m_chapter_area_id": 101, "party_no": 1}, expected=409)
    call(context, "api/main-story/repeat/skip-battle", {"m_chapter_area_id": 101, "skip_count": 1}, expected=409)
    assert snapshot(context) == before


def test_all_current_repeat_areas_have_one_real_dungeon_definition(gameplay_master):
    schema, tables = gameplay_master
    catalog = GameplayCatalog(tables, schema)
    repeats = catalog.rows("m_chapter_repeat_quests")
    areas = [row["m_chapter_area_id"] for row in repeats]
    assert len(areas) == len(set(areas))
    assert set(areas) == {row["id"] for row in catalog.rows("m_chapter_areas")}
    for repeat in repeats:
        battle = catalog.get("m_chapter_quest_battles", repeat["m_chapter_quest_battle_id"])
        assert battle["stage_type"] == 1
        assert catalog.get("m_dungeons", battle["stage_id"])


def test_duplicate_repeat_clear_and_old_clear_during_next_battle_are_safe(setup_gameplay):
    context = setup_gameplay
    repeat = prepare_repeat(context)
    start_body = {"m_chapter_area_id": repeat["m_chapter_area_id"], "party_no": 1}
    call(context, "api/main-story/repeat/start-battle", start_body)
    body = {"stage_results": victory(active_session(context))["stage_results"]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call(context, "api/main-story/repeat/clear-battle", body), range(2)))
    assert results[0]["battle_result"] == results[1]["battle_result"]
    call(context, "api/main-story/repeat/start-battle", start_body)
    current = active_session(context)
    before = snapshot(context)
    assert call(context, "api/main-story/repeat/clear-battle", body)["battle_result"] == results[0]["battle_result"]
    assert snapshot(context) == before
    assert active_session(context)["id"] == current["id"]


def test_repeat_start_receipt_cannot_reopen_an_abandoned_session(setup_gameplay):
    context = setup_gameplay
    repeat = prepare_repeat(context)
    body = {"m_chapter_area_id": repeat["m_chapter_area_id"], "party_no": 1}
    headers = {**context.headers, "x-request-id": "old-repeat-start"}
    call(context, "api/main-story/repeat/start-battle", body, headers=headers)
    call(context, "api/main-story/repeat/close-battle", {"play_time": 1})
    before = snapshot(context)
    call(context, "api/main-story/repeat/start-battle", body, headers=headers, expected=409)
    assert snapshot(context) == before


def test_repeat_skip_cannot_run_while_manual_battle_is_active(setup_gameplay):
    context = setup_gameplay
    repeat = prepare_repeat(context)
    call(context, "api/main-story/repeat/start-battle", {"m_chapter_area_id": repeat["m_chapter_area_id"], "party_no": 1})
    before = snapshot(context)
    call(context, "api/main-story/repeat/skip-battle", {"m_chapter_area_id": repeat["m_chapter_area_id"], "skip_count": 1}, expected=409)
    assert snapshot(context) == before


def test_skip_charges_all_runs_once_and_grants_no_first_rewards(setup_gameplay):
    context = setup_gameplay
    repeat = prepare_repeat(context)
    before_stamina = stamina(context)
    before_status = copy.deepcopy(state(context)["records"]["t_statuses"]["up"][0])
    before_story = copy.deepcopy(state(context)["records"]["t_chapter_quests"])
    before_claims = first_claims(context)
    body = {"m_chapter_area_id": repeat["m_chapter_area_id"], "skip_count": 3}
    headers = {**context.headers, "x-request-id": "repeat-skip-batch"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call(context, "api/main-story/repeat/skip-battle", body, headers=headers), range(2)))
    assert results[0]["drops"] == results[1]["drops"]
    assert results[0]["first_rewards"] == results[0]["clear_rewards"] == results[0]["event_drops"] == []
    assert results[0]["grant_character_exp"] == results[0]["after_character_exp"] - results[0]["before_character_exp"]
    after_status = state(context)["records"]["t_statuses"]["up"][0]
    gained_stamina = sum(row["stamina_max"] for row in table(context, "m_user_ranks")
                         if before_status["user_rank"] < row["rank"] <= after_status["user_rank"])
    assert stamina(context) == min(999, before_stamina - repeat["stamina"] * 3 + gained_stamina)
    assert after_status["player_exp"] == before_status["player_exp"] + repeat["stamina"] * 3
    assert progress(context)["features"]["statistics"]["data"]["totals"]["130001:0:0"] == repeat["stamina"] * 3
    assert state(context)["records"]["t_chapter_quests"] == before_story
    assert first_claims(context) == before_claims
    assert active_session(context) is None
    stable = snapshot(context)
    call(context, "api/main-story/repeat/skip-battle", body, headers=headers)
    assert snapshot(context) == stable


def test_skip_does_not_require_a_current_battle_party(setup_gameplay):
    context = setup_gameplay
    repeat = prepare_repeat(context)
    update(context, lambda current, _: current["records"]["t_exploration_party_characters"]["up"].clear())
    call(context, "api/main-story/repeat/skip-battle", {"m_chapter_area_id": repeat["m_chapter_area_id"], "skip_count": 1})
    assert active_session(context) is None


def test_skip_checks_total_stamina_before_granting(setup_gameplay):
    context = setup_gameplay
    repeat = prepare_repeat(context)

    def reduce(current, _):
        next(row for row in current["records"]["t_timed_parameters"]["up"] if row["type"] == 5)["amount"] = repeat["stamina"]

    update(context, reduce)
    before = snapshot(context)
    call(context, "api/main-story/repeat/skip-battle", {"m_chapter_area_id": repeat["m_chapter_area_id"], "skip_count": 2}, expected=409)
    assert snapshot(context) == before


def test_later_skip_failure_rolls_back_all_runs(setup_gameplay, monkeypatch):
    import dotabyss_ps.gameplay_main_repeat as module

    context = setup_gameplay
    repeat = prepare_repeat(context)
    original = module.settle_battle
    completed_runs = []

    def fail_second(game, session, result):
        response = original(game, session, result)
        completed_runs.append(response)
        if len(completed_runs) == 2:
            raise GameplayError("Injected second-run failure")
        return response

    monkeypatch.setattr(module, "settle_battle", fail_second)
    before = snapshot(context)
    call(context, "api/main-story/repeat/skip-battle", {"m_chapter_area_id": repeat["m_chapter_area_id"], "skip_count": 3}, expected=409)
    assert len(completed_runs) == 2
    assert snapshot(context) == before


@pytest.mark.parametrize("endpoint", ["close-battle", "surrender-battle"])
def test_repeat_abandon_does_not_refund_stamina_or_accept_old_results(setup_gameplay, endpoint):
    context = setup_gameplay
    repeat = prepare_repeat(context)
    body = {"m_chapter_area_id": repeat["m_chapter_area_id"], "party_no": 1}
    call(context, "api/main-story/repeat/start-battle", body)
    old = active_session(context)
    paid = stamina(context)
    result = call(context, "api/main-story/repeat/" + endpoint, {"play_time": 8})
    assert result["status"] == 0
    assert stamina(context) == paid
    call(context, "api/main-story/repeat/start-battle", body)
    before = snapshot(context)
    call(context, "api/main-story/repeat/clear-battle", {"stage_results": victory(old)["stage_results"]}, expected=400)
    assert snapshot(context) == before


def test_repeat_endpoints_do_not_close_an_active_main_story_battle(setup_gameplay):
    context = setup_gameplay
    _, session = first_battle(context)
    before = snapshot(context)
    call(context, "api/main-story/repeat/clear-battle", {"stage_results": victory(session)["stage_results"]}, expected=409)
    call(context, "api/main-story/repeat/surrender-battle", {"play_time": 0}, expected=409)
    assert snapshot(context) == before


@pytest.mark.parametrize("count", [0, 101, -1, True])
def test_invalid_skip_count_does_not_mutate_state(setup_gameplay, count):
    context = setup_gameplay
    repeat = prepare_repeat(context)
    before = snapshot(context)
    call(context, "api/main-story/repeat/skip-battle", {"m_chapter_area_id": repeat["m_chapter_area_id"], "skip_count": count}, expected=400)
    assert snapshot(context) == before
