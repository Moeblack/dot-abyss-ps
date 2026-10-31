import copy
import json
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from dotabyss_ps.gameplay_battle_stages import build_stage
from dotabyss_ps.gameplay_event_quest_state import QUEST_KINDS
from dotabyss_ps.gameplay_records import put
from dotabyss_ps.gameplay_rules import DEFAULT_RULES
from dotabyss_ps.gameplay_stamina import STAMINA_TYPE
from test_battle_stages import assert_links, battle_catalog, game_for
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_training_encounter_modes import active, clock, edit, prepare, records, table, victory


@pytest.fixture
def event_game(setup_gameplay, monkeypatch):
    monkeypatch.setitem(DEFAULT_RULES, "event_open_all", 1)
    context = setup_gameplay
    prepare(context, chapters=True)
    return context


def join(context, identifier):
    call(context, "api/event/join", {"m_event_id": identifier})
    call(context, "api/event/top", {"m_event_id": identifier})


def nodes(context, kind, event_id):
    return [row for row in table(context, kind.nodes) if row["m_event_id"] == event_id]


def state(context, kind, identifier):
    return next(row for row in records(context)[kind.records]["up"] if row[kind.field] == identifier)


def supply(context, identifier):
    return next(row for row in records(context)["t_event_timed_parameters"]["up"] if row["m_event_id"] == identifier)


def session_start(context, kind, identifier, party_no=1, **kwargs):
    return call(context, f"api/event/{kind.name}/start-quest", {kind.field: identifier, "party_no": party_no}, **kwargs)


def complete(context, kind, node, *, play_time=10, party_no=1):
    if kind.staged and node["type"] == 1:
        return call(context, f"api/event/{kind.name}/clear-story", {kind.field: node["id"]})
    response = session_start(context, kind, node["id"], party_no)
    expected = next(row["id"] for row in table(context, kind.quests) if row[kind.field] == node["id"]) if kind.staged else node["id"]
    assert response["quest_id"] == expected
    result = victory(context)
    result["stage_results"]["play_time"] = play_time
    return call(context, f"api/event/{kind.name}/clear-quest", result)


def test_story_event_full_campaign_preconditions_rewards_and_skip(event_game):
    context, kind, event_id = event_game, QUEST_KINDS["story"], 300001
    join(context, event_id)
    all_nodes = nodes(context, kind, event_id)
    assert len(all_nodes) == 24
    pending = {row["id"]: row for row in all_nodes}
    battle_count, novel_count = 0, 0
    while pending:
        accessible = [row for row in pending.values() if state(context, kind, row["id"])["status"] in (1, 2)]
        assert accessible
        for node in accessible:
            before = snapshot(context)
            result = complete(context, kind, node)
            assert state(context, kind, node["id"])["status"] == 99
            if node["type"] == 1:
                novel_count += 1
                assert result["receive_content_list"]
            else:
                battle_count += 1
                assert result["first_rewards"]
            assert snapshot(context) != before
            del pending[node["id"]]
    assert (novel_count, battle_count) == (8, 16)
    quest = next(row for row in all_nodes if row["type"] == 2)
    assert not call(context, "api/event/story/skip-quest", {kind.field: quest["id"], "skip_count": 1})["first_rewards"]
    challenge = next(row for row in all_nodes if row["type"] == 3)
    call(context, "api/event/story/skip-quest", {kind.field: challenge["id"], "skip_count": 1}, expected=409)


@pytest.mark.parametrize("kind_name,event_id", [("story", 300001), ("training", 900001)])
@pytest.mark.parametrize("read_first", [True, False])
def test_event_story_recollection_shares_reward_claims_in_both_directions(event_game, kind_name, event_id, read_first):
    context, kind = event_game, QUEST_KINDS[kind_name]
    join(context, event_id)
    node = next(row for row in nodes(context, kind, event_id) if row["type"] == 1)
    novel = next(row for row in table(context, f"m_event_{kind_name}_novels") if row[kind.field] == node["id"])
    read_body = {"novel_type": 2, "m_novel_id": novel["m_novel_event_id"]}
    first = call(context, "api/novel/read", read_body) if read_first else complete(context, kind, node)
    assert first["read_rewards" if read_first else "receive_content_list"]
    before = records(context)["t_statuses"]["up"][0]["free_virtual_money"]
    assert not complete(context, kind, node)["receive_content_list"]
    assert state(context, kind, node["id"])["status"] == 99
    assert not call(context, "api/novel/read", read_body)["read_rewards"]
    assert records(context)["t_statuses"]["up"][0]["free_virtual_money"] == before
    join(context, event_id)
    assert not complete(context, kind, node)["receive_content_list"]
    assert records(context)["t_statuses"]["up"][0]["free_virtual_money"] == before


def test_training_event_daily_attempts_free_fixed_tryout_and_recovery(event_game, monkeypatch):
    context, kind, event_id = event_game, QUEST_KINDS["training"], 900003
    join(context, event_id)
    assert supply(context, event_id)["amount"] == 3
    all_nodes = nodes(context, kind, event_id)
    trial = next(row for row in all_nodes if row["type"] == 3)
    result = complete(context, kind, trial, party_no=0)
    assert result["first_rewards"] and supply(context, event_id)["amount"] == 3
    call(context, "api/event/training/skip-quest", {kind.field: trial["id"], "skip_count": 1}, expected=409)
    novel = next(row for row in all_nodes if row["type"] == 1)
    complete(context, kind, novel)
    quest = min((row for row in all_nodes if row["type"] == 2), key=lambda row: row["id"])
    result = complete(context, kind, quest)
    assert result["first_rewards"] and result["clear_rewards"]
    assert supply(context, event_id)["amount"] == 2
    body = {kind.field: quest["id"], "skip_count": 2}
    headers = {**context.headers, "X-Request-Id": "training-double-skip"}
    skipped = call(context, "api/event/training/skip-quest", body, headers=headers)
    assert skipped["clear_rewards"] and not skipped["first_rewards"]
    assert supply(context, event_id)["amount"] == 0
    before = snapshot(context)
    call(context, "api/event/training/skip-quest", body, headers=headers)
    assert snapshot(context) == before
    session_start(context, kind, quest["id"], expected=409)
    clock(monkeypatch, NOW + timedelta(days=1))
    session_start(context, kind, quest["id"])
    assert supply(context, event_id)["amount"] == 2


def test_hunt_event_ticket_cost_and_novel_release(event_game):
    context, kind, event_id = event_game, QUEST_KINDS["hunt"], 500001
    join(context, event_id)
    all_nodes = nodes(context, kind, event_id)
    ordinary = min((row for row in all_nodes if row["type"] == 2), key=lambda row: row["id"])
    first = complete(context, kind, ordinary)
    assert any(row["content_type"] == 56 and row["amount"] == 10 for row in first["clear_rewards"])
    assert 50000102 in first["release_novel_event_ids"]
    boss = min((row for row in all_nodes if row["type"] == 3), key=lambda row: row["id"])
    session_start(context, kind, boss["id"], expected=409)
    for _ in range(3):
        complete(context, kind, ordinary)
    ticket = lambda: next(row["amount"] for row in records(context)["t_event_boss_tickets"]["up"] if row["m_event_boss_ticket_id"] == 1)
    assert ticket() == 40
    started = session_start(context, kind, boss["id"])
    assert started["quest_type"] == 103 and ticket() == 0
    session_start(context, kind, boss["id"])
    assert ticket() == 0
    call(context, "api/event/hunt/surrender-quest", {"play_time": 1})
    assert state(context, kind, boss["id"])["status"] != 99


@pytest.mark.parametrize("kind_name,event_id,quest_type", [("commission", 800001, 104), ("speedrun", 1100001, 107)])
def test_event_free_modes_first_rewards_only_and_fastest_time(event_game, kind_name, event_id, quest_type):
    context, kind = event_game, QUEST_KINDS[kind_name]
    join(context, event_id)
    quest = min(nodes(context, kind, event_id), key=lambda row: row["id"])
    started = session_start(context, kind, quest["id"])
    assert started["quest_type"] == quest_type
    assert call(context, "api/battle-session/status")["stage_detail"] == started["stage_detail"]
    result = victory(context)
    result["stage_results"]["play_time"] = 25
    won = call(context, f"api/event/{kind.name}/clear-quest", result)
    assert won["first_rewards"] and not won["clear_rewards"]
    assert state(context, kind, quest["id"])["clear_time"] == 25
    assert not complete(context, kind, quest, play_time=40)["first_rewards"]
    assert state(context, kind, quest["id"])["clear_time"] == 25
    complete(context, kind, quest, play_time=15)
    assert state(context, kind, quest["id"])["clear_time"] == 15


def test_event_battle_resume_wrong_mode_and_forged_result_rollback(event_game):
    context, kind = event_game, QUEST_KINDS["speedrun"]
    join(context, 1100001)
    node = nodes(context, kind, 1100001)[0]
    started = session_start(context, kind, node["id"])
    with TestClient(create_app(context.settings)) as client:
        resumed = client.get("/api/battle-session/status", headers=context.headers).json()["contents"]
        assert resumed["stage_detail"] == started["stage_detail"] and resumed["quest_id"] == node["id"]
    result = victory(context)
    forged = copy.deepcopy(result)
    forged["stage_results"]["drop_items"].append(999999999999)
    before = snapshot(context)
    call(context, "api/event/speedrun/clear-quest", forged, expected=400)
    call(context, "api/event/commission/close-quest", {"play_time": 0}, expected=409)
    assert snapshot(context) == before
    won = call(context, "api/event/speedrun/clear-quest", result)
    before = snapshot(context)
    assert call(context, "api/event/speedrun/clear-quest", result)["first_rewards"] == won["first_rewards"]
    assert snapshot(context) == before


def test_event_lock_join_and_request_contracts(event_game):
    context = event_game
    session_start(context, QUEST_KINDS["speedrun"], 1100001101, expected=409)
    join(context, 1100001)
    session_start(context, QUEST_KINDS["speedrun"], 1100001102, expected=409)
    call(context, "api/event/speedrun/check-quest", {"m_event_id": 1100001})
    assert state(context, QUEST_KINDS["speedrun"], 1100001101)["status"] == 2
    call(context, "api/event/story/check-quest", {"m_event_id": 1100001}, expected=400)
    call(context, "api/event/speedrun/start-quest", {"m_event_speedrun_quest_id": 1100001101, "party_no": 1, "extra": 1}, expected=400)


def test_event_character_currency_bonus_uses_departing_party(event_game):
    context, kind = event_game, QUEST_KINDS["story"]
    join(context, 300001)
    complete(context, kind, next(row for row in nodes(context, kind, 300001) if row["id"] == 2))
    session_start(context, kind, 6)
    result = victory(context)
    edit(context, lambda state, _: state["records"]["t_exploration_party_characters"].update(up=[]))
    won = call(context, "api/event/story/clear-quest", result)
    repeated = next(row for row in won["clear_rewards"] if row["content_type"] == 54 and row["content_id"] == 3000)
    assert repeated["amount"] == 69
    assert state(context, kind, 6)["skip_additional_drop_rate"] == 150
    skipped = call(context, "api/event/story/skip-quest", {kind.field: 6, "skip_count": 1})
    assert next(row for row in skipped["clear_rewards"] if row["content_type"] == 54)["amount"] == 69


def test_all_five_event_modes_build_every_actual_battle(battle_catalog):
    game = game_for(battle_catalog)
    count = 0
    for kind in QUEST_KINDS.values():
        for definition in battle_catalog.rows(kind.quests):
            count += 1
            generated = build_stage(game, definition["stage_type"], definition["stage_id"], count,
                                    kind=f"event_{kind.name}", quest_id=definition["id"])
            assert_links(generated)
            assert generated["enemies"]
    assert count == 221
