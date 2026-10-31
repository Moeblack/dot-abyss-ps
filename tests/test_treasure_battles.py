import copy

import pytest

from dotabyss_ps.gameplay_battle_rewards import attach_drops, settle_battle, validate_stage_results
from dotabyss_ps.gameplay_battle_stages import build_stage
from dotabyss_ps.gameplay_catalog import GameplayError
from dotabyss_ps.gameplay_main_repeat import skipped_result
from test_battle_rewards import reward_game, reward_master


def treasure(game):
    definition = game.catalog.get("m_encounter_quests", 1010102)
    stage = build_stage(game, definition["stage_type"], definition["stage_id"], 51, kind="encounter", quest_id=definition["id"])
    index = attach_drops(game, definition["id"], stage, 51, kind="encounter")
    return stage, index


def ranks(stage):
    return next(floor["role_option"]["treasure_battle"]["ranks"] for floor in stage["floor_parts"] if floor["role"] == 303)


def test_native_treasure_three_ranks_have_disjoint_rewards(reward_game):
    stage, index = treasure(reward_game)
    values = ranks(stage)
    assert [(row["rank"], row["asset_id"], row["time_limit"]) for row in values] == [
        (3, "BoxGold", 120), (2, "BoxSilver", 180), (1, "BoxBronze", 0)]
    assert [len(row["drops"]) for row in values] == [3, 2, 1]
    selected = [sid for row in values for sid in row["drops"]]
    assert len(selected) == len(set(selected))
    assert all(index[str(sid)]["source_type"] == "treasure" for sid in selected)
    for row in values:
        assert all(index[str(sid)]["treasure_rank"] == row["rank"] for sid in row["drops"])


@pytest.mark.parametrize("grade", [1, 2, 3])
def test_only_the_selected_rank_can_be_claimed(reward_game, grade):
    stage, _ = treasure(reward_game)
    values = ranks(stage)
    all_treasure = {sid for row in values for sid in row["drops"]}
    selected = next(row for row in values if row["rank"] == grade)
    result = skipped_result(stage, 1)
    result.update(clear_rank=grade, play_time=359)
    result["drop_items"] = [row["sid"] for row in stage["drops"] if row["sid"] not in all_treasure] + selected["drops"]
    assert validate_stage_results(stage, result, 1, time_limit=360)["clear_rank"] == grade
    other = next(row for row in values if row["rank"] != grade)
    result["drop_items"].append(other["drops"][0])
    with pytest.raises(GameplayError):
        validate_stage_results(stage, result, 1, time_limit=360)


def test_skip_selects_gold_and_does_not_collect_other_ranks(reward_game):
    stage, _ = treasure(reward_game)
    result = skipped_result(stage, 1)
    assert result["clear_rank"] == 3
    assert set(ranks(stage)[0]["drops"]) <= set(result["drop_items"])
    assert not set(ranks(stage)[1]["drops"] + ranks(stage)[2]["drops"]) & set(result["drop_items"])
    validate_stage_results(stage, result, 1, time_limit=360)


def test_treasure_result_replay_cannot_duplicate_rewards(reward_game):
    stage, index = treasure(reward_game)
    session = {"kind": "encounter", "battle_id": 1010102, "stage_type": 1, "stage_detail": stage,
               "reward_index": index, "party_no": 1, "stamina_spent": 0, "time_limit": 360}
    result = skipped_result(stage, 1)
    first = settle_battle(reward_game, session, result)
    state, progress = copy.deepcopy(reward_game.state), copy.deepcopy(reward_game.progress)
    assert settle_battle(reward_game, session, result) == first
    assert (reward_game.state, reward_game.progress) == (state, progress)


def test_treasure_quantities_can_be_disabled(reward_game):
    reward_game.rules.update(battle_drop_probability_per_mille=0, battle_treasure_b_drops=0,
                             battle_treasure_a_drops=0, battle_treasure_s_drops=0)
    stage, index = treasure(reward_game)
    assert index == {} and stage["drops"] == []
    assert all(row["drops"] == [] for row in ranks(stage))
    validate_stage_results(stage, skipped_result(stage, 1), 1)


def test_treasure_thresholds_cannot_be_reversed(reward_game):
    reward_game.rules.update(battle_treasure_s_seconds=200, battle_treasure_a_seconds=100)
    with pytest.raises(GameplayError):
        treasure(reward_game)
