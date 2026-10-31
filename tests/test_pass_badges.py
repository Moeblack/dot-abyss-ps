import copy
from datetime import timedelta

import pytest

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog, available, parse_timestamp
from dotabyss_ps.gameplay_pass import active_pass, maximum, receivable
from dotabyss_ps.gameplay_pass_state import pass_state
from dotabyss_ps.gameplay_plans import missions
from dotabyss_ps.gameplay_statistics import record
from dotabyss_ps.newgame import build_initial_state
from test_gameplay import NOW, gameplay_master


@pytest.fixture(scope="module")
def pass_template(gameplay_master):
    schema, tables = gameplay_master
    state = build_initial_state(tables, schema, 7, "Pass badge", "2026-10-31 10:00:00", start_tutorial=False)
    game = Gameplay(state, {}, GameplayCatalog(tables, schema), 7, NOW)
    game.prepare()
    return game


@pytest.fixture
def pass_game(pass_template):
    return Gameplay(copy.deepcopy(pass_template.state), copy.deepcopy(pass_template.progress),
                    pass_template.catalog, pass_template.user_id, NOW)


def current(game):
    identifier = next(row["id"] for row in game.catalog.rows("m_mission_passes") if available(row, game.now))
    return active_pass(game, identifier)


def claim_all_levels(game, definition, levels, *, premium):
    game.execute("api/mission-pass/level/reward/receive", "POST", {
        "m_mission_pass_id": definition["id"],
        "levels": [{"level": level["level"], "receive_normal": 1, "receive_premium": int(premium)}
                   for level in levels if level["level"] <= definition["m_mission_pass_level_max"]],
    })


def test_pass_badge_appears_at_level_threshold_and_clears_after_normal_claim(pass_game):
    game = pass_game
    definition, levels, state = current(game)
    first = min(levels, key=lambda row: row["level"])
    state["points"] = first["require_total_pass_point"] - 1
    assert not receivable(game)
    state["points"] += 1
    assert receivable(game)
    game.execute("api/mission-pass/level/reward/receive", "POST", {
        "m_mission_pass_id": definition["id"],
        "levels": [{"level": first["level"], "receive_normal": 1, "receive_premium": 0}],
    })
    assert not receivable(game)


def test_pass_badge_requires_premium_access_for_unclaimed_premium_track(pass_game):
    game = pass_game
    definition, levels, state = current(game)
    first = min(levels, key=lambda row: row["level"])
    state["points"] = first["require_total_pass_point"]
    state["rewards"][str(first["level"])] = {"normal": True, "premium": False}
    assert not receivable(game)
    state["premium"] = 1
    assert receivable(game)
    game.execute("api/mission-pass/level/reward/receive", "POST", {
        "m_mission_pass_id": definition["id"],
        "levels": [{"level": first["level"], "receive_normal": 0, "receive_premium": 1}],
    })
    assert not receivable(game)


def test_pass_badge_follows_task_claim_then_level_claim(pass_game):
    game = pass_game
    definition, levels, state = current(game)
    task = next(row for row in game.catalog.rows("m_missions")
                if row["m_mission_pass_id"] == definition["id"] and row["situation_type"] == 60000)
    assert not receivable(game)
    record(game.progress, game.now, task["situation_type"], task["require_value"])
    evaluator = missions(game)
    assert receivable(game, evaluator)
    game.execute("api/mission-pass/mission/reward/receive", "POST", {
        "m_mission_pass_id": definition["id"], "m_mission_ids": [task["id"]],
    })
    reached = [row for row in levels if row["require_total_pass_point"] <= state["points"]]
    assert receivable(game) == bool(reached)
    if reached:
        claim_all_levels(game, definition, reached, premium=False)
    assert not receivable(game)


def test_pass_badge_ignores_unmet_task_predecessor(pass_game):
    game = pass_game
    definition, _, _ = current(game)
    task = next(row for row in game.catalog.rows("m_missions")
                if row["m_mission_pass_id"] == definition["id"] and row["require_mission_id"])

    class Evaluator:
        def receivable(self, mission):
            return mission["id"] == task["id"] and missions(game).receivable(mission)

    record(game.progress, game.now, task["situation_type"], task["require_value"], task["target_id"], task["sub_target_id"])
    assert not receivable(game, Evaluator())


def test_pass_loop_badge_uses_unclaimed_intervals_and_clears_after_claim(pass_game):
    game = pass_game
    definition, levels, state = current(game)
    cap = maximum(definition, levels)
    loop = next(row for row in levels if row["level"] == cap["level"] + 1)
    interval = loop["require_total_pass_point"] - cap["require_total_pass_point"]
    state["points"] = cap["require_total_pass_point"] + interval - 1
    claim_all_levels(game, definition, levels, premium=False)
    assert not receivable(game)
    state["points"] += 1
    assert receivable(game)
    game.execute("api/mission-pass/loop-reward/receive", "POST", {"m_mission_pass_id": definition["id"]})
    assert state["loop_received"] == 1
    assert not receivable(game)


@pytest.mark.parametrize("boundary", ["before", "after"])
def test_pass_badge_ignores_inactive_pass_without_mutating_claims(pass_game, boundary):
    game = pass_game
    definition, levels, state = current(game)
    state["points"] = maximum(definition, levels)["require_total_pass_point"]
    state["premium"] = 1
    game.now = (parse_timestamp(definition["start_at"]) - timedelta(seconds=1) if boundary == "before"
                else parse_timestamp(definition["end_at"]) + timedelta(seconds=1))
    before = copy.deepcopy(state)

    class EmptyEvaluator:
        def receivable(self, mission):
            return False

    assert not receivable(game, EmptyEvaluator())
    assert state == before


def test_pass_badge_does_not_consider_other_pass_task(pass_game):
    game = pass_game
    definition, _, _ = current(game)
    foreign = next(row for row in game.catalog.rows("m_missions")
                   if row["m_mission_pass_id"] and row["m_mission_pass_id"] != definition["id"])

    class Evaluator:
        def receivable(self, mission):
            return mission["id"] == foreign["id"]

    assert not receivable(game, Evaluator())


def test_pass_badge_accepts_old_save_without_pass_record(pass_game):
    game = pass_game
    definition, _, _ = current(game)
    game.progress["features"]["mission_pass"]["data"].pop(str(definition["id"]))
    assert not receivable(game)
    assert pass_state(game.progress, definition["id"]) == {"points": 0, "premium": 0, "loop_received": 0, "rewards": {}}


def test_pass_badge_supports_master_without_passes(pass_game):
    game = pass_game
    game.catalog = copy.copy(game.catalog)
    game.catalog.tables = {name: rows for name, rows in game.catalog.tables.items() if name != "m_mission_passes"}
    assert not receivable(game)


def test_shared_home_badges_follow_pass_task_claim(pass_game):
    game = pass_game
    definition, _, _ = current(game)
    task = next(row for row in game.catalog.rows("m_missions")
                if row["m_mission_pass_id"] == definition["id"] and row["situation_type"] == 60000)
    assert missions(game).badges()["mission_pass_reward"] == 0
    record(game.progress, game.now, task["situation_type"], task["require_value"])
    assert missions(game).badges()["mission_pass_reward"] == 1
    game.execute("api/mission-pass/mission/reward/receive", "POST", {
        "m_mission_pass_id": definition["id"], "m_mission_ids": [task["id"]],
    })
    assert missions(game).badges()["mission_pass_reward"] == 0
