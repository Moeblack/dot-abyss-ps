import copy

import pytest

from dotabyss_ps.gameplay_catalog import GameplayError
from dotabyss_ps.gameplay_nether_state import ensure, run
from test_battle_rewards import reward_game, reward_master
from test_nether_campaign import coordinates, execute, resolve_event
from test_nether_growth import game_with_party
from test_nether_tickets_hidden import enter, skip_segment


def secret_pair(game, kinds):
    expedition = run(game)
    for secret in expedition["floors"]:
        if not secret["is_hidden"]:
            continue
        for previous in expedition["floors"]:
            if previous["floor_level"] == secret["floor_level"] - 1 and game.catalog.get("m_nether_map_floors", previous["m_nether_map_floor_id"])["type"] in kinds:
                return secret, previous
    raise AssertionError("Required secret-floor predecessor is absent")


def test_update_notifies_only_newly_opened_secret_floor(reward_game):
    game = game_with_party(reward_game)
    enter(game, 1)
    first = next(row for row in run(game)["floors"] if row["floor_level"] == 1)
    assert execute(game, "nether/update", coordinates(first))["t_nether_map_floor"] is None
    secret, previous = secret_pair(game, {5, 6})
    condition = next(row for row in game.catalog.rows("m_nether_floor_restricts") if row["m_nether_map_floor_id"] == secret["m_nether_map_floor_id"])
    ensure(game).update(floor_level=previous["floor_level"] - 1, m_nether_map_floor_id=0, status=2,
                        erosion_point=condition["restrict_param_1"] - 1)
    response = execute(game, "nether/update", coordinates(previous))
    assert response["t_nether_map_floor"] == secret
    assert coordinates(response["t_nether_map_floor"]) != coordinates(previous)
    assert response["t_nether_map_floor"]["is_hidden"] == 0
    assert execute(game, "nether/update", coordinates(previous))["t_nether_map_floor"] is None


def test_update_event_notifies_opened_secret_and_then_returns_null(reward_game):
    game = game_with_party(reward_game)
    enter(game, 1)
    secret, previous = secret_pair(game, {5})
    condition = next(row for row in game.catalog.rows("m_nether_floor_restricts") if row["m_nether_map_floor_id"] == secret["m_nether_map_floor_id"])
    ensure(game).update(floor_level=previous["floor_level"], floor_index=previous["floor_index"],
                        m_nether_map_floor_id=previous["m_nether_map_floor_id"], erosion_point=condition["restrict_param_1"])
    request = {**coordinates(previous), "select_number": 2, "m_nether_code_id": 0}
    response = execute(game, "nether/update-event", request)
    assert response["t_nether_map_floor"] == secret and secret["is_hidden"] == 0
    assert execute(game, "nether/update-event", request)["t_nether_map_floor"] is None
    listed = execute(game, "nether")
    assert next(row for row in listed["t_nether_map_floors"] if coordinates(row) == coordinates(secret))["is_hidden"] == 0


def test_old_unsettled_score_fields_are_defaulted_without_regranting(reward_game):
    game = game_with_party(reward_game)
    enter(game, 3)
    skip_segment(game)
    expedition = run(game)
    for key in ("base_score", "bonus_score", "score_multiplier"):
        del expedition[key]
    request = {"m_nether_id": 1, "m_nether_map_id": 1, "use_item_flag": 0}
    result = execute(game, "nether/result", request)
    assert result["result_score"] == 1000 and result["result_boost_score"] == 0
    before = copy.deepcopy([game.state, game.progress])
    assert execute(game, "nether/result", request)["result_score"] == 1000
    assert [game.state, game.progress] == before


@pytest.mark.parametrize("provable", [True, False])
def test_old_event_battle_connection_is_repaired_only_with_master_evidence(reward_game, provable):
    game = game_with_party(reward_game)
    enter(game, 1)
    source = target = None
    for event in game.catalog.rows("m_nether_floor_events"):
        for number in range(1, 5):
            if not event[f"m_nether_floor_event_part_id_{number}"]:
                continue
            part = game.catalog.get("m_nether_floor_event_parts", event[f"m_nether_floor_event_part_id_{number}"])
            for index in range(1, 4):
                if part[f"target_type_{index}"] == 8:
                    source, target = event["m_nether_map_floor_id"], part[f"select_parameter_{index}"]
                    break
            if source:
                break
        if source:
            break
    expedition = run(game)
    current_floor = next(row for row in expedition["floors"] if row["floor_level"] == 1)
    current_floor["m_nether_map_floor_id"] = target
    actual = {row["m_nether_map_floor_id"] for row in expedition["floors"] if row["floor_level"] == 1}
    stale = source if provable else 999999999
    following = [row for row in expedition["floors"] if row["floor_level"] == 2]
    for row in following:
        row["m_nether_map_floor_prev_ids"] = sorted((actual - {target}) | {stale})
    ensure(game).update(floor_level=1, floor_index=current_floor["floor_index"], m_nether_map_floor_id=target)
    expedition["resolved"].append(f"1:{current_floor['floor_index']}")
    if provable:
        execute(game, "nether/update", coordinates(following[0]))
        assert all(stale not in row["m_nether_map_floor_prev_ids"] and target in row["m_nether_map_floor_prev_ids"] for row in following)
    else:
        with pytest.raises(GameplayError, match="not connected"):
            execute(game, "nether/update", coordinates(following[0]))
        assert all(stale in row["m_nether_map_floor_prev_ids"] for row in following)
