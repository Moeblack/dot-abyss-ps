import copy
import json
from types import SimpleNamespace

import pytest

from dotabyss_ps.gameplay_battle_stages import build_stage
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError
from dotabyss_ps.gameplay_rules import DEFAULT_RULES
from test_gameplay import gameplay_master


@pytest.fixture(scope="module")
def battle_catalog(gameplay_master):
    schema, tables = gameplay_master
    return GameplayCatalog(tables, schema)


def game_for(catalog):
    return SimpleNamespace(catalog=catalog, rules=copy.deepcopy(DEFAULT_RULES), state={"records": {
        name: {"up": [], "down": []} for name in (
            "t_building_skills", "t_defensive_units", "t_exploration_parties",
            "t_disaster_parties", "t_disaster_party_defensive_units",
        )
    }})


def entities(stage):
    result = list(stage["enemies"]) + stage.get("resources", []) + stage.get("floor_parts", []) + stage.get("defensive_units", [])
    if "boss_enemy" in stage:
        result.append(stage["boss_enemy"])
    return result


def assert_links(stage):
    all_entities = entities(stage)
    identifiers = [row["sid"] for row in all_entities]
    assert len(identifiers) == len(set(identifiers))
    assert all(type(value) is int and 0 < value < 2 ** 53 for value in identifiers)
    enemies = {row["sid"]: row for row in stage["enemies"]}
    resources = {row["sid"]: row for row in stage.get("resources", [])}
    seen_enemies, seen_resources = [], []
    for floor in stage.get("floor_parts", []):
        referenced = [sid for group in floor["enemy_groups"] for sid in group["enemies"]]
        assert all(sid in enemies for sid in referenced)
        assert all(sid in resources for sid in floor["resources"])
        seen_enemies.extend(referenced)
        seen_resources.extend(floor["resources"])
        if floor["destroy_enemy_max"]:
            assert len(referenced) == floor["destroy_enemy_max"]
        if floor["role"] == 304:
            assert isinstance(floor["role_option"]["seamless_battle"], dict)
    if "floor_parts" in stage:
        assert set(seen_enemies) == set(enemies)
        assert set(seen_resources) == set(resources)
        assert len(seen_enemies) == len(enemies)
        assert len(seen_resources) == len(resources)
    assert stage["drops"] == []
    assert json.loads(json.dumps(stage)) == stage


def test_every_main_story_and_tutorial_stage_builds_with_closed_references(battle_catalog):
    game = game_for(battle_catalog)
    before = copy.deepcopy(game.state)
    battles = battle_catalog.rows("m_chapter_quest_battles")
    tutorials = battle_catalog.rows("m_tutorial_quests")
    assert {battle["stage_type"] for battle in battles} == {1, 2}
    for seed, battle in enumerate(battles + tutorials, 1):
        stage = build_stage(game, battle["stage_type"], battle["stage_id"], seed,
                            fixed_fort_id=battle.get("m_fixed_fort_id", 0))
        assert_links(stage)
        assert stage["enemies"] or stage.get("boss_enemy")
        for enemy in stage["enemies"] + ([stage["boss_enemy"]] if "boss_enemy" in stage else []):
            definition = battle_catalog.get("m_enemies", enemy["m_enemy_id"])
            assert battle_catalog.get("m_enemy_parameters", definition["m_enemy_parameter_id"])["hp"] > 0
    assert game.state == before


@pytest.mark.parametrize("stage_type,stage_id", [(1, 1101101), (2, 1101001)])
def test_retries_keep_the_stage_but_new_sessions_change_entity_ids(battle_catalog, stage_type, stage_id):
    game = game_for(battle_catalog)
    first = build_stage(game, stage_type, stage_id, 1234)
    assert first == build_stage(game, stage_type, stage_id, 1234)
    later = build_stage(game, stage_type, stage_id, 1235)
    assert {row["sid"] for row in entities(first)}.isdisjoint(row["sid"] for row in entities(later))


def test_tutorial_defence_uses_fixed_layout_without_player_research(battle_catalog):
    game = game_for(battle_catalog)
    effect = next(row for row in battle_catalog.rows("m_building_skill_params")
                  if row["param_type"] == 500 and row["param_1"] == 3001 and row["param_2"] == 2)
    skill = battle_catalog.get("m_building_skill_levels", effect["m_building_skill_level_id"])
    game.state["records"]["t_building_skills"]["up"] = [
        {"m_building_id": skill["m_building_id"], "skill_type": skill["skill_type"], "skill_level": skill["skill_level"]},
    ]
    stage = build_stage(game, 2, 1000000000001, 5678, fixed_fort_id=1)
    assert stage["defensive_object"] == {"m_defensive_unit_id": 9000, "level": 1}
    assert [{key: value for key, value in row.items() if key != "sid"} for row in stage["defensive_units"]] == [
        {"m_defensive_unit_id": 4003, "level": 4, "placement_line": 1, "placement_index": 1},
        {"m_defensive_unit_id": 5003, "level": 2, "placement_line": 2, "placement_index": 1},
        {"m_defensive_unit_id": 4003, "level": 4, "placement_line": 3, "placement_index": 1},
        {"m_defensive_unit_id": 3001, "level": 1, "placement_line": 2, "placement_index": 0},
    ]


@pytest.mark.parametrize("tutorial_id", [1, 2, 3])
def test_native_tutorial_party_zero_uses_master_stage(battle_catalog, tutorial_id):
    definition = battle_catalog.get("m_tutorial_quests", tutorial_id)
    stage = build_stage(game_for(battle_catalog), definition["stage_type"], definition["stage_id"], tutorial_id,
                        fixed_fort_id=definition["m_fixed_fort_id"], party_no=0)
    assert_links(stage)
    if definition["stage_type"] == 2:
        assert len(stage["defensive_units"]) == 4


def test_main_story_defence_uses_owned_formation_and_research_level(battle_catalog):
    game = game_for(battle_catalog)
    records = game.state["records"]
    records["t_exploration_parties"]["up"] = [{"id": 1, "party_no": 1}, {"id": 2, "party_no": 2}]
    records["t_defensive_units"]["up"] = [{"id": 1, "m_defensive_unit_id": 4001, "level": 1}]
    records["t_disaster_parties"]["up"] = [
        {"id": 1, "t_exploration_party_id": 1, "disaster_type": 1, "m_disaster_boss_id": 0, "m_disaster_quest_id": 0},
        {"id": 2, "t_exploration_party_id": 1, "disaster_type": 2, "m_disaster_boss_id": 7, "m_disaster_quest_id": 0},
    ]
    records["t_disaster_party_defensive_units"]["up"] = [
        {"id": 1, "t_disaster_party_id": 1, "t_defensive_unit_id": 1, "placement_line": 2, "placement_index": 1},
        {"id": 2, "t_disaster_party_id": 2, "t_defensive_unit_id": 999, "placement_line": 1, "placement_index": 1},
    ]
    effect = next(row for row in battle_catalog.rows("m_building_skill_params")
                  if row["param_type"] == 500 and row["param_1"] == 4001 and row["param_2"] == 2)
    skill = battle_catalog.get("m_building_skill_levels", effect["m_building_skill_level_id"])
    records["t_building_skills"]["up"] = [
        {"m_building_id": skill["m_building_id"], "skill_type": skill["skill_type"], "skill_level": skill["skill_level"]},
    ]
    stage = build_stage(game, 2, 1101001, 42)
    assert len(stage["defensive_units"]) == 1
    assert stage["defensive_units"][0]["level"] == 2
    assert stage["defensive_units"][0]["m_defensive_unit_id"] == 4001
    records["t_disaster_party_defensive_units"]["up"][0]["t_defensive_unit_id"] = 999
    with pytest.raises(GameplayError, match="unowned"):
        build_stage(game, 2, 1101001, 42)


def test_empty_lottery_is_rejected_without_mutating_player(battle_catalog):
    catalog = copy.copy(battle_catalog)
    catalog.tables = {**battle_catalog.tables, "m_dungeon_floor_part_enemy_lotteries": {}}
    game = game_for(catalog)
    before = copy.deepcopy(game.state)
    with pytest.raises(GameplayError, match="no eligible"):
        build_stage(game, 1, 1101101, 1)
    assert game.state == before


@pytest.mark.parametrize("weighted", [0, 1])
def test_zero_weight_groups_are_excluded_in_both_lottery_modes(battle_catalog, weighted):
    catalog = copy.copy(battle_catalog)
    lottery = copy.deepcopy(battle_catalog.tables["m_dungeon_floor_part_enemy_lotteries"])
    group_id = 110110100
    eligible = next(row for row in lottery.values() if row["group_id"] == group_id)
    for row in lottery.values():
        if row["group_id"] == group_id:
            row["weight"] = 1 if row is eligible else 0
    catalog.tables = {**battle_catalog.tables, "m_dungeon_floor_part_enemy_lotteries": lottery}
    game = game_for(catalog)
    game.rules["battle_dungeon_lottery_weighted"] = weighted
    stage = build_stage(game, 1, 1101101, 2 ** 31 - 1)
    allowed = {row["m_enemy_id"] for row in catalog.rows("m_dungeon_floor_part_enemies")
               if row["group_id"] == eligible["enemy_group_id"]}
    floor = next(floor for floor in stage["floor_parts"] if floor["role"] == 304)
    spawned = {sid for group in floor["enemy_groups"] for sid in group["enemies"]}
    assert {row["m_enemy_id"] for row in stage["enemies"] if row["sid"] in spawned} <= allowed
    assert_links(stage)


def test_defence_lineup_count_and_energy_are_configurable(battle_catalog):
    game = game_for(battle_catalog)
    first = build_stage(game, 2, 1101001, 1)
    game.rules.update(battle_defence_enemy_copies=2, battle_defence_enemy_energy=777)
    expanded = build_stage(game, 2, 1101001, 2)
    assert len(expanded["enemies"]) == len(first["enemies"]) * 2
    assert all(row["drop_energy"] == 777 for row in expanded["enemies"])
    assert expanded["boss_enemy"]["m_enemy_id"] == first["boss_enemy"]["m_enemy_id"]
    assert expanded["boss_enemy"]["is_target"] == 1


def test_missing_defence_lineup_is_rejected(battle_catalog):
    catalog = copy.copy(battle_catalog)
    catalog.tables = {**battle_catalog.tables, "m_chapter_quest_battle_view_enemies": {}}
    with pytest.raises(GameplayError, match="no bound enemy lineup"):
        build_stage(game_for(catalog), 2, 1101001, 1)


@pytest.mark.parametrize("stage_type,stage_id,seed", [(True, 1, 1), (3, 1, 1), (1, 0, 1), (1, 1, -1), (1, 1, 2 ** 31)])
def test_invalid_builder_arguments_are_rejected(battle_catalog, stage_type, stage_id, seed):
    with pytest.raises(GameplayError):
        build_stage(game_for(battle_catalog), stage_type, stage_id, seed)
