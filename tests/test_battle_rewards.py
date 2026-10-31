import copy
import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import msgpack
import pytest

from dotabyss_ps.gameplay_battle_rewards import attach_drops, complete_quest, extra_drop_probability, first_rewards, grant_display_rewards, settle_battle, validate_stage_results
from dotabyss_ps.gameplay_battle_stages import build_stage
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError, JST
from dotabyss_ps.gameplay_content import create_equipment
from dotabyss_ps.gameplay_presents import presents
from dotabyss_ps.gameplay_records import put, rows
from dotabyss_ps.gameplay_rules import load_rules
from dotabyss_ps.newgame import build_initial_state


@pytest.fixture(scope="module")
def reward_master():
    root = Path(__file__).resolve().parents[1]
    schema = json.loads((root / "artifacts/master-schema-pc-1.15.0.json").read_text(encoding="utf-8"))
    blob = root / "data-pc-1.15.0/master/blobs/ee9f68157917ee9601c539f950aaf98ae9b446a54dac66707bc297746efac523.msgpack"
    tables = msgpack.unpackb(blob.read_bytes(), raw=False)
    return tables, schema, GameplayCatalog(tables, schema)


@pytest.fixture
def reward_game(reward_master):
    tables, schema, original = reward_master
    catalog = copy.copy(original)
    catalog.tables = dict(original.tables)
    catalog.tables["m_chapter_quest_battles"] = dict(original.tables["m_chapter_quest_battles"])
    catalog.tables["m_chapter_quest_battles"][1001] = {
        **original.tables["m_chapter_quest_battles"][1001], "addition_extra_drop_probability": 0}
    catalog.tables["m_chapter_quest_battle_view_drops"] = {
        1: {"id": 1, "m_chapter_quest_battle_id": 1001, "content_type": 30, "content_id": 50007, "is_rare_drop": 0}}
    state = build_initial_state(tables, schema, 7, "Battle test", "2026-10-31 10:00:00")
    progress = {"features": {}}
    rules = {**load_rules(), "battle_drop_probability_per_mille": 1000, "battle_rare_drop_probability_per_mille": 0,
             "battle_player_exp_per_stamina": 1, "battle_first_reward_group_mode": 2}
    return SimpleNamespace(state=state, progress=progress, catalog=catalog, rules=rules, user_id=7,
                           now=datetime(2026, 10, 31, 19, tzinfo=JST))


def dungeon_stage():
    return {"floor_parts": [{"sid": 11, "fork_group_no": 0, "destroy_enemy_max": 0,
                              "enemy_groups": [{"enemies": [101, 102]}], "resources": [201]}],
            "enemies": [{"sid": sid, "m_enemy_id": 110110101, "drop_exp": 5,
                         "is_target": 0, "is_respawn": 0, "drops": []} for sid in (101, 102)],
            "resources": [{"sid": 201, "drops": []}], "drops": []}


def session(game, *, sequence=2, identity=1):
    stage = dungeon_stage()
    index = attach_drops(game, 1001, stage, identity)
    return {"id": identity, "kind": "main", "m_chapter_quest_id": 1010101, "battle_id": 1001,
            "stage_type": 1, "stage_detail": stage, "reward_index": index, "party_no": 1,
            "sequence": sequence, "stamina_spent": 40}


def clear_result(data):
    return {"play_time": 78, "destroyed_enemies": [101, 102], "drop_items": list(map(int, data["reward_index"])),
            "mana_gem_count": 2, "top_fc_damage": 932, "total_damage": 0, "passed_floor_parts": [11], "clear_rank": 0}


def test_drop_manifest_is_deterministic_and_bound_to_generated_source_ids(reward_game):
    game = reward_game
    first, second = dungeon_stage(), dungeon_stage()
    index = attach_drops(game, 1001, first, 123)
    assert attach_drops(game, 1001, second, 123) == index
    assert first == second
    assert set(index) == {"202", "203", "204"}
    for row in first["enemies"] + first["resources"]:
        assert len(row["drops"]) == 1
        assert index[str(row["drops"][0])]["source_sid"] == row["sid"]
    with pytest.raises(GameplayError, match="already been attached"):
        attach_drops(game, 1001, first, 123)


def test_disabled_drops_preserve_empty_source_contract(reward_game):
    reward_game.rules["battle_drop_probability_per_mille"] = 0
    stage = dungeon_stage()
    assert attach_drops(reward_game, 1001, stage, 123) == {}
    assert stage["drops"] == []
    assert all(row["drops"] == [] for row in stage["enemies"] + stage["resources"])


def test_bonus_drops_are_frozen_at_start_and_keep_native_bonus_type(reward_game):
    game = reward_game
    game.catalog.tables["m_chapter_quest_battles"][1001]["addition_extra_drop_probability"] = 1000
    data = session(game)
    assert len(data["reward_index"]) == 6
    assert sum(row["bonus_type"] == 1 for row in data["reward_index"].values()) == 3
    game.catalog.tables["m_chapter_quest_battles"][1001]["addition_extra_drop_probability"] = 0
    result = settle_battle(game, data, clear_result(data))
    assert len(result["battle_result"]["drops"]) == 6
    assert sum(row["bonus_type"] == 1 for row in result["battle_result"]["drops"]) == 3


def test_research_vip_and_active_subscription_add_to_bonus_probability(reward_game):
    game = reward_game
    level = game.catalog.get("m_building_skill_levels", 100003)
    put(game.state, "t_building_skills", {"m_building_id": level["m_building_id"], "skill_type": level["skill_type"]},
        {"skill_level": level["skill_level"], "checked_skill_level": 0}, game.user_id)
    assert extra_drop_probability(game, 1001) == 10
    vip = next(row for row in game.catalog.rows("m_vip_ranks") if row["rank"] == 6)
    put(game.state, "t_vip_ranks", {}, {"total_point": vip["total_point"]}, game.user_id)
    assert extra_drop_probability(game, 1001) == 40
    subscription = next(row for row in game.catalog.rows("m_payment_subscriptions")
                        if row["m_payment_subscription_effect_group_id"] == 1001)
    benefit = put(game.state, "t_payment_subscription_benefits", {"m_payment_subscription_id": subscription["id"]},
                  {"expire_at": (game.now + timedelta(days=1)).isoformat()}, game.user_id)
    assert extra_drop_probability(game, 1001) == 70
    benefit["expire_at"] = game.now.isoformat()
    assert extra_drop_probability(game, 1001) == 40
    game.catalog.tables["m_chapter_quest_battles"][1001]["addition_extra_drop_probability"] = 990
    assert extra_drop_probability(game, 1001) == 1000


@pytest.mark.parametrize("field", ["top_fc_damage", "total_damage"])
def test_native_damage_values_are_signed_32_bit_integers(reward_game, field):
    data = session(reward_game)
    result = {**clear_result(data), field: 2 ** 31 - 1}
    assert validate_stage_results(data["stage_detail"], result, 1)[field] == 2 ** 31 - 1
    with pytest.raises(GameplayError, match="integer"):
        validate_stage_results(data["stage_detail"], {**result, field: 2 ** 31}, 1)


@pytest.mark.parametrize("field,value", [
    ("destroyed_enemies", [101, 999]), ("destroyed_enemies", [101, 101]),
    ("drop_items", [999]), ("passed_floor_parts", [999]),
    ("passed_floor_parts", []), ("play_time", True), ("play_time", 500), ("clear_rank", 99)])
def test_foreign_duplicate_and_malformed_results_do_not_mutate_rewards(reward_game, field, value):
    data = session(reward_game)
    result = clear_result(data)
    result[field] = value
    before = copy.deepcopy((reward_game.state, reward_game.progress))
    with pytest.raises(GameplayError):
        settle_battle(reward_game, data, result)
    assert (reward_game.state, reward_game.progress) == before


def test_undefeated_source_cannot_supply_a_drop(reward_game):
    data = session(reward_game)
    stage = data["stage_detail"]
    stage["enemies"][0]["is_target"] = 1
    result = clear_result(data)
    result["destroyed_enemies"] = [101]
    with pytest.raises(GameplayError, match="drop source"):
        settle_battle(reward_game, data, result)


def test_resource_only_floor_accepts_visited_drops_without_invented_enemy_targets(reward_game):
    stage = dungeon_stage()
    stage["enemies"] = []
    stage["floor_parts"][0]["enemy_groups"] = []
    index = attach_drops(reward_game, 1001, stage, 123)
    result = {"play_time": 12, "destroyed_enemies": [], "drop_items": list(map(int, index)),
              "mana_gem_count": 0, "top_fc_damage": 0, "total_damage": 0,
              "passed_floor_parts": [11], "clear_rank": 0}
    assert validate_stage_results(stage, result, 1) == result
    stage["floor_parts"].append({"sid": 12, "fork_group_no": 0, "destroy_enemy_max": 0,
                                 "enemy_groups": [], "resources": []})
    with pytest.raises(GameplayError, match="drop source"):
        validate_stage_results(stage, {**result, "passed_floor_parts": [12]}, 1)


def test_disaster_requires_boss_and_does_not_accept_dungeon_fields():
    boss = {"sid": 999, "m_enemy_id": 1, "drop_exp": 0, "is_target": 1, "drops": []}
    stage = {"enemies": [], "boss_enemy": boss, "drops": []}
    result = {"play_time": 1, "destroyed_enemies": [999], "drop_items": [], "mana_gem_count": 0, "top_fc_damage": 1}
    assert validate_stage_results(stage, result, 2) == result
    with pytest.raises(GameplayError, match="boss"):
        validate_stage_results(stage, {**result, "destroyed_enemies": []}, 2)
    with pytest.raises(GameplayError, match="fields"):
        validate_stage_results(stage, {**result, "clear_rank": 0}, 2)


def test_unlimited_stage_uses_a_bounded_session_limit(reward_game):
    data = session(reward_game)
    data["time_limit"] = 0
    result = {**clear_result(data), "play_time": 1200}
    assert settle_battle(reward_game, data, result)["battle_result"]["first_rewards"]
    with pytest.raises(GameplayError, match="time limit"):
        validate_stage_results(data["stage_detail"], {**result, "play_time": 86401}, 1, time_limit=0)


@pytest.mark.parametrize("mode,group", [(0, 0), (1, 1), (2, 2)])
def test_first_clear_group_policy_is_configurable_and_receipted(reward_game, mode, group):
    game = reward_game
    game.rules["battle_first_reward_group_mode"] = mode
    data = {"kind": "main", "m_chapter_quest_id": 1010102, "sequence": 1}
    displayed = first_rewards(game, data)
    assert bool(displayed) is bool(mode)
    receipt = game.progress["features"]["battle_rewards"]["data"]["first_claims"]["1010102:1"]
    assert receipt["mode"] == mode and receipt["group_no"] == group
    before = copy.deepcopy(game.state)
    game.rules["battle_first_reward_group_mode"] = 2
    assert first_rewards(game, data) == []
    assert game.state == before


@pytest.mark.parametrize("quest_id", [1010301, 1030402, 1040501, 1050501, 1060302, 1070504])
def test_multiple_battle_nodes_have_independent_first_clear_receipts(reward_game, quest_id):
    game = reward_game
    for sequence in (2, 4):
        data = {"kind": "main", "m_chapter_quest_id": quest_id, "sequence": sequence}
        assert first_rewards(game, data)
        assert first_rewards(game, data) == []
    claims = game.progress["features"]["battle_rewards"]["data"]["first_claims"]
    assert set(claims) == {f"{quest_id}:2", f"{quest_id}:4"}


def test_settlement_grants_server_rewards_once_with_native_result_fields(reward_game):
    game = reward_game
    data = session(game)
    result = clear_result(data)
    status = rows(game.state, "t_statuses")[0]
    before_exp, before_player, before_money = status["character_exp"], status["player_exp"], status["free_virtual_money"]
    response = settle_battle(game, data, result)
    assert response["last_party_no"] == 1
    assert response["battle_result"]["grant_character_exp"] == 10
    assert response["battle_result"]["before_character_exp"] == before_exp
    assert response["battle_result"]["after_character_exp"] == before_exp + 10
    assert len(response["battle_result"]["drops"]) == 3
    assert response["battle_result"]["first_rewards"][0]["amount"] == 100
    assert status["free_virtual_money"] == before_money + 100
    assert status["player_exp"] == before_player + 40
    assert game.progress["features"]["statistics"]["data"]["totals"]["140000:0:0"] == 2
    saved = copy.deepcopy((game.state, game.progress))
    assert settle_battle(game, data, result) == response
    assert (game.state, game.progress) == saved
    with pytest.raises(GameplayError, match="already settled"):
        settle_battle(game, data, {**result, "play_time": 79})
    assert (game.state, game.progress) == saved
    other = session(game, identity=2)
    assert settle_battle(game, other, clear_result(other))["battle_result"]["first_rewards"] == []


def test_existing_completed_progress_does_not_regrant_first_or_quest_rewards(reward_game):
    game = reward_game
    game.progress["features"]["main_story"] = {"version": 1, "data": {
        "legacy_cleared_battles": ["1010101:2"], "legacy_completed_quests": [1010101]}}
    data = session(game)
    assert settle_battle(game, data, clear_result(data))["battle_result"]["first_rewards"] == []
    status = copy.deepcopy(rows(game.state, "t_statuses"))
    assert complete_quest(game, game.catalog.get("m_chapter_quests", 1010101)) == []
    assert rows(game.state, "t_statuses") == status


def test_whole_quest_rewards_are_separate_and_idempotent(reward_game):
    game = reward_game
    quest = game.catalog.get("m_chapter_quests", 1010101)
    status = rows(game.state, "t_statuses")[0]
    old = dict(status)
    received = complete_quest(game, quest)
    assert status["gold"] == old["gold"] + 4000
    assert status["character_exp"] == old["character_exp"] + 5000
    assert status["free_virtual_money"] == old["free_virtual_money"] + 100
    after = copy.deepcopy(game.state)
    assert complete_quest(game, quest) == received
    assert game.state == after


def test_equipment_rewards_include_allocated_ids_and_overflow_mail_is_clean(reward_game):
    game = reward_game
    for _ in range(99):
        create_equipment(game.state, 70, 11010110, game.catalog, game.user_id, game.progress)
    rewards = [{"content_type": 70, "content_id": 11010110, "amount": 2, "sid": 12345,
                "source_type": "enemy", "is_rare_drop": 1}]
    displayed = grant_display_rewards(game, rewards)
    assert len(displayed) == 2
    assert displayed[0]["additional_info"]["t_weapon_id"] == rows(game.state, "t_weapons")[-1]["id"]
    assert displayed[1]["additional_info"] is None
    assert len(rows(game.state, "t_weapons")) == 100
    overflow = [entry for entry in presents(game) if entry["title"] == "Inventory overflow"]
    assert len(overflow) == 1 and overflow[0]["amount"] == 1
    assert "sid" not in overflow[0] and "source_type" not in overflow[0]


@pytest.mark.parametrize("stage_type", [1, 2])
def test_native_master_stage_identifiers_roundtrip_to_result_validation(reward_game, stage_type):
    game = reward_game
    battle = next(row for row in game.catalog.rows("m_chapter_quest_battles") if row["stage_type"] == stage_type)
    stage = build_stage(game, stage_type, battle["stage_id"], 123)
    index = attach_drops(game, battle["id"], stage, 123)
    enemies = [row["sid"] for row in stage["enemies"]]
    if stage.get("boss_enemy"):
        enemies.append(stage["boss_enemy"]["sid"])
    result = {"play_time": 78, "destroyed_enemies": enemies, "drop_items": list(map(int, index)),
              "mana_gem_count": 0, "top_fc_damage": 0}
    if stage_type == 1:
        result.update(total_damage=0, passed_floor_parts=[row["sid"] for row in stage["floor_parts"]], clear_rank=0)
    assert validate_stage_results(stage, result, stage_type) == result
    other = build_stage(game, stage_type, battle["stage_id"], 124)
    attach_drops(game, battle["id"], other, 124)
    with pytest.raises(GameplayError, match="outside this session"):
        validate_stage_results(other, result, stage_type)
