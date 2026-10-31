import copy

import pytest

from dotabyss_ps.admin_actions_growth import edit_synchro
from dotabyss_ps.gameplay_catalog import GameplayError
from dotabyss_ps.gameplay_character_levels import effective_character_level
from dotabyss_ps.gameplay_content import create_character
from dotabyss_ps.gameplay_nether_party import base_stat, snapshot_party
from dotabyss_ps.gameplay_nether_state import run
from dotabyss_ps.gameplay_records import find, put, rows
from test_battle_rewards import reward_game, reward_master
from test_nether_growth import game_with_party
from test_nether_tickets_hidden import enter


def research(game, kind, level):
    return put(game.state, "t_building_skills", {"m_building_id": 1000, "skill_type": kind},
               {"skill_level": level, "checked_skill_level": level}, game.user_id)


def test_synchronization_changes_effective_stats_without_rewriting_training(reward_game):
    game = game_with_party(reward_game)
    character = rows(game.state, "t_characters")[0]
    character["level"] = 30
    edit_synchro(game, {"grade": 1, "level": 32})
    assert effective_character_level(game, character) == 30
    unlocked = research(game, 11001, 1)
    before = copy.deepcopy(character)
    snapshot = snapshot_party(game, 1)["characters"][0]
    master = game.catalog.get("m_characters", character["m_character_id"])
    assert snapshot["level"] == 32
    assert {key: snapshot[key] for key in ("hp", "attack", "defence")} == {
        key: base_stat(master[key], 32) for key in ("hp", "attack", "defence")}
    assert character == before
    character["level"] = 20
    assert effective_character_level(game, character) == 32
    edit_synchro(game, {"grade": 1, "level": 30})
    assert effective_character_level(game, character) == 30
    unlocked["skill_level"] = 0
    assert effective_character_level(game, character) == 20


def test_newly_obtained_character_uses_unlocked_synchronization(reward_game):
    game = game_with_party(reward_game)
    research(game, 11001, 1)
    edit_synchro(game, {"grade": 1, "level": 40})
    identifier = next(row["id"] for row in game.catalog.rows("m_characters")
                      if find(game.state, "t_characters", m_character_id=row["id"]) is None)
    create_character(game.state, identifier, game.catalog, game.user_id, game.now, game.progress)
    character = find(game.state, "t_characters", m_character_id=identifier)
    assert character["level"] == 1
    assert effective_character_level(game, character) == 40


def test_synchronized_levels_reach_missions_research_and_event_context(reward_game):
    from dotabyss_ps.gameplay_missions import Missions
    from dotabyss_ps.gameplay_research import requirements_met
    from dotabyss_ps.gameplay_event_battle_common import context

    game = game_with_party(reward_game)
    character = rows(game.state, "t_characters")[0]
    character["level"] = 1
    research(game, 11001, 1)
    edit_synchro(game, {"grade": 1, "level": 32})
    evaluator = Missions(game.state, game.progress, game.catalog, game.now, game.user_id)
    assert evaluator.value({"situation_type": 40102, "mission_type": 1, "target_id": 32}) == len(rows(game.state, "t_characters"))
    event = game.catalog.rows("m_events")[0]
    assert set(context(game, event, 1)["members"].values()) == {32}
    definition = game.catalog.rows("m_building_skill_levels")[0]
    restriction = {"id": 99999999, "m_building_skill_level_id": definition["id"], "restrict_type": 2, "restrict_param_1": 1}
    game.catalog.tables["m_building_skill_restricts"] = {restriction["id"]: restriction}
    game.catalog.tables["m_building_skill_costs"] = {}
    assert requirements_met(game, definition)
    research(game, 11001, 0)
    assert evaluator.value({"situation_type": 40102, "mission_type": 1, "target_id": 32}) == 0
    assert not requirements_met(game, definition)
    assert character["level"] == 1


def test_unlocked_synchronization_requires_its_level_record(reward_game):
    game = game_with_party(reward_game)
    research(game, 11001, 1)
    rows(game.state, "t_level_synchros").clear()
    with pytest.raises(GameplayError, match="requires one level record"):
        effective_character_level(game, rows(game.state, "t_characters")[0])


@pytest.mark.parametrize("level,expected_rank", [(0, 1), (1, 2), (3, 4), (8, 9)])
def test_nether_mana_rank_uses_cumulative_research(reward_game, level, expected_rank):
    game = game_with_party(reward_game)
    research(game, 10000, level)
    assert snapshot_party(game, 1)["mana_gem_rank"] == expected_rank


def test_existing_expedition_keeps_frozen_levels_and_mana_rank(reward_game):
    game = game_with_party(reward_game)
    research(game, 11001, 1)
    research(game, 10000, 1)
    edit_synchro(game, {"grade": 1, "level": 32})
    enter(game, 1)
    frozen = copy.deepcopy(run(game)["party"])
    research(game, 10000, 3)
    edit_synchro(game, {"grade": 1, "level": 40})
    game.prepare()
    assert run(game)["party"] == frozen
    assert frozen["mana_gem_rank"] == 2
    assert all(row["level"] == 32 for row in frozen["characters"])
    next_party = snapshot_party(game, 1)
    assert next_party["mana_gem_rank"] == 4
    assert all(row["level"] == 40 for row in next_party["characters"])
