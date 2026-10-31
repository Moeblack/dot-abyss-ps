import json
from datetime import timedelta
from types import SimpleNamespace

import pytest

from dotabyss_ps.gameplay_catalog import GameplayCatalog
from dotabyss_ps.gameplay_event_missions import EventMissions, definitions, record_clear
from dotabyss_ps.gameplay_rules import DEFAULT_RULES
from test_event_quests import event_game, join, nodes, complete
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_training_encounter_modes import clock, edit, records, table


def game(context, state, progress, now=NOW):
    return SimpleNamespace(state=state, progress=progress, catalog=GameplayCatalog(context.tables, context.schema),
                           now=now, user_id=context.identity["user_id"], rules=DEFAULT_RULES)


def record(context, event_id, quest_id, *, elapsed=10, damage=0, members=None, effects=1, skip=False, story=False, count=1, now=NOW, kind=None, stage_id=None):
    def update(state, progress):
        current = game(context, state, progress, now)
        event = current.catalog.get("m_events", event_id)
        record_clear(current, event, quest_id, {"play_time": elapsed, "top_fc_damage": damage, "total_damage": damage * 2},
                     {"kind": kind or "event", "stage_id": stage_id or quest_id, "is_story": story,
                      "is_skip": skip, "effects_enabled": effects, "party_characters": members or []}, count)
    edit(context, update)


def mission(context, event_id, mission_id, **kwargs):
    category = GameplayCatalog(context.tables, context.schema).get("m_events", event_id)["m_mission_term_category_group"]
    response = call(context, f"api/event-mission/list/{category}", method="GET", **kwargs)
    return next(row for section in response.values() if isinstance(section, list) for row in section
                if row.get("m_mission_id") == mission_id)


def evaluator(context, event_id):
    state, progress = map(json.loads, snapshot(context))
    current = game(context, state, progress)
    return EventMissions(current, current.catalog.get("m_events", event_id))


def test_event_mission_scope_uses_real_term_groups_and_quest_ids(event_game):
    context = event_game
    for identifier in (300001, 300002, 500001, 500002):
        join(context, identifier)
    record(context, 300001, 4, stage_id=8)
    assert mission(context, 300001, 30000106)["value"] == 0
    record(context, 300001, 8, stage_id=12)
    assert mission(context, 300001, 30000106)["value"] == 1
    assert mission(context, 300002, 30000201)["value"] == 0
    assert 50000209 in evaluator(context, 500002).allowed_ids
    assert 50000209 not in evaluator(context, 500001).allowed_ids


def test_event_mission_reward_claim_atomic_scope_and_old_receipts(event_game):
    context = event_game
    join(context, 300001)
    record(context, 300001, 2)
    selected = {"category_group": 300001, "m_mission_ids": [30000101]}
    headers = {**context.headers, "X-Request-Id": "event-mission-list"}
    assert not mission(context, 300001, 30000101, headers=headers)["is_received"]
    badges = call(context, "api/quest/top")["receivable_event_missions"]
    assert {"m_event_id": 300001, "is_receivable_mission": 1} in badges
    response = call(context, "api/event-mission/reward/receive", selected)
    assert response["records"]
    assert mission(context, 300001, 30000101, headers=headers)["is_received"] == 1
    before = snapshot(context)
    call(context, "api/event-mission/reward/receive", selected)
    assert snapshot(context) == before
    call(context, "api/event-mission/reward/receive", {"category_group": 300001, "m_mission_ids": [30000201]}, expected=400)
    call(context, "api/event-mission/reward/receive", {"category_group": 300001, "m_mission_ids": [30000102, 30000103]}, expected=409)
    assert snapshot(context) == before
    edit(context, lambda _, progress: progress["mission_claims"].update({"once:30000103": []}))
    before = snapshot(context)
    call(context, "api/event-mission/reward/receive", {"category_group": 300001, "m_mission_ids": [30000103]})
    assert snapshot(context) == before


def test_event_daily_counter_resets_but_limited_progress_remains(event_game, monkeypatch):
    context = event_game
    join(context, 300001)
    record(context, 300001, 2, count=3)
    assert mission(context, 300001, 30000101)["value"] == 3
    assert mission(context, 300001, 30000103)["value"] == 3
    body = {"category_group": 300001, "m_mission_ids": [30000101]}
    call(context, "api/event-mission/reward/receive", body)
    clock(monkeypatch, NOW + timedelta(days=1))
    assert mission(context, 300001, 30000101)["value"] == 0
    assert mission(context, 300001, 30000103)["value"] == 3
    record(context, 300001, 2, now=NOW + timedelta(days=1))
    assert mission(context, 300001, 30000101)["value"] == 1
    call(context, "api/event-mission/reward/receive", body)
    claims = json.loads(snapshot(context)[1])["mission_claims"]
    assert f"{NOW.date().isoformat()}:30000101" in claims
    assert f"{(NOW + timedelta(days=1)).date().isoformat()}:30000101" in claims


def test_speedrun_total_requires_every_quest_and_skip_cannot_improve_time(event_game):
    context = event_game
    join(context, 1100001)
    record(context, 1100001, 1100001101, elapsed=90)
    record(context, 1100001, 1100001102, elapsed=90)
    assert mission(context, 1100001, 110000104)["value"] == 0
    record(context, 1100001, 1100001103, elapsed=110)
    assert mission(context, 1100001, 110000104)["value"] == 290
    record(context, 1100001, 1100001101, elapsed=0, skip=True)
    record(context, 1100001, 1100001101, elapsed=150)
    assert mission(context, 1100001, 110000101)["value"] == 90
    assert mission(context, 1100001, 110000104)["value"] == 290
    call(context, "api/event-mission/reward/receive", {"category_group": 110001, "m_mission_ids": [110000104]})


def test_speedrun_rarity_and_crest_use_departure_roster(event_game):
    context = event_game
    join(context, 1100001)
    characters = table(context, "m_characters")
    low = next(row["id"] for row in characters if row["rarity"] == 1)
    high = next(row["id"] for row in characters if row["rarity"] == 3)
    record(context, 1100001, 1100001201, elapsed=80, members=[low, high])
    assert mission(context, 1100001, 110000121)["value"] == 0
    record(context, 1100001, 1100001201, elapsed=85, members=[low])
    assert mission(context, 1100001, 110000121)["value"] == 85
    matched = [row for row in characters if row["mana_type"] == 2]
    first = matched[0]
    second = next(row for row in matched if row["element_type"] != first["element_type"])
    record(context, 1100001, 1100001301, elapsed=120, members=[first["id"], second["id"]])
    assert mission(context, 1100001, 110000112)["value"] == 120
    record(context, 1100001, 1100001301, elapsed=1, members=[])
    assert mission(context, 1100001, 110000112)["value"] == 120


def test_commission_fc_and_disabled_hunt_effect_require_actual_evidence(event_game):
    context = event_game
    join(context, 800001)
    join(context, 500002)
    record(context, 800001, 80000104, damage=50000)
    record(context, 800001, 80000104, damage=30000)
    assert mission(context, 800001, 80000105)["value"] == 50000
    record(context, 500002, 500002206, effects=1)
    record(context, 500002, 500002206, effects=0, skip=True)
    assert mission(context, 500002, 50000210)["value"] == 0
    record(context, 500002, 500002206, effects=0)
    assert mission(context, 500002, 50000210)["value"] == 1


def test_story_reads_do_not_count_as_event_battles_and_real_clear_updates_list(event_game):
    from dotabyss_ps.gameplay_event_quest_state import QUEST_KINDS

    context, kind = event_game, QUEST_KINDS["story"]
    join(context, 300001)
    novel = next(row for row in nodes(context, kind, 300001) if row["id"] == 2)
    complete(context, kind, novel)
    assert mission(context, 300001, 30000101)["value"] == 0
    battle = next(row for row in nodes(context, kind, 300001) if row["id"] == 6)
    complete(context, kind, battle)
    assert mission(context, 300001, 30000101)["value"] == 1


def test_event_mission_group_cannot_be_used_without_joining(event_game):
    context = event_game
    call(context, "api/event-mission/list/300001", method="GET", expected=409)
    call(context, "api/event-mission/reward/receive", {"category_group": 300001, "m_mission_ids": [30000101]}, expected=409)


@pytest.mark.parametrize("event_id,quest_id,mission_id,kind", [
    (100001, 100001101, 10000101, "event_miner"),
    (900001, 90000101, 90000101, "event_training"),
    (600002, 60000201, 160000201, "event_bonus"),
])
def test_other_event_counter_kinds_are_scoped_and_count_sweeps(event_game, event_id, quest_id, mission_id, kind):
    context = event_game
    join(context, event_id)
    record(context, event_id, quest_id, kind=kind, count=3, skip=True)
    assert mission(context, event_id, mission_id)["value"] == 3
