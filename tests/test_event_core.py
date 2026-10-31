import copy
import hashlib
import json

import msgpack

from dotabyss_ps.event_master import CLOSE_DATE, OPEN_DATE, reopened_tables
from dotabyss_ps.gameplay_records import put
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot
from test_training_encounter_modes import edit, prepare, records, table


def rows(tables, schema, name):
    fields = [column["name"] for column in schema["tables"][name]]
    return [dict(zip(fields, row)) for row in tables[name]]


def test_event_master_is_separate_and_does_not_change_source_or_save_binding(setup_gameplay):
    context = setup_gameplay
    base = context.store.master()
    version = context.client.get("/maintenance").json()["versions"]["resource"][0]
    assert version.startswith("events-") and version != base["revision"]
    response = context.client.get("/data/" + version)
    assert response.status_code == 200
    assert response.headers["etag"] == '"' + hashlib.sha256(response.content).hexdigest() + '"'
    derived = msgpack.unpackb(response.content, raw=False)
    assert all(row["open_at"] == OPEN_DATE and row["end_at"] == CLOSE_DATE for row in rows(derived, context.schema, "m_events"))
    source = context.client.get("/data/" + base["revision"])
    assert hashlib.sha256(source.content).hexdigest() == base["sha256"]
    assert context.store.master() == base
    assert context.client.get("/data/" + version, headers={"if-none-match": response.headers["etag"]}).status_code == 304
    call(context, "api/quest/top")
    before = snapshot(context)
    (context.settings.data_dir / "gameplay-rules.json").write_text(json.dumps({"event_open_all": 0}), encoding="utf-8")
    assert context.client.get("/maintenance").json()["versions"]["resource"] == [base["revision"]]
    assert snapshot(context) == before


def test_selected_event_dates_are_scoped_and_missing_mutant_metadata_is_isolated(gameplay_master):
    schema, tables = gameplay_master
    original = copy.deepcopy(tables)
    selected, changed = reopened_tables(tables, schema, {"event_open_all": 0, "event_open_ids": [300001]})
    assert tables == original
    old = {row["id"]: row for row in rows(tables, schema, "m_events")}
    for row in rows(selected, schema, "m_events"):
        assert row["open_at"] == (OPEN_DATE if row["id"] == 300001 else old[row["id"]]["open_at"])
    assert changed["m_events"] == 1
    derived, _ = reopened_tables(tables, schema, {"event_open_all": 1, "event_open_ids": []})
    restored = next(row for row in rows(derived, schema, "m_event_disasters") if row["m_event_id"] == 1050001)
    assert restored["element_type"] == 0 and restored["m_disaster_boss_id"] == 99001050001
    assert not any(row["m_disaster_boss_id"] == restored["m_disaster_boss_id"] for row in rows(derived, schema, "m_disaster_boss_appears"))
    assert derived["m_enemy_parameters"] == original["m_enemy_parameters"]


def test_open_dates_do_not_bypass_participation_or_reset_joined_progress(setup_gameplay):
    context = setup_gameplay
    before = snapshot(context)
    call(context, "api/event/join", {"m_event_id": 300001}, expected=409)
    assert snapshot(context) == before
    prepare(context, chapters=True)
    call(context, "api/event/join", {"m_event_id": 300001})
    call(context, "api/event/top", {"m_event_id": 300001})
    before = snapshot(context)
    call(context, "api/event/join", {"m_event_id": 300001})
    assert snapshot(context) == before
    assert len([row for row in records(context)["t_events"]["up"] if row["m_event_id"] == 300001]) == 1


def test_event_effect_upgrade_consumes_the_correct_currency_and_respects_assignment(setup_gameplay):
    context = setup_gameplay
    prepare(context, chapters=True)
    event = next(row for row in table(context, "m_events") if row["m_event_stage_effect_assignment_group_no"])
    call(context, "api/event/join", {"m_event_id": event["id"]})
    assignment = next(row for row in table(context, "m_event_stage_effect_assignments") if row["group_no"] == event["m_event_stage_effect_assignment_group_no"])
    group = assignment["m_event_stage_effect_group_no"]
    first = next(row for row in table(context, "m_event_stage_effects") if row["group_no"] == group and row["level"] == 1)
    costs = [row for row in table(context, "m_event_stage_effect_costs") if row["group_no"] == first["m_event_stage_effect_cost_group_no"]]
    assert costs and all(row["content_type"] == 54 for row in costs)
    edit(context, lambda state, _: [put(state, "t_event_currencies", {"m_event_currency_id": row["content_id"]}, {"amount": 10000}) for row in costs])
    call(context, "api/event/stage-effect/upgrade", {"m_event_id": event["id"], "m_event_stage_effect_group_no": group})
    data = records(context)
    assert next(row for row in data["t_event_stage_effect_levels"]["up"] if row["m_event_stage_effect_group_no"] == group)["level"] == 1
    assert next(row for row in data["t_event_currencies"]["up"] if row["m_event_currency_id"] == costs[0]["content_id"])["amount"] == 10000 - costs[0]["amount"]
    before = snapshot(context)
    call(context, "api/event/stage-effect/upgrade", {"m_event_id": event["id"], "m_event_stage_effect_group_no": 999999}, expected=400)
    assert snapshot(context) == before
