import copy

import pytest

from dotabyss_ps.event_master import OPEN_DATE, CLOSE_DATE, reopened_tables
from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError
from dotabyss_ps.gameplay_event_quest_state import QUEST_KINDS, release_novels
from dotabyss_ps.gameplay_records import find, put, rows
from dotabyss_ps.gameplay_recommendations import owner_context
from dotabyss_ps.gameplay_rules import load_rules, validate_rules
from dotabyss_ps.newgame import build_initial_state
from test_gameplay import NOW, gameplay_master


def test_reopened_recommendation_dates_follow_only_their_owner(gameplay_master):
    schema, tables = gameplay_master
    original = copy.deepcopy(tables["m_payment_recommends"])
    rules = {**load_rules(), "event_open_all": 0, "event_open_ids": [700001]}
    projected, changed = reopened_tables(tables, schema, rules)
    before, after = GameplayCatalog(tables, schema), GameplayCatalog(projected, schema)
    count = 0
    for definition in before.rows("m_payment_recommends"):
        actual = after.get("m_payment_recommends", definition["id"])
        if owner_context(before, definition) == ("event", 700001):
            assert actual["start_at"] == OPEN_DATE and actual["end_at"] == CLOSE_DATE
            count += 1
        else:
            assert actual == definition
    assert count == changed["m_payment_recommends"] == 4
    assert tables["m_payment_recommends"] == original


def test_speedrun_novel_requires_the_referenced_quest_clear(gameplay_master):
    schema, tables = gameplay_master
    state = build_initial_state(tables, schema, 7, "Novel condition", "2026-11-01 00:00:00", start_tutorial=False)
    game = Gameplay(state, {}, GameplayCatalog(tables, schema), 7, NOW, load_rules())
    game.prepare()
    event = game.catalog.get("m_events", 1100001)
    kind = QUEST_KINDS["speedrun"]
    assert release_novels(game, kind, event) == []
    assert find(state, "t_novel_events", m_novel_event_id=100000101) is None
    put(state, kind.records, {kind.field: 1100001101}, {"status": 99})
    assert release_novels(game, kind, event) == []
    put(state, kind.records, {kind.field: 1100001103}, {"status": 1})
    assert release_novels(game, kind, event) == []
    find(state, kind.records, **{kind.field: 1100001103})["status"] = 99
    assert release_novels(game, kind, event) == [100000101]
    assert release_novels(game, kind, event) == []
    assert len([r for r in rows(state, "t_novel_events") if r["m_novel_event_id"] == 100000101]) == 1


@pytest.mark.parametrize("value", [{"../x": 2}, {"260099": True}, {"260099": 0}, [], {"260099": 100001}])
def test_board_threshold_configuration_is_validated(value):
    with pytest.raises(GameplayError):
        validate_rules({"recommendation_board_sheet_requirements": value})
