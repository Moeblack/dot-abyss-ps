import json
from datetime import timedelta

import pytest

from dotabyss_ps.gameplay_catalog import GameplayCatalog
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records, seed


def configure(context):
    result = call(context, "api/tavern")
    identifier = result["tavern_daily_cards"][0]["id"]
    call(context, "api/tavern/set/characters", {"set_info": [{"m_tavern_work_id": 100, "m_character_id": 1200002}]})
    return identifier


def finish(context, identifier, *, combined=False):
    result = call(context, "api/tavern/exec/work", {"use_ticket": 0, "t_tavern_daily_card_id": identifier})
    for turn in range(3):
        body = {"t_tavern_daily_card_id": identifier, "m_tavern_card_id": result["tavern_cards"][0]["m_tavern_card_id"]}
        if combined:
            body.update(m_tavern_character_card_id=0, random_target_ids={"tavern_character_card": [], "tavern_card": []})
        result = call(context, "api/tavern/select/cards" if combined else "api/tavern/select/card", body,
                      headers={**context.headers, "x-request-id": f"tavern-turn-{turn}"})
    assert result["is_work_end"] == 1
    return result, body


@pytest.mark.parametrize("combined", [False, True])
def test_tavern_work_result_matches_master_rewards_and_replay_is_safe(setup_gameplay, combined):
    c = setup_gameplay
    identifier = configure(c)
    gold = records(c)["t_statuses"]["up"][0]["gold"]
    result, body = finish(c, identifier, combined=combined)
    assert result["result_score"] > 0 and 1 <= result["result_level"] <= 10
    master = GameplayCatalog(c.tables, c.schema)
    expected_gold = next(r["amount"] for r in master.rows("m_tavern_reward_levels") if r["reward_level"] == 1 and r["result_level"] == result["result_level"] and r["content_type"] == 50)
    assert records(c)["t_statuses"]["up"][0]["gold"] == gold + expected_gold
    before = snapshot(c)
    replay = call(c, "api/tavern/select/cards" if combined else "api/tavern/select/card", body, headers={**c.headers, "x-request-id": "tavern-turn-2"})
    assert replay == result and snapshot(c) == before
    call(c, "api/tavern/exec/work", {"use_ticket": 0, "t_tavern_daily_card_id": identifier}, expected=409)
    assert snapshot(c) == before
    assert records(c)["t_tavern_shops"]["up"][0]["total_play_count"] == 1


def test_active_work_can_resume_without_another_ticket(setup_gameplay):
    c = setup_gameplay
    identifier = configure(c)
    seed(c, items={40001: 2})
    body = {"use_ticket": 1, "t_tavern_daily_card_id": identifier}
    first = call(c, "api/tavern/exec/work", body)
    assert call(c, "api/tavern/exec/work", body)["tavern_cards"] == first["tavern_cards"]
    assert next(r["amount"] for r in records(c)["t_items"]["up"] if r["m_item_id"] == 40001) == 1
    before = snapshot(c)
    call(c, "api/tavern/select/card", {"t_tavern_daily_card_id": identifier, "m_tavern_card_id": 999999}, expected=409)
    call(c, "api/tavern/set/characters", {"set_info": []}, expected=409)
    assert snapshot(c) == before
    assert call(c, "api/tavern")["tavern_daily_cards"][0]["work_seed"] == first["tavern_daily_card"]["work_seed"]


def test_skip_costs_and_all_rewards_rollback_when_batch_is_unaffordable(setup_gameplay):
    c = setup_gameplay
    identifier = configure(c)
    seed(c, items={40001: 1})
    before = snapshot(c)
    call(c, "api/tavern/skip/work", {"use_ticket": 1, "t_tavern_daily_card_id": identifier, "exec_count": 2}, expected=409)
    assert snapshot(c) == before
    result = call(c, "api/tavern/skip/work", {"use_ticket": 1, "t_tavern_daily_card_id": identifier, "exec_count": 1})
    assert result["result_rewards"] and result["mvp_m_character_id"] == 1200002
    assert records(c)["t_tavern_shops"]["up"][0]["total_play_count"] == 1


def test_day_rollover_preserves_active_work_and_grants_new_free_run(setup_gameplay, monkeypatch):
    c = setup_gameplay
    identifier = configure(c)
    call(c, "api/tavern/exec/work", {"use_ticket": 0, "t_tavern_daily_card_id": identifier})
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(days=1))
    result, _ = finish(c, identifier)
    assert result["result_rewards"]
    next_run = call(c, "api/tavern/exec/work", {"use_ticket": 0, "t_tavern_daily_card_id": identifier})
    assert next_run["tavern_daily_card"]["selected_count"] == 0
    assert next_run["tavern_daily_card"]["day"] == 20261101


def test_staff_requirements_duplicate_assignments_and_rename(setup_gameplay):
    c = setup_gameplay
    configure(c)
    before = snapshot(c)
    call(c, "api/tavern/set/characters", {"set_info": [{"m_tavern_work_id": 100, "m_character_id": 1200002}, {"m_tavern_work_id": 101, "m_character_id": 1200002}]}, expected=409)
    call(c, "api/tavern/set/character", {"m_tavern_work_id": 200, "position": 1, "m_character_id": 1200002}, expected=409)
    call(c, "api/tavern/set/character", {"m_tavern_work_id": 101, "position": 1, "m_character_id": 1200016}, expected=409)
    assert snapshot(c) == before
    seed(c, items={140001: 2})
    call(c, "api/character/gift/give", {"t_character_id": 1, "use_items": [{"m_item_id": 140001, "amount": 2}]})
    result = call(c, "api/tavern/set/character", {"m_tavern_work_id": 101, "position": 1, "m_character_id": 1200016})
    assert result["tavern_work"]["t_character_id_1"] == 1
    call(c, "api/tavern/update/name", {"name": "Local Inn"})
    assert call(c, "api/tavern")["tavern_shop"]["name"] == "Local Inn"
    assert any(r["m_character_id"] == 1200016 for r in call(c, "api/interaction/top")["t_tavern_characters"])


def test_character_card_can_only_be_used_once_per_turn(setup_gameplay):
    c = setup_gameplay
    identifier = configure(c)
    call(c, "api/tavern/exec/work", {"use_ticket": 0, "t_tavern_daily_card_id": identifier})
    result = call(c, "api/tavern/select/character-card", {"m_character_id": 1200002, "t_tavern_daily_card_id": identifier})
    assert result["tavern_daily_card"]["selected_cards"]["selected_character_cards"] == [{"turn": 1, "m_character_id": 1200002}]
    before = snapshot(c)
    call(c, "api/tavern/select/character-card", {"m_character_id": 1200002, "t_tavern_daily_card_id": identifier}, expected=409)
    assert snapshot(c) == before


def test_active_session_keeps_scoring_rules_when_configuration_changes(setup_gameplay):
    c = setup_gameplay
    identifier = configure(c)
    first = call(c, "api/tavern/exec/work", {"use_ticket": 0, "t_tavern_daily_card_id": identifier})
    (c.settings.data_dir / "gameplay-rules.json").write_text(json.dumps({"tavern_base_score": 10000}), encoding="utf-8")
    result = call(c, "api/tavern/select/card", {"t_tavern_daily_card_id": identifier, "m_tavern_card_id": first["tavern_cards"][0]["m_tavern_card_id"]})
    assert 0 < result["result_score"] < 10000
    active = json.loads(snapshot(c)[1])["features"]["tavern"]["data"]["active"]
    assert active["score_rules"]["tavern_base_score"] == 100
