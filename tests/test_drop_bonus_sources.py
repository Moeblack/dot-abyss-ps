import copy

import pytest

from dotabyss_ps.admin_validation import validate_import
from dotabyss_ps.gameplay_battle_session import battle_state, create_session
from dotabyss_ps.gameplay_battle_rewards import extra_drop_probability, stored_drop_probability
from dotabyss_ps.gameplay_catalog import GameplayError
from dotabyss_ps.gameplay_characters import ensure_abilities
from dotabyss_ps.gameplay_content import create_character
from dotabyss_ps.gameplay_drop_bonuses import member_bonus, party_bonus, save_bonus, saved_bonus, snapshots
from dotabyss_ps.gameplay_records import find, put, rows
from test_battle_rewards import dungeon_stage, reward_game, reward_master
from test_nether_growth import game_with_party


def bonus_character(game):
    create_character(game.state, 1300041, game.catalog, game.user_id, game.now, game.progress)
    character = find(game.state, "t_characters", m_character_id=1300041)
    character["intimacy_point"] = 2650
    ensure_abilities(game)
    return character


def field_member(character):
    return {"position_no": 1, "t_character_id": character["id"], "t_weapon_id": 0,
            "t_armor_id": 0, "t_accessory_id": 0}


def test_bonus_comes_from_active_members_and_does_not_multiply_skill_level(reward_game):
    game = game_with_party(reward_game)
    character = bonus_character(game)
    assert party_bonus(game, 1) == 0
    game.execute("api/party/exploration/edit", "POST", {"party_no": 1, "characters": [field_member(character)], "mana_gems": []})
    assert party_bonus(game, 1) == 50
    ability = find(game.state, "t_character_abilities", t_character_id=character["id"], m_character_ability_id=130004103)
    ability["level"] = 10
    assert party_bonus(game, 1) == 50
    ability["level"] = 0
    assert party_bonus(game, 1) == 0


def test_idle_bonus_uses_its_own_party_members(reward_game):
    game = game_with_party(reward_game)
    character = bonus_character(game)
    put(game.state, "t_idle_exploration_party_characters", {"party_no": 1, "position_no": 1},
        {"t_character_id": character["id"], "t_weapon_id": 0}, game.user_id)
    assert party_bonus(game, 1) == 0
    assert party_bonus(game, 1, idle=True) == 50
    assert party_bonus(game, 2, idle=True) == 0


def test_stored_probability_leaves_global_bonus_to_native_client(reward_game):
    game = game_with_party(reward_game)
    character = bonus_character(game)
    game.execute("api/party/exploration/edit", "POST", {"party_no": 1, "characters": [field_member(character)], "mana_gems": []})
    rank = next(row for row in game.catalog.rows("m_vip_ranks") if row["rank"] == 6)
    put(game.state, "t_vip_ranks", {}, {"total_point": rank["total_point"]}, game.user_id)
    game.catalog.get("m_chapter_quest_battles", 1001)["addition_extra_drop_probability"] = 0
    assert stored_drop_probability(game, 1001, party_no=1) == 50
    assert extra_drop_probability(game, 1001, party_no=1) == 80


def test_unique_enchantment_does_not_suppress_another_equipment_source(reward_game):
    game = game_with_party(reward_game)
    character = rows(game.state, "t_characters")[0]
    definition = next(row for row in game.catalog.rows("m_enchantments") if row["is_unique"])
    member = {**field_member(character), "t_weapon_id": 9001, "t_armor_id": 9002}
    for singular, identifier in (("weapon", 9001), ("armor", 9002)):
        put(game.state, f"t_{singular}_enchantments", {f"t_{singular}_id": identifier}, {
            "enchantment_no": definition["enchantment_no"], "enchantment_ability_no": definition["enchantment_ability_no"],
            "enchantment_group_no": 1, "m_ability_effect_id": 20288, "level": 1, "is_appraised": 1}, game.user_id)
    assert member_bonus(game, member) == 100


def test_new_battle_freezes_member_bonus_and_fixed_parties_exclude_it(reward_game):
    game = game_with_party(reward_game)
    character = bonus_character(game)
    game.execute("api/party/exploration/edit", "POST", {"party_no": 1, "characters": [field_member(character)], "mana_gems": []})
    definition = game.catalog.get("m_chapter_quest_battles", 1001)
    create_session(game, definition, kind="main", quest_id=1001, party_no=1, stage_detail=dungeon_stage())
    session = battle_state(game)["active"]
    assert session["ability_extra_drop"] == 50
    save_bonus(game, session)
    for ability in rows(game.state, "t_character_abilities"):
        if ability["t_character_id"] == character["id"]:
            ability["level"] = 0
    assert party_bonus(game, 1) == 0
    assert saved_bonus(game, "main", 1001, 1) == saved_bonus(game, "repeat", 1001, 1) == 50
    assert session["ability_extra_drop"] == 50
    find(game.state, "t_character_abilities", t_character_id=character["id"], m_character_ability_id=130004103)["level"] = 1
    assert party_bonus(game, 1) == 50
    battle_state(game)["active"] = None
    fixed = game.catalog.rows("m_fixed_parties")[0]["id"]
    create_session(game, {**definition, "m_fixed_party_id": fixed}, kind="main", quest_id=1001,
                   party_no=1, stage_detail=dungeon_stage(), validate_members=False)
    assert battle_state(game)["active"]["ability_extra_drop"] == 0


def test_import_accepts_valid_drop_snapshot_without_mutation(reward_game):
    game = game_with_party(reward_game)
    snapshots(game)["main:1001"] = 50
    before = copy.deepcopy((game.state, game.progress))
    validate_import(game)
    assert (game.state, game.progress) == before


@pytest.mark.parametrize("key,value", [
    ("main:1001", -1), ("main:1001", 1001), ("main:1001", True),
    ("bogus:1001", 50), ("main:0001001", 50), ("main:999999999999", 50),
])
def test_import_rejects_invalid_drop_snapshot_without_mutation(reward_game, key, value):
    game = game_with_party(reward_game)
    snapshots(game)[key] = value
    before = copy.deepcopy((game.state, game.progress))
    with pytest.raises(GameplayError):
        validate_import(game)
    assert (game.state, game.progress) == before
