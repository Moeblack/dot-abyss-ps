import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from dotabyss_ps.gameplay_catalog import GameplayCatalog, JST
from dotabyss_ps.gameplay_records import put
from dotabyss_ps.gameplay_tavern_score import draw_hand, score_turn
from test_economy_features import update
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records, seed
from test_tavern_features import configure, finish


def set_clock(monkeypatch, day, hour, minute=0):
    monkeypatch.setattr("test_gameplay.NOW", datetime(2026, 11, day, hour, minute, tzinfo=JST))


def finish_active(context, identifier, first, request_prefix="complete"):
    result = first
    turn = 0
    while not result.get("is_work_end"):
        turn += 1
        result = call(context, "api/tavern/select/cards", {
            "t_tavern_daily_card_id": identifier,
            "m_tavern_card_id": result["tavern_cards"][0]["m_tavern_card_id"],
            "m_tavern_character_card_id": 0, "random_target_ids": None,
        }, headers={**context.headers, "x-request-id": f"{request_prefix}-{turn}"})
    return result, turn


@pytest.mark.parametrize("work_level,turns", [(1, 3), (2, 4), (3, 5)])
def test_each_work_level_completes_its_full_rounds_once(setup_gameplay, work_level, turns):
    context = setup_gameplay
    identifier = configure(context)
    if work_level > 1:
        catalog = GameplayCatalog(context.tables, context.schema)
        effect = next(row for row in catalog.rows("m_building_skill_params")
                      if row["param_type"] == 200 and row["param_1"] == work_level)
        skill = catalog.get("m_building_skill_levels", effect["m_building_skill_level_id"])
        update(context, lambda state, _: put(state, "t_building_skills", {
            "m_building_id": skill["m_building_id"], "skill_type": skill["skill_type"]}, {
            "skill_level": skill["skill_level"], "checked_skill_level": 0}, context.identity["user_id"]))
    first = call(context, "api/tavern/exec/work", {"use_ticket": 0, "t_tavern_daily_card_id": identifier})
    result, actual = finish_active(context, identifier, first)
    assert actual == turns
    assert result["tavern_daily_card"]["selected_count"] == turns
    assert result["result_rewards"]
    assert records(context)["t_tavern_shops"]["up"][0]["total_play_count"] == 1


def test_calendar_midnight_does_not_reset_free_work(setup_gameplay, monkeypatch):
    context = setup_gameplay
    set_clock(monkeypatch, 1, 23, 59)
    identifier = configure(context)
    finish(context, identifier)
    set_clock(monkeypatch, 2, 0, 1)
    before = snapshot(context)
    call(context, "api/tavern/exec/work", {"use_ticket": 0, "t_tavern_daily_card_id": identifier}, expected=409)
    assert snapshot(context) == before
    set_clock(monkeypatch, 2, 5)
    result = call(context, "api/tavern/exec/work", {"use_ticket": 0, "t_tavern_daily_card_id": identifier})
    assert result["tavern_daily_card"]["day"] == 20261102


def test_active_work_crosses_five_am_and_retains_the_new_daily_run(setup_gameplay, monkeypatch):
    context = setup_gameplay
    set_clock(monkeypatch, 1, 4, 59)
    identifier = configure(context)
    first = call(context, "api/tavern/exec/work", {"use_ticket": 0, "t_tavern_daily_card_id": identifier})
    assert first["tavern_daily_card"]["day"] == 20261031
    set_clock(monkeypatch, 1, 5)
    assert call(context, "api/tavern/exec/work", {"use_ticket": 0, "t_tavern_daily_card_id": identifier})["tavern_cards"] == first["tavern_cards"]
    finish_active(context, identifier, first)
    new = call(context, "api/tavern/exec/work", {"use_ticket": 0, "t_tavern_daily_card_id": identifier})
    assert new["tavern_daily_card"]["day"] == 20261101
    assert new["tavern_daily_card"]["selected_count"] == 0


@pytest.mark.parametrize("old_day", ["2026-10-31", "2026-11-01"])
def test_legacy_day_key_cannot_duplicate_a_free_run(setup_gameplay, monkeypatch, old_day):
    context = setup_gameplay
    set_clock(monkeypatch, 1, 3)
    identifier = configure(context)
    finish(context, identifier)
    update(context, lambda _, progress: progress["features"]["tavern"]["data"].update(day=old_day))
    result = call(context, "api/tavern")
    assert result["tavern_daily_cards"][0]["day"] == 20261031
    call(context, "api/tavern/exec/work", {"use_ticket": 0, "t_tavern_daily_card_id": identifier}, expected=409)


def test_paid_skip_replay_charges_and_grants_once(setup_gameplay):
    context = setup_gameplay
    identifier = configure(context)
    seed(context, items={40001: 2})
    body = {"use_ticket": 1, "t_tavern_daily_card_id": identifier, "exec_count": 2}
    headers = {**context.headers, "x-request-id": "paid-skip-batch"}
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: call(context, "api/tavern/skip/work", body, headers=headers), range(2)))
    assert responses[0] == responses[1]
    state = records(context)
    assert state["t_tavern_shops"]["up"][0]["total_play_count"] == 2
    assert next(row["amount"] for row in state["t_items"]["up"] if row["m_item_id"] == 40001) == 0


def test_active_work_survives_service_recreation_without_charging_again(setup_gameplay):
    context = setup_gameplay
    identifier = configure(context)
    seed(context, items={40001: 1})
    first = call(context, "api/tavern/exec/work", {"use_ticket": 1, "t_tavern_daily_card_id": identifier})
    result = call(context, "api/tavern/select/card", {"t_tavern_daily_card_id": identifier,
                  "m_tavern_card_id": first["tavern_cards"][0]["m_tavern_card_id"]})
    with TestClient(create_app(context.settings)) as restarted:
        old_client = context.client
        context.client = restarted
        try:
            resumed = call(context, "api/tavern/exec/work", {"use_ticket": 1, "t_tavern_daily_card_id": identifier})
            assert resumed["tavern_cards"] == result["tavern_cards"]
            assert resumed["tavern_daily_card"]["selected_count"] == 1
            finished, turns = finish_active(context, identifier, resumed)
            assert finished["result_rewards"] and turns == 2
        finally:
            context.client = old_client
    assert next(row["amount"] for row in records(context)["t_items"]["up"] if row["m_item_id"] == 40001) == 0


@pytest.mark.parametrize("work_level,intimacy,categories,expected", [
    (1, 0, [], 150), (1, 0, [1, 2], 180), (1, 0, [1, 2, 3], 300),
    (3, 15, [], 400), (3, 15, [1, 2], 480), (3, 15, [1, 2, 3], 800),
])
def test_client_base_score_and_combo_contract(work_level, intimacy, categories, expected):
    session = {"work_level": work_level, "score_rules": {"tavern_use_client_score_parameters": 1},
               "workers": [{"job_level": 99, "intimacy_level": intimacy, "rate": 0, "flat": 0,
                            "categories": categories, "score": 0}]}
    assert score_turn(None, session) == expected


def test_legacy_active_scoring_keeps_its_existing_formula():
    session = {"work_level": 3, "score_rules": {"tavern_base_score": 100, "tavern_combo_permille": 500},
               "workers": [{"job_level": 5, "rate": 0, "flat": 0, "categories": [1, 2], "score": 0}]}
    assert score_turn(None, session) == 750


def test_reload_hand_is_deterministic_and_matches_requested_card_targets(gameplay_master):
    from types import SimpleNamespace
    schema, tables = gameplay_master
    catalog = GameplayCatalog(tables, schema)
    game = SimpleNamespace(catalog=catalog)
    for kind, param in ((100, 2), (101, 1), (102, 1), (103, 2)):
        session = {"seed": 100, "turn": 1, "rerolls": 1, "card_level": 1, "reload": {"kind": kind, "param": param},
                   "workers": [{"character": 1200002, "work": 100, "categories": []}]}
        duplicate = copy.deepcopy(session)
        draw_hand(game, session)
        draw_hand(game, duplicate)
        assert session["hand"] == duplicate["hand"] and session["hand"]
        assert "reload" not in session
        expected = {10006, 10007, 10008, 10011} if kind == 101 else {10000, 10003, 10006, 10009, 10010, 10011}
        assert set(session["hand"]) <= expected


def test_reload_without_matching_worker_retains_a_playable_hand(gameplay_master):
    from types import SimpleNamespace
    schema, tables = gameplay_master
    game = SimpleNamespace(catalog=GameplayCatalog(tables, schema))
    session = {"seed": 100, "turn": 1, "rerolls": 1, "card_level": 1,
               "workers": [{"character": 1200002, "work": 100, "categories": []}]}
    ordinary = copy.deepcopy(session)
    session["reload"] = {"kind": 100, "param": 999}
    draw_hand(game, session)
    draw_hand(game, ordinary)
    assert session["hand"] == ordinary["hand"] and len(session["hand"]) == 3


def test_client_score_switch_is_frozen_with_work_session(setup_gameplay):
    import json
    context = setup_gameplay
    identifier = configure(context)
    rule_file = context.settings.data_dir / "gameplay-rules.json"
    rule_file.write_text(json.dumps({"tavern_use_client_score_parameters": 0}), encoding="utf-8")
    first = call(context, "api/tavern/exec/work", {"use_ticket": 0, "t_tavern_daily_card_id": identifier})
    rule_file.write_text(json.dumps({"tavern_use_client_score_parameters": 1}), encoding="utf-8")
    call(context, "api/tavern/select/card", {"t_tavern_daily_card_id": identifier,
         "m_tavern_card_id": first["tavern_cards"][0]["m_tavern_card_id"]})
    active = json.loads(snapshot(context)[1])["features"]["tavern"]["data"]["active"]
    assert active["score_rules"]["tavern_use_client_score_parameters"] == 0
