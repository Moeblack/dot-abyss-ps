from types import SimpleNamespace

import pytest

from dotabyss_ps.admin_actions_progress import finish_story
from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog
from dotabyss_ps.gameplay_library import chapter_complete
from dotabyss_ps.gameplay_missions import Missions
from dotabyss_ps.gameplay_records import find
from dotabyss_ps.gameplay_story_state import COMPLETED_SEQUENCE, is_quest_complete
from dotabyss_ps.newgame import build_initial_state
from test_gameplay import NOW, gameplay_master


def story_state(*entries):
    return {"records": {"t_chapter_quests": {"up": [
        {"id": index, "m_chapter_quest_id": identifier, "sequence": sequence}
        for index, (identifier, sequence) in enumerate(entries, 1)
    ], "down": []}}}


@pytest.mark.parametrize("sequence,expected", [(0, False), (1, False), (3, False), (4, False), (98, False), (99, True), (100, False)])
def test_completion_uses_native_sentinel_instead_of_last_step(sequence, expected):
    state = story_state((1010101, sequence))
    assert is_quest_complete(state, 1010101) is expected
    assert chapter_complete(SimpleNamespace(state=state), 1010101) is expected
    assert not is_quest_complete(state, 1010102)


def test_story_missions_count_only_completed_quests_and_preserve_repeat_counts(monkeypatch):
    state = story_state((1, 99), (2, 4), (3, 99), (999, 99))
    catalog = SimpleNamespace(rows=lambda table: [{"id": identifier} for identifier in (1, 2, 3)])
    evaluator = object.__new__(Missions)
    evaluator.state, evaluator.catalog = state, catalog
    evaluator.progress, evaluator.statistics, evaluator.now = {}, {}, NOW
    monkeypatch.setattr("dotabyss_ps.gameplay_missions.statistic_value", lambda *args, **kwargs: 5)
    mission = {"situation_type": 20000, "target_id": 0}
    assert evaluator.value(mission) == 7
    assert evaluator.value({**mission, "target_id": 1}) == 1
    assert evaluator.value({**mission, "target_id": 2}) == 0
    assert evaluator.value({**mission, "target_id": 4}) == 0


def test_console_story_completion_writes_native_sentinel_and_preserves_reward_receipts(gameplay_master):
    schema, tables = gameplay_master
    state = build_initial_state(tables, schema, 7, "Story completion test", "2026-11-01 00:00:00", start_tutorial=False)
    game = Gameplay(state, {}, GameplayCatalog(tables, schema), 7, NOW)
    game.prepare()
    assert finish_story(game, [1010101]) == {1010101}
    assert find(state, "t_chapter_quests", m_chapter_quest_id=1010101)["sequence"] == COMPLETED_SEQUENCE
    ledger = game.progress["features"]["battle_rewards"]["data"]
    assert ledger["quest_claims"]["1010101"] == {"legacy": True}
    assert ledger["first_claims"]["1010101:2"] == {"legacy": True}
    rewards_before = dict(ledger["quest_claims"])
    finish_story(game, [1010101])
    assert ledger["quest_claims"] == rewards_before
