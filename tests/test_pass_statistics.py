import copy
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from dotabyss_ps.gameplay_catalog import GameplayError, JST
from dotabyss_ps.gameplay_missions import Missions
from dotabyss_ps.gameplay_pass_statistics import claim_key, prepare, value, window
from dotabyss_ps.gameplay_statistics import bucket, record


def stamp(text):
    return datetime.fromisoformat(text).replace(tzinfo=JST)


@pytest.fixture
def context():
    definition = {"id": 202611, "start_at": "2026-10-31 10:00:00", "end_at": "2026-11-30 04:59:59"}
    definitions = [definition, {"id": 202612, "start_at": "2026-11-30 10:00:00", "end_at": "2026-12-31 04:59:59"}]
    monthly = {"id": 50052, "mission_type": 5, "m_mission_pass_id": 202611, "situation_type": 150100,
               "target_id": 0, "sub_target_id": 0, "require_value": 30, "m_mission_term_id": 0, "require_mission_id": 0}
    weekly = {**monthly, "id": 60052, "mission_type": 6, "require_value": 5}
    future = {**monthly, "id": 50152, "m_mission_pass_id": 202612}
    tables = {"m_mission_passes": definitions, "m_missions": [monthly, weekly, future], "m_mission_terms": []}
    catalog = SimpleNamespace(tables=tables, rows=lambda name: tables[name],
                              get=lambda name, identifier: next(row for row in tables[name] if row["id"] == identifier))
    return SimpleNamespace(state={"records": {}}, progress={"features": {}, "mission_claims": {}, "login_days": []},
                           catalog=catalog, now=stamp("2026-11-01T12:00:00"), user_id=4, rules={},
                           definition=definition, monthly=monthly, weekly=weekly, future=future)


def test_old_statistics_only_seed_wholly_owned_dates_once(context):
    g = context
    record(g.progress, stamp("2026-10-01T12:00:00"), 150100, 30)
    record(g.progress, stamp("2026-10-31T12:00:00"), 150100, 9)
    record(g.progress, stamp("2026-11-01T08:00:00"), 150100, 7)
    old = copy.deepcopy(bucket(g.progress))
    prepare(g)
    assert value(g, g.monthly) == value(g, g.weekly) == 7
    prepare(g)
    assert value(g, g.monthly) == 7
    assert bucket(g.progress) == old
    g.progress = json.loads(json.dumps(g.progress))
    record(g.progress, g.now, 150100, 2)
    assert value(g, g.monthly) == value(g, g.weekly) == 9


def test_new_counts_use_exact_pass_start_and_end(context):
    g = context
    g.now = stamp("2026-10-31T09:59:59")
    prepare(g)
    record(g.progress, g.now, 150100, 5)
    assert value(g, g.monthly) is None
    g.now += timedelta(seconds=1)
    prepare(g)
    record(g.progress, g.now, 150100, 2)
    assert value(g, g.monthly) == 2
    g.now = stamp("2026-11-30T04:59:59")
    prepare(g)
    record(g.progress, g.now, 150100)
    assert value(g, g.monthly) == 3
    g.now += timedelta(microseconds=1)
    record(g.progress, g.now, 150100, 10)
    assert value(g, g.monthly) is None
    assert not Missions(g.state, g.progress, g.catalog, g.now, g.user_id, g.rules).active(g.monthly)


def test_weekly_counts_reset_on_pass_weekday_at_five(context):
    g = context
    prepare(g)
    record(g.progress, g.now, 150100, 3)
    g.now = stamp("2026-11-02T05:00:00")
    prepare(g)
    assert value(g, g.weekly) == 3
    g.now = stamp("2026-11-07T04:59:59.999999")
    prepare(g)
    record(g.progress, g.now, 150100)
    assert value(g, g.weekly) == 4
    assert claim_key(g.weekly, g.definition, g.now) == "2026-10-31:60052"
    g.now += timedelta(microseconds=1)
    prepare(g)
    assert value(g, g.weekly) is None
    record(g.progress, g.now, 150100)
    assert value(g, g.weekly) == 1
    assert value(g, g.monthly) == 5
    assert claim_key(g.weekly, g.definition, g.now) == "2026-11-07:60052"


def test_next_pass_does_not_inherit_previous_pass_counts(context):
    g = context
    prepare(g)
    record(g.progress, g.now, 150100, 40)
    g.now = stamp("2026-11-30T10:00:00")
    prepare(g)
    assert value(g, g.future) is None
    record(g.progress, g.now, 150100, 2)
    assert value(g, g.future) == 2


def test_legacy_weekly_receipt_is_migrated_only_to_current_period(context):
    g = context
    g.now = stamp("2026-11-03T12:00:00")
    rewards = [{"content_type": 181, "content_id": 202611, "amount": 100}]
    g.progress["mission_claims"]["2026-W45:60052"] = rewards
    prepare(g)
    assert g.progress["mission_claims"]["2026-10-31:60052"] == rewards
    assert g.progress["mission_claims"]["2026-W45:60052"] == rewards
    g.now = stamp("2026-11-07T05:00:00")
    prepare(g)
    assert "2026-11-07:60052" not in g.progress["mission_claims"]


def test_clock_rollback_cannot_reopen_a_legacy_paid_week(context):
    g = context
    g.now = stamp("2026-11-21T12:00:00")
    g.progress["mission_claims"]["2026-W45:60052"] = [{"content_type": 181, "content_id": 202611, "amount": 100}]
    prepare(g)
    assert "2026-10-31:60052" not in g.progress["mission_claims"]
    g.now = stamp("2026-11-03T12:00:00")
    prepare(g)
    assert "2026-10-31:60052" in g.progress["mission_claims"]


def test_mission_term_narrows_pass_statistics(context):
    g = context
    g.monthly["m_mission_term_id"] = 7
    g.catalog.tables["m_mission_terms"].append({"id": 7, "start_at": "2026-11-02 10:00:00", "end_at": "2026-11-03 10:00:00"})
    prepare(g)
    record(g.progress, g.now, 150100, 50)
    assert value(g, g.monthly) is None
    g.now = stamp("2026-11-02T10:00:00")
    prepare(g)
    record(g.progress, g.now, 150100, 2)
    assert value(g, g.monthly) == 2
    assert value(g, g.weekly) == 52


def test_unrelated_statistics_and_monthly_claim_keys_are_unchanged(context):
    g = context
    prepare(g)
    record(g.progress, g.now, 60000, 17)
    assert bucket(g.progress)["totals"]["60000:0:0"] == 17
    assert value(g, g.monthly) is None
    assert claim_key(g.monthly, g.definition, g.now) == "once:50052"


@pytest.mark.parametrize("kind,now,expected_start,expected_end", [
    (6, "2026-10-31T12:00:00", "2026-10-31T10:00:00", "2026-11-07T04:59:59.999999"),
    (6, "2026-11-28T05:00:00", "2026-11-28T05:00:00", "2026-11-30T04:59:59"),
    (5, "2026-11-15T00:00:00", "2026-10-31T10:00:00", "2026-11-30T04:59:59"),
])
def test_native_period_boundaries(context, kind, now, expected_start, expected_end):
    assert window(context.definition, kind, stamp(now)) == (stamp(expected_start), stamp(expected_end))


def test_corrupt_pass_counters_are_rejected(context):
    g = context
    prepare(g)
    period = next(iter(g.progress["features"]["missions"]["data"]["pass_statistics"]["periods"].values()))
    period["values"]["150100:0:0"] = -1
    with pytest.raises(GameplayError):
        prepare(g)
