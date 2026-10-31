import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from test_gameplay import call, gameplay_master, party_body, setup_gameplay, snapshot


def active_session(context):
    return json.loads(snapshot(context)[1])["features"]["battle"]["data"]["active"]


def victory(session):
    stage = session["stage_detail"]
    enemies = stage["enemies"] + ([stage["boss_enemy"]] if stage.get("boss_enemy") else [])
    result = {"play_time": 1, "destroyed_enemies": [row["sid"] for row in enemies],
              "drop_items": [row["sid"] for row in stage["drops"]], "mana_gem_count": 0, "top_fc_damage": 100}
    if session["stage_type"] == 1:
        result.update(total_damage=1000, passed_floor_parts=[row["sid"] for row in stage["floor_parts"]], clear_rank=0)
    return {"m_chapter_quest_id": session["m_chapter_quest_id"], "stage_results": result}


def first_battle(context):
    call(context, "api/user")
    call(context, "api/party/exploration/edit", party_body())
    for step in (1, 2):
        call(context, "api/main-story/update-sequence", {"m_chapter_quest_id": 1010101, "sequence": step})
    response = call(context, "api/main-story/start-battle", {"m_chapter_quest_id": 1010101, "party_no": 1})
    return response, active_session(context)


def table(context, name):
    fields = [field["name"] for field in context.schema["tables"][name]]
    return [dict(zip(fields, row)) for row in context.tables[name]]


def prepared_paid_battle(context, stage_type=1):
    call(context, "api/user")
    call(context, "api/party/exploration/edit", party_body())
    quests = {row["id"]: row for row in table(context, "m_chapter_quests")}
    battles = {row["id"]: row for row in table(context, "m_chapter_quest_battles")}
    steps = table(context, "m_chapter_quest_sequences")
    step = next(row for row in steps if row["m_chapter_quest_battle_id"] and
                battles[row["m_chapter_quest_battle_id"]]["stage_type"] == stage_type and
                (stage_type == 2 or quests[row["m_chapter_quest_id"]]["stamina"] > 0))
    quest = quests[step["m_chapter_quest_id"]]
    state = json.loads(snapshot(context)[0])
    area = next(row for row in table(context, "m_chapter_areas") if row["id"] == quest["m_chapter_area_id"])
    state["records"]["t_chapter_quests"]["up"] = [{"id": 1, "m_chapter_quest_id": quest["id"],
        "m_chapter_id": area["m_chapter_id"], "m_chapter_area_id": area["id"], "sequence": step["sequence"]}]
    for row in state["records"]["t_timed_parameters"]["up"]:
        if row["type"] == 5:
            row["amount"] = 999
    with context.store.connection() as db:
        db.execute("UPDATE game_states SET contents=? WHERE profile_id=?", (json.dumps(state), context.identity["user_id"]))
    return quest


def test_main_story_start_resume_win_novel_finish_and_next_quest(setup_gameplay):
    context = setup_gameplay
    response, session = first_battle(context)
    assert (response["status"], response["quest_type"], response["quest_id"], response["stage_type"]) == (1, 2, 1001, 1)
    assert json.loads(response["party_detail"]) == {"party_no": 1}
    assert json.loads(response["stage_detail"]) == session["stage_detail"]
    before = snapshot(context)
    repeated = call(context, "api/main-story/start-battle", {"m_chapter_quest_id": 1010101, "party_no": 1})
    assert repeated["stage_detail"] == response["stage_detail"]
    assert snapshot(context) == before
    with TestClient(create_app(context.settings)) as restarted:
        resumed = restarted.get("/api/battle-session/status", headers=context.headers).json()["contents"]
    assert resumed["stage_detail"] == response["stage_detail"]
    won = call(context, "api/main-story/clear-battle", victory(session))
    assert won["battle_result"]["first_rewards"]
    assert active_session(context) is None
    assert next(row for row in won["records"]["t_chapter_quests"]["up"] if row["m_chapter_quest_id"] == 1010101)["sequence"] == 3
    finished = call(context, "api/main-story/clear", {"m_chapter_quest_id": 1010101})
    assert finished["receive_content_list"]
    assert finished["release_data"]["release_m_chapter_quest_ids"]
    next_quest = finished["release_data"]["release_m_chapter_quest_ids"][0]
    assert next_quest != 1010101
    call(context, "api/main-story/update-sequence", {"m_chapter_quest_id": next_quest, "sequence": 1})
    stable = snapshot(context)
    call(context, "api/main-story/clear", {"m_chapter_quest_id": 1010101})
    assert snapshot(context) == stable


def test_duplicate_and_concurrent_clear_do_not_grant_twice(setup_gameplay):
    context = setup_gameplay
    _, session = first_battle(context)
    body = victory(session)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: call(context, "api/main-story/clear-battle", body), range(2)))
    assert responses[0]["battle_result"] == responses[1]["battle_result"]
    state = snapshot(context)
    assert call(context, "api/main-story/clear-battle", body)["battle_result"] == responses[0]["battle_result"]
    assert snapshot(context) == state
    changed = copy.deepcopy(body)
    changed["stage_results"]["play_time"] += 1
    call(context, "api/main-story/clear-battle", changed, expected=409)
    assert snapshot(context) == state


@pytest.mark.parametrize("field,value", [("destroyed_enemies", [9223372036854770000]), ("drop_items", [9223372036854770000]),
                                          ("passed_floor_parts", [9223372036854770000]), ("destroyed_enemies", []), ("play_time", True)])
def test_untrusted_battle_results_roll_back_everything(setup_gameplay, field, value):
    context = setup_gameplay
    _, session = first_battle(context)
    body = victory(session)
    body["stage_results"][field] = value
    before = snapshot(context)
    response = context.client.post("/api/main-story/clear-battle", json=body, headers=context.headers)
    assert response.status_code in (400, 409)
    assert snapshot(context) == before


def test_surrender_leaves_story_unchanged_and_new_session_rejects_old_results(setup_gameplay):
    context = setup_gameplay
    _, original = first_battle(context)
    call(context, "api/main-story/surrender-battle", {"play_time": 12})
    assert call(context, "api/battle-session/status")["status"] == 0
    call(context, "api/main-story/start-battle", {"m_chapter_quest_id": 1010101, "party_no": 1})
    current = active_session(context)
    assert current["id"] != original["id"]
    assert current["stage_detail"] != original["stage_detail"]
    before = snapshot(context)
    call(context, "api/main-story/clear-battle", victory(original), expected=400)
    assert snapshot(context) == before
    call(context, "api/main-story/clear-battle", victory(current))


def test_quest_reward_and_sequence_cannot_bypass_battle(setup_gameplay):
    context = setup_gameplay
    _, session = first_battle(context)
    before = snapshot(context)
    call(context, "api/main-story/clear", {"m_chapter_quest_id": 1010101}, expected=409)
    call(context, "api/main-story/update-sequence", {"m_chapter_quest_id": 1010101, "sequence": 3}, expected=409)
    assert snapshot(context) == before
    call(context, "api/main-story/clear-battle", victory(session))


def test_original_start_receipt_cannot_resurrect_finished_session(setup_gameplay):
    context = setup_gameplay
    call(context, "api/user")
    call(context, "api/party/exploration/edit", party_body())
    for step in (1, 2):
        call(context, "api/main-story/update-sequence", {"m_chapter_quest_id": 1010101, "sequence": step})
    body = {"m_chapter_quest_id": 1010101, "party_no": 1}
    headers = {**context.headers, "x-request-id": "battle-start-retry"}
    call(context, "api/main-story/start-battle", body, headers=headers)
    call(context, "api/main-story/clear-battle", victory(active_session(context)))
    before = snapshot(context)
    call(context, "api/main-story/start-battle", body, headers=headers, expected=409)
    assert snapshot(context) == before


def test_failed_reward_rolls_back_session_rewards_and_progress(setup_gameplay, monkeypatch):
    import dotabyss_ps.gameplay_battle_rewards as rewards

    context = setup_gameplay
    _, session = first_battle(context)
    before = snapshot(context)
    original = rewards.first_rewards

    def fail_after_grant(game, current):
        original(game, current)
        from dotabyss_ps.gameplay_catalog import GameplayError
        raise GameplayError("Injected settlement failure")

    monkeypatch.setattr(rewards, "first_rewards", fail_after_grant)
    call(context, "api/main-story/clear-battle", victory(session), expected=409)
    assert snapshot(context) == before


@pytest.mark.parametrize("stage_type", [1, 2])
def test_master_stamina_charged_once_and_dungeon_or_defence_settles(setup_gameplay, stage_type):
    context = setup_gameplay
    quest = prepared_paid_battle(context, stage_type)
    body = {"m_chapter_quest_id": quest["id"], "party_no": 1}
    call(context, "api/main-story/start-battle", body)
    session = active_session(context)
    state = json.loads(snapshot(context)[0])
    stamina = next(row for row in state["records"]["t_timed_parameters"]["up"] if row["type"] == 5)
    assert stamina["amount"] == 999 - quest["stamina"]
    assert session["stamina_spent"] == quest["stamina"]
    before = snapshot(context)
    call(context, "api/main-story/start-battle", body)
    assert snapshot(context) == before
    won = call(context, "api/main-story/clear-battle", victory(session))
    assert won["battle_result"]["first_rewards"]
    assert active_session(context) is None


def test_insufficient_stamina_does_not_create_a_session(setup_gameplay):
    context = setup_gameplay
    quest = prepared_paid_battle(context)
    state = json.loads(snapshot(context)[0])
    for row in state["records"]["t_timed_parameters"]["up"]:
        if row["type"] == 5:
            row["amount"] = 0
    with context.store.connection() as db:
        db.execute("UPDATE game_states SET contents=? WHERE profile_id=?", (json.dumps(state), context.identity["user_id"]))
    before = snapshot(context)
    call(context, "api/main-story/start-battle", {"m_chapter_quest_id": quest["id"], "party_no": 1}, expected=409)
    assert snapshot(context) == before


def test_final_novel_sequence_then_clear_retains_release_notifications(setup_gameplay):
    context = setup_gameplay
    _, session = first_battle(context)
    call(context, "api/main-story/clear-battle", victory(session))
    call(context, "api/main-story/update-sequence", {"m_chapter_quest_id": 1010101, "sequence": 4})
    response = call(context, "api/main-story/clear", {"m_chapter_quest_id": 1010101})
    assert response["release_data"]["release_m_chapter_quest_ids"]
    assert response["receive_content_list"]


def test_start_receipt_from_previous_attempt_rejected_with_same_clock(setup_gameplay):
    context = setup_gameplay
    _, _ = first_battle(context)
    body = {"m_chapter_quest_id": 1010101, "party_no": 1}
    headers = {**context.headers, "x-request-id": "same-second-start"}
    call(context, "api/main-story/start-battle", body, headers=headers)
    call(context, "api/main-story/close-battle", {"play_time": 1})
    call(context, "api/main-story/start-battle", body)
    before = snapshot(context)
    call(context, "api/main-story/start-battle", body, headers=headers, expected=409)
    assert snapshot(context) == before
