import copy
import json
from collections import Counter

import pytest

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayError
from dotabyss_ps.gameplay_nether_state import add_item, ensure, generate_map, point_view, run
from dotabyss_ps.gameplay_records import find, put
from test_battle_rewards import reward_game, reward_master
from test_nether_campaign import choose_codes, coordinates, execute, resolve_event
from test_nether_growth import game_with_party


def enter(game, tickets):
    put(game.state, "t_items", {"m_item_id": 200002}, {"amount": 100})
    put(game.state, "t_items", {"m_item_id": 200007}, {"amount": 10})
    request = {"m_nether_id": 1, "m_nether_map_id": 1, "party_no": 1, "use_ticket": tickets, "start_floor_level": 0}
    game.execute("api/nether/start", "POST", request, "start-once")
    before = copy.deepcopy([game.state, game.progress])
    game.execute("api/nether/start", "POST", request, "start-once")
    assert [game.state, game.progress] == before


def skip_segment(game):
    execute(game, "nether/skip", {"m_nether_id": 1, "m_nether_map_id": 1,
            "start_floor_level": ensure(game)["floor_level"], "is_rare": 0, "party_no": 1, "lock_items": []})
    choose_codes(game)


@pytest.mark.parametrize("tickets,coefficient,multiplier", [(0, 1, 1), (1, 1, 1), (3, 1, 3), (3, 0, 1), (3, 2, 5)])
def test_ticket_multiplier_is_frozen_and_score_display_statistics_agree(reward_game, tickets, coefficient, multiplier):
    game = game_with_party(reward_game)
    game.rules["nether_score_multiplier_per_extra_ticket"] = coefficient
    enter(game, tickets)
    game.rules["nether_score_multiplier_per_extra_ticket"] = 99
    skip_segment(game)
    add_item(game, 200003, 7)
    previous = find(game.state, "t_items", m_item_id=200003)
    amount = previous["amount"] if previous else 0
    response = execute(game, "nether/result", {"m_nether_id": 1, "m_nether_map_id": 1, "use_item_flag": 0})
    assert response["result_score"] == 1000 * multiplier
    assert response["result_boost_score"] == 1000 * (multiplier - 1)
    assert response["result_score_bonus"] == 1
    assert response["t_nether_point"]["total_score"] == 1000 * multiplier
    assert game.progress["features"]["statistics"]["data"]["totals"]["120002:0:0"] == 1000 * multiplier
    assert find(game.state, "t_items", m_item_id=200002)["amount"] == 100 - tickets
    assert find(game.state, "t_items", m_item_id=200003)["amount"] == amount + 7
    assert sum(response["nether_code_points"].values()) == 3
    assert response["result_rank"] == max(row["score_level"] for row in game.catalog.rows("m_nether_result_reward_levels")
                                          if row["total_score"] <= 1000 * multiplier)
    before = copy.deepcopy([game.state, game.progress])
    again = execute(game, "nether/result", {"m_nether_id": 1, "m_nether_map_id": 1, "use_item_flag": 0})
    assert again["result_score"] == response["result_score"] and [game.state, game.progress] == before


def test_continuation_bonus_only_applies_to_its_segment_and_retry_costs_nothing(reward_game):
    game = game_with_party(reward_game)
    enter(game, 2)
    skip_segment(game)
    request = {"m_nether_id": 1, "m_nether_map_id": 1, "floor_level": 10, "use_ticket": 3, "lock_items": []}
    execute(game, "nether/continue", request)
    before = copy.deepcopy([game.state, game.progress])
    execute(game, "nether/continue", request)
    assert [game.state, game.progress] == before
    restored = json.loads(json.dumps([game.state, game.progress]))
    game = Gameplay(*restored, game.catalog, game.user_id, game.now, game.rules)
    skip_segment(game)
    response = execute(game, "nether/result", {"m_nether_id": 1, "m_nether_map_id": 1, "use_item_flag": 0})
    assert response["result_score"] == 5000 and response["result_boost_score"] == 3000
    assert find(game.state, "t_items", m_item_id=200002)["amount"] == 95


def test_generated_secret_templates_start_hidden_and_obey_usage_limits(reward_game):
    game = game_with_party(reward_game)
    restricted = {row["m_nether_map_floor_id"] for row in game.catalog.rows("m_nether_floor_restricts")}
    floors = generate_map(game, 1, 1)
    counts = Counter(row["m_nether_map_floor_id"] for row in floors)
    assert any(row["is_hidden"] for row in floors)
    for row in floors:
        master = game.catalog.get("m_nether_map_floors", row["m_nether_map_floor_id"])
        assert row["is_hidden"] == int(master["id"] in restricted)
        assert not master["used_count"] or counts[master["id"]] <= master["used_count"]
    assert len({row["floor_level"] for row in floors}) == 150
    assert all(any(row["floor_level"] == level and game.catalog.get("m_nether_map_floors", row["m_nether_map_floor_id"])["type"] == 2
                   for row in floors) for level in range(10, 151, 10))


def test_secret_floor_reveals_by_erosion_and_counts_once_after_restore(reward_game):
    game = game_with_party(reward_game)
    enter(game, 1)
    floor = next(row for row in run(game)["floors"] if row["is_hidden"])
    restriction = next(row for row in game.catalog.rows("m_nether_floor_restricts") if row["m_nether_map_floor_id"] == floor["m_nether_map_floor_id"])
    current = ensure(game)
    current.update(floor_level=floor["floor_level"] - 1, floor_index=1,
                   m_nether_map_floor_id=floor["m_nether_map_floor_prev_ids"][0], erosion_point=restriction["restrict_param_1"] - 1)
    run(game)["resolved"].append(f"{current['floor_level']}:1")
    before = copy.deepcopy([game.state, game.progress])
    with pytest.raises(GameplayError, match="erosion requirement"):
        execute(game, "nether/update", coordinates(floor))
    assert [game.state, game.progress] == before and floor["is_hidden"] == 1
    current["erosion_point"] = restriction["restrict_param_1"]
    listed = execute(game, "nether")
    revealed = next(row for row in listed["t_nether_map_floors"] if coordinates(row) == coordinates(floor))
    assert revealed["is_hidden"] == 0
    restored = json.loads(json.dumps([game.state, game.progress]))
    game = Gameplay(*restored, game.catalog, game.user_id, game.now, game.rules)
    execute(game, "nether/update", coordinates(floor))
    assert ensure(game)["hidden_count"] == 1
    execute(game, "nether/update", coordinates(floor))
    assert ensure(game)["hidden_count"] == 1
    live = next(row for row in run(game)["floors"] if coordinates(row) == coordinates(floor))
    resolve_event(game, live)
    assert f"{floor['floor_level']}:{floor['floor_index']}" in run(game)["resolved"]
