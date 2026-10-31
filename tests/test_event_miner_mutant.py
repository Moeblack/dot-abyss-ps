import copy
import json
from datetime import timedelta

import pytest

from dotabyss_ps.gameplay import EXTRA_ROUTES, Gameplay
from dotabyss_ps.gameplay_battle_rewards import attach_drops, validate_stage_results
from dotabyss_ps.gameplay_battle_session import battle_state
from dotabyss_ps.gameplay_battle_stages import build_stage
from dotabyss_ps.gameplay_catalog import GameplayError
from dotabyss_ps.gameplay_event_miner import MINER_ROUTES
from dotabyss_ps.gameplay_event_mutant import MUTANT_ROUTES, phases
from dotabyss_ps.gameplay_events import event_data, event_supply
from dotabyss_ps.gameplay_main_repeat import skipped_result
from dotabyss_ps.gameplay_mining_stage import skipped_mining_result, validate_mining_path
from dotabyss_ps.gameplay_records import find, put, rows
from dotabyss_ps.gameplay_presents import presents
from test_battle_rewards import reward_game, reward_master
from test_gameplay import party_body
from test_nether_growth import game_with_party


@pytest.fixture
def event_game(reward_game, monkeypatch):
    for path, handler in {**MINER_ROUTES, **MUTANT_ROUTES}.items():
        monkeypatch.setitem(EXTRA_ROUTES, path, handler)
    game = game_with_party(reward_game)
    for identifier in (1001, 1002):
        put(game.state, "t_feature_locks", {"m_feature_lock_id": identifier}, {"is_unlocked": True})
    rows(game.state, "t_statuses")[0]["free_virtual_money"] = 10000
    return game


def call(game, endpoint, body=None, operation=""):
    return game.execute("api/" + endpoint, "GET" if body is None else "POST", body or {}, operation)


def join(game, identifier):
    call(game, "event/join", {"m_event_id": identifier})
    call(game, "event/miner/check-quest" if game.catalog.get("m_events", identifier)["type"] == 1 else "event/disaster/check-quest", {"m_event_id": identifier})


def miner_start(game, identifier=100001101):
    return call(game, "event/miner/start-quest", {"m_event_miner_quest_id": identifier, "party_no": 1})


def mutant_start(game, identifier=1101):
    return call(game, "event/disaster/start-quest", {"m_event_disaster_quest_id": identifier, "party_no": 1})


def mutant_victory(game):
    session = battle_state(game)["active"]
    hp, _ = phases(game, game.catalog.get("m_event_disaster_quests", session["quest_id"]))
    return {"stage_results": skipped_result(session["stage_detail"], 2), "battle_detail": [],
            "boss_damage": session["context"]["remain_hp"] + sum(hp[session["context"]["phase_no"]:])}


def test_all_mining_stages_build_real_trolley_and_exclusive_rank_routes(event_game):
    game = event_game
    for index, quest in enumerate(game.catalog.rows("m_event_miner_quests"), 1):
        stage = build_stage(game, 3, quest["stage_id"], index, kind="event_miner", quest_id=quest["id"])
        attach_drops(game, quest["id"], stage, index, kind="event_miner")
        assert stage["mining"]["trolley"]["hp"] > 0
        for rank in (1, 2, 3, 4):
            result = skipped_mining_result(stage, rank)
            validate_stage_results(stage, result, 3, time_limit=quest["time_limit"], result_schema="exploration")
        result = skipped_mining_result(stage, 4)
        unchosen = next(row["sid"] for row in stage["floor_parts"] if row["sid"] not in result["passed_floor_parts"])
        result["passed_floor_parts"].append(unchosen)
        with pytest.raises(GameplayError, match="one outcome|exclusive"):
            validate_mining_path(stage, result)


def test_miner_clear_skip_recovery_replay_and_event_separation(event_game):
    game = event_game
    join(game, 100001)
    join(game, 100002)
    response = miner_start(game)
    assert response["quest_type"] == 100 and response["stage_type"] == 3
    assert event_supply(game, 100001)["amount"] == 2
    before = copy.deepcopy([game.state, game.progress])
    assert miner_start(game)["stage_detail"] == response["stage_detail"]
    assert [game.state, game.progress] == before
    result = skipped_mining_result(battle_state(game)["active"]["stage_detail"], 4)
    result["play_time"] = 12
    won = call(game, "event/miner/clear-quest", {"stage_results": result})
    assert won["first_rewards"]
    before = copy.deepcopy([game.state, game.progress])
    call(game, "event/miner/clear-quest", {"stage_results": result})
    assert [game.state, game.progress] == before
    skipped = call(game, "event/miner/skip-quest", {"m_event_miner_quest_id": 100001101, "skip_count": 1}, "skip-once")
    before = copy.deepcopy([game.state, game.progress])
    call(game, "event/miner/skip-quest", {"m_event_miner_quest_id": 100001101, "skip_count": 1}, "skip-once")
    assert [game.state, game.progress] == before and skipped["first_rewards"] == []
    assert find(game.state, "t_event_miner_quests", m_event_miner_quest_id=100001101)["clear_time"] == 12
    assert event_supply(game, 100002)["amount"] == 3
    cash = rows(game.state, "t_statuses")[0]["free_virtual_money"]
    call(game, "event/miner/recovery", {"m_event_id": 100001, "recovery_count": 2}, "recover-once")
    assert event_supply(game, 100001)["amount"] == 3
    assert rows(game.state, "t_statuses")[0]["free_virtual_money"] == cash - 100
    game.now += timedelta(days=1)
    call(game, "event/miner/check-quest", {"m_event_id": 100001})
    assert find(game.state, "t_event_timed_parameters", m_event_id=100001, type=2)["amount"] == 3


def test_miner_rejects_wrong_rank_and_changed_event_epoch(event_game):
    game = event_game
    join(game, 100001)
    miner_start(game)
    result = skipped_mining_result(battle_state(game)["active"]["stage_detail"], 4)
    result["clear_rank"] = 1
    with pytest.raises(GameplayError, match="rank"):
        call(game, "event/miner/clear-quest", {"stage_results": result})
    event_data(game, 100001)["epoch"] += 1
    with pytest.raises(GameplayError, match="another event period"):
        call(game, "event/miner/close-quest", {"play_time": 1})
    assert battle_state(game)["active"] is not None


def test_mutant_partial_hp_party_lock_second_attempt_and_first_reward_once(event_game):
    game = event_game
    join(game, 1050001)
    party = party_body()
    party["characters"] = party["characters"][:1]
    call(game, "party/exploration/edit", party)
    started = mutant_start(game, 101)
    assert started["quest_type"] == 102
    session = battle_state(game)["active"]
    hp = session["context"]["remain_hp"]
    member = int(next(iter(session["context"]["members"])))
    body = {"play_time": 15, "phase_no": 1, "remain_hp": hp - 1000, "boss_damage": 1000,
            "battle_detail": [{"m_character_id": member, "level": 1, "damage": 1000}]}
    assert call(game, "event/disaster/close-quest", body)["status"] == 0
    before = copy.deepcopy([game.state, game.progress])
    call(game, "event/disaster/close-quest", body)
    assert [game.state, game.progress] == before
    with pytest.raises(GameplayError, match="already fought"):
        mutant_start(game, 101)
    party["characters"][0]["t_character_id"] = 2
    call(game, "party/exploration/edit", party)
    mutant_start(game, 101)
    assert battle_state(game)["active"]["context"]["remain_hp"] == hp - 1000
    won = call(game, "event/disaster/clear-quest", mutant_victory(game))
    assert won["first_rewards"]
    row = find(game.state, "t_event_disaster_quests", m_event_disaster_quest_id=101)
    assert row["is_cleared"] == 1 and row["challenge_count"] == 2 and row["remain_hp"] == 0
    call(game, "event/disaster/reset-quest", {"m_event_disaster_quest_id": 101})
    assert row["phase_no"] == 1 and row["remain_hp"] == hp and row["used_character_list"] == []
    mutant_start(game, 101)
    assert call(game, "event/disaster/clear-quest", mutant_victory(game))["first_rewards"] == []


def test_mutant_rejects_unearned_phase_and_foreign_damage_detail(event_game):
    game = event_game
    join(game, 1050003)
    mutant_start(game)
    row = find(game.state, "t_event_disaster_quests", m_event_disaster_quest_id=1101)
    before = copy.deepcopy(row)
    with pytest.raises(GameplayError, match="progress exceeds"):
        call(game, "event/disaster/close-quest", {"play_time": 1, "phase_no": 2, "remain_hp": row["remain_hp"], "boss_damage": 0, "battle_detail": []})
    assert row == before
    body = mutant_victory(game)
    body["battle_detail"] = [{"m_character_id": 999999, "level": 1, "damage": 1}]
    with pytest.raises(GameplayError, match="frozen party"):
        call(game, "event/disaster/clear-quest", body)
    assert row == before


def test_mutant_cross_phase_snapshot_restores_and_force_suspend_locks_members(event_game):
    game = event_game
    join(game, 1050003)
    mutant_start(game)
    hp, _ = phases(game, game.catalog.get("m_event_disaster_quests", 1101))
    call(game, "event/disaster/close-quest", {"play_time": 30, "phase_no": 2, "remain_hp": hp[1] - 10,
                                            "boss_damage": hp[0] + 10, "battle_detail": []})
    state, progress = json.loads(json.dumps([game.state, game.progress]))
    restored = Gameplay(state, progress, game.catalog, game.user_id, game.now, game.rules)
    mutant_start(restored)
    session = battle_state(restored)["active"]
    assert session["context"]["phase_no"] == 2 and session["context"]["remain_hp"] == hp[1] - 10
    call(restored, "battle-session/suspend", {"is_force": 1})
    assert battle_state(restored)["active"] is None
    record = find(restored.state, "t_event_disaster_quests", m_event_disaster_quest_id=1101)
    assert set(record["used_character_list"]) == set(map(int, session["context"]["members"]))


def test_mutant_all_master_phases_build_and_ranking_is_local(event_game):
    game = event_game
    for event_id in (1050001, 1050003):
        join(game, event_id)
    for index, definition in enumerate(game.catalog.rows("m_event_disaster_quests"), 1):
        stage = build_stage(game, 2, definition["stage_id"], index, kind="event_mutant", quest_id=definition["id"], defensive_type=3)
        hp, infinite = phases(game, definition)
        assert stage["boss_enemy"] and hp and sum(hp) > 0
    row = find(game.state, "t_event_disaster_quests", m_event_disaster_quest_id=1201)
    row["status"] = 2
    mutant_start(game, 1201)
    call(game, "event/disaster/clear-quest", mutant_victory(game))
    before_mail = len(presents(game))
    game.rules["event_mutant_ranking_reward_on_query"] = 0
    ranking = call(game, "event/disaster/ranking/1050003")
    assert len(presents(game)) == before_mail
    assert ranking["own"]["score"] > 0 and ranking["own"]["rank"] == 1
    assert len(ranking["ranking"]) == 1 and ranking["ranking"][0]["user_id"] == game.user_id
    game.rules["event_mutant_ranking_reward_on_query"] = 1
    call(game, "event/disaster/ranking/1050003")
    assert len(presents(game)) > before_mail
    after_mail = len(presents(game))
    call(game, "event/disaster/ranking/1050003")
    assert len(presents(game)) == after_mail
    assert call(game, "event/disaster/ranking/1050001")["own"]["score"] == 0
