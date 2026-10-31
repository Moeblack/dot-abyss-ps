import copy
from datetime import timedelta

import pytest

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayError
from dotabyss_ps.gameplay_nether_battle import attach_rewards
from dotabyss_ps.gameplay_nether import pay_entry
from dotabyss_ps.gameplay_nether_events import add_code, erosion, upgrade_sphere
from dotabyss_ps.gameplay_nether_party import snapshot_party
from dotabyss_ps.gameplay_nether_missions import NetherMissions
from dotabyss_ps.gameplay_missions import Missions
from dotabyss_ps.gameplay_nether_state import ensure, point_view, state
from dotabyss_ps.gameplay_records import put
from test_battle_rewards import dungeon_stage, reward_game, reward_master
from test_gameplay import party_body


def game_with_party(source):
    game = Gameplay(source.state, {}, source.catalog, source.user_id, source.now, source.rules)
    game.execute("api/party/exploration/edit", "POST", party_body())
    return game


def sphere_body(row):
    return {"m_nether_id": 1, "m_nether_map_id": 1, "m_nether_sphere_board_id": row["m_nether_sphere_board_id"],
            "m_nether_sphere_id": row["id"], "current_level": row["level"] - 1}


def test_all_sphere_boards_require_predecessors_and_grant_cumulative_effects(reward_game):
    game = game_with_party(reward_game)
    for key in state(game)["points"]:
        state(game)["points"][key] = 1000000
    for cost in game.catalog.rows("m_nether_sphere_costs"):
        if cost["content_type"] == 30:
            put(game.state, "t_items", {"m_item_id": cost["content_id"]}, {"amount": 1000000})
    sphere = game.catalog.get("m_nether_spheres", 170101)
    with pytest.raises(GameplayError, match="before unlocking its center"):
        upgrade_sphere(game, sphere_body(sphere))
    sphere = next(row for row in game.catalog.rows("m_nether_spheres")
                  if row["m_nether_sphere_board_id"] == 20 and row["type"] == 1)
    with pytest.raises(GameplayError, match="previous Nether sphere board center"):
        upgrade_sphere(game, sphere_body(sphere))
    acquired = 0
    for board in sorted(game.catalog.rows("m_nether_sphere_boards"), key=lambda row: row["display_order"]):
        spheres = [row for row in game.catalog.rows("m_nether_spheres") if row["m_nether_sphere_board_id"] == board["id"]]
        for sphere in sorted(spheres, key=lambda row: (row["type"], row["level"])):
            game.execute("api/nether/upgrade-sphere", "POST", sphere_body(sphere))
            acquired += 1
    assert acquired == 125
    effects = point_view(game)
    assert effects["recovery_floor_level"] == 100
    assert effects["extra_drop_ratio"] == 54
    assert effects["max_nether_code"] > game.rules["nether_initial_code_limit"]
    assert effects["lock_reward"] > 0 and effects["max_code_reload"] > 1


def test_sphere_extra_drop_ratio_changes_frozen_battle_drop_manifest(reward_game):
    game = game_with_party(reward_game)
    game.rules["nether_random_drop_probability_per_mille"] = 1000
    base = dungeon_stage()
    before = attach_rewards(game, base, 1, 7)
    definition = game.catalog.get("m_nether_spheres", 10021)
    game.catalog.tables["m_nether_spheres"] = dict(game.catalog.tables["m_nether_spheres"])
    game.catalog.tables["m_nether_spheres"][10021] = {**definition, "effect_parameter_1": 1000}
    state(game)["spheres"].append({"m_nether_sphere_board_id": 10, "type": 2, "level": 1})
    boosted = dungeon_stage()
    after = attach_rewards(game, boosted, 1, 7)
    assert len(after) == len(before) * 2
    assert sum(reward.get("bonus_type", 0) == 1 for reward in after.values()) == len(before)
    assert len({drop["sid"] for drop in boosted["drops"]}) == len(after)
    assert all("bonus_type" not in drop for drop in boosted["drops"])
    assert all(len(row["drops"]) == 2 for row in boosted["enemies"] + boosted["resources"])


def test_five_erosion_resistance_codes_activate_category_erosion_reduction(reward_game):
    game = game_with_party(reward_game)
    candidates = [row for row in game.catalog.rows("m_nether_codes") if row["category"] == 3 and row["effect_type"] == 1]
    for definition in candidates[:4]:
        add_code(game, definition["id"])
    ensure(game)["erosion_point"] = 50
    erosion(game, 1)
    assert ensure(game)["erosion_point"] == 51
    add_code(game, candidates[4]["id"])
    ensure(game)["erosion_point"] = 50
    erosion(game, 1)
    assert ensure(game)["erosion_point"] == 46


def test_party_uses_effect_ids_and_resistance_upgrade_levels(reward_game):
    game = game_with_party(reward_game)
    put(game.state, "t_character_condition_resistances", {"t_character_id": 1, "up_parameter_type": 8}, {"level": 3}, game.user_id)
    party = snapshot_party(game, 1)
    first = party["characters"][0]
    assert first["m_character_id"] == 1200016
    assert {row["m_ability_effect_id"] for row in first["character_abilities"]} == {20077, 20078}
    assert first["resist_condition_1"] == 3
    for row in first["character_abilities"]:
        definition = game.catalog.get("m_ability_effects", row["m_ability_effect_id"])
        assert (row["type"], row["value"]) == (definition["type"], definition["value"])


def test_nether_code_collection_counts_distinct_types_not_reacquisition(reward_game):
    game = game_with_party(reward_game)
    identifier = next(row["id"] for row in game.catalog.rows("m_nether_codes") if row["category"] == 1)
    added = add_code(game, identifier)
    mission = game.catalog.get("m_missions", 91000001)
    assert NetherMissions(game).value(mission) == 1
    added["amount"] = 0
    add_code(game, identifier)
    assert NetherMissions(game).value(mission) == 1
    second = next(row["id"] for row in game.catalog.rows("m_nether_codes") if row["category"] == 1 and row["id"] != identifier)
    add_code(game, second)
    assert NetherMissions(game).value(mission) == 2
    generic = Missions(game.state, game.progress, game.catalog, game.now, game.user_id, game.rules)
    assert generic.value(mission) == NetherMissions(game).value(mission)


def test_gate_ticket_day_changes_at_five_and_counts_only_consumed_items(reward_game):
    game = game_with_party(reward_game)
    pay_entry(game, 0)
    assert game.progress["features"]["statistics"]["data"]["totals"].get("120018:0:0", 0) == 0
    put(game.state, "t_items", {"m_item_id": 200002}, {"amount": 3})
    assert pay_entry(game, 3) == 3
    ensure(game)["used_ticket_day"] = 3
    assert game.progress["features"]["statistics"]["data"]["totals"]["120018:0:0"] == 3
    game.now += timedelta(hours=9, minutes=59)
    state(game)
    assert ensure(game)["used_ticket_day"] == 3
    with pytest.raises(GameplayError, match="No free Nether entries"):
        pay_entry(game, 0)
    game.now += timedelta(minutes=1)
    state(game)
    assert ensure(game)["used_ticket_day"] == 0
    assert pay_entry(game, 0) == 0
