import copy
import json
import sqlite3
from pathlib import Path

import pytest

from dotabyss_ps.migrations import migrate_profile
from dotabyss_ps.newgame import NewGameError
from dotabyss_ps.plan_migration import migrate_plan_progress
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot


@pytest.fixture
def legacy_plans(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    state_text, progress_text = snapshot(c)
    state, progress = json.loads(state_text), json.loads(progress_text)
    state["policy_version"] = 4
    for row in state["records"]["t_plans"]["up"]:
        row["step"] += 1
    with c.store.connection() as db:
        db.execute("UPDATE game_states SET contents=? WHERE profile_id=?", (json.dumps(state), c.identity["user_id"]))
    c.profile_name = "local-test"
    return c


def migrate(c, *, apply=False, schema=True):
    return migrate_profile(c.store.path, c.profile_name, apply=apply,
                           schema_path=c.settings.new_game_schema if schema else None)


def replace(c, state, progress):
    with c.store.connection() as db:
        db.execute("UPDATE game_states SET contents=? WHERE profile_id=?", (json.dumps(state), c.identity["user_id"]))
        db.execute("UPDATE gameplay_progress SET contents=? WHERE profile_id=?", (json.dumps(progress), c.identity["user_id"]))


@pytest.mark.parametrize("completed", [0, 1])
def test_plan_migration_preserves_rewards_and_rewrites_replay_records_once(legacy_plans, completed):
    c = legacy_plans
    state, progress = map(json.loads, snapshot(c))
    plan = next(row for row in state["records"]["t_plans"]["up"] if row["m_plan_id"] == 1001)
    plan["step"] = completed + 1
    if completed:
        progress["mission_claims"]["once:200010010"] = [{"content_type": 50, "content_id": 0, "amount": 123}]
    progress["operation_receipts"]["plan-claim"] = {
        "digest": "a" * 64, "response": {"records": {"t_plans": {"up": [copy.deepcopy(plan)], "down": []}},
                                         "received_items": [{"content_type": 50, "content_id": 0, "amount": 123}]}}
    replace(c, state, progress)
    before = snapshot(c)
    preview = migrate(c)
    assert preview["status"] == "ready" and preview["progress_changed"]
    assert snapshot(c) == before and not (c.store.path.parent / "migration-backups").exists()
    result = migrate(c, apply=True)
    backup = json.loads(Path(result["backup"]).read_text())
    assert backup["game_state"]["contents"] == before[0]
    assert backup["gameplay_progress"]["contents"] == before[1]
    expected_state, expected_progress = copy.deepcopy(state), copy.deepcopy(progress)
    expected_state["policy_version"] = 5
    for row in expected_state["records"]["t_plans"]["up"]:
        row["step"] -= 1
    expected_progress["operation_receipts"]["plan-claim"]["response"]["records"]["t_plans"]["up"][0]["step"] -= 1
    assert tuple(map(json.loads, snapshot(c))) == (expected_state, expected_progress)
    after = snapshot(c)
    assert migrate(c, apply=True, schema=False)["status"] == "unchanged"
    assert snapshot(c) == after
    assert len(list((c.store.path.parent / "migration-backups").iterdir())) == 1
    current = call(c, "api/user")["records"]["t_plans"]["up"]
    assert next(row["step"] for row in current if row["m_plan_id"] == 1001) == completed


def test_plan_migration_without_gameplay_ledger_keeps_it_absent(legacy_plans):
    c = legacy_plans
    with c.store.connection() as db:
        db.execute("DELETE FROM gameplay_progress WHERE profile_id=?", (c.identity["user_id"],))
    result = migrate(c, apply=True)
    assert not result["progress_changed"] and result["progress_before_sha256"] is None
    assert snapshot(c)[1] is None
    assert all(row["step"] == 0 for row in json.loads(snapshot(c)[0])["records"]["t_plans"]["up"])


@pytest.mark.parametrize("damage", ["unsupported-step", "missing-claim", "claim-without-step", "cached-step", "periodic-claim", "master-binding"])
def test_ambiguous_plan_migration_never_changes_either_ledger(legacy_plans, damage):
    c = legacy_plans
    state, progress = map(json.loads, snapshot(c))
    plan = state["records"]["t_plans"]["up"][0]
    if damage == "unsupported-step":
        plan["step"] = 200
    elif damage == "missing-claim":
        plan["step"] = 2
    elif damage == "claim-without-step":
        progress["mission_claims"]["once:200010010"] = []
    elif damage == "periodic-claim":
        progress["mission_claims"]["2026-10-31:200010010"] = []
    elif damage == "cached-step":
        progress["operation_receipts"]["bad"] = {
            "digest": "b" * 64, "response": {"records": {"t_plans": {"up": [{**plan, "step": 2}], "down": []}}}}
    replace(c, state, progress)
    if damage == "master-binding":
        with c.store.connection() as db:
            db.execute("UPDATE gameplay_progress SET master_revision='other' WHERE profile_id=?", (c.identity["user_id"],))
    before = snapshot(c)
    with pytest.raises(NewGameError):
        migrate(c, apply=True)
    assert snapshot(c) == before
    assert not (c.store.path.parent / "migration-backups").exists()


def test_plan_migration_requires_the_exact_schema_and_blob(legacy_plans):
    c = legacy_plans
    before = snapshot(c)
    with pytest.raises(NewGameError, match="schema"):
        migrate(c, apply=True, schema=False)
    schema_file = c.settings.new_game_schema
    schema = json.loads(schema_file.read_text())
    schema["tables"]["m_plans"][0]["name"] = "wrong_id"
    schema_file.write_text(json.dumps(schema))
    with pytest.raises(NewGameError, match="schema"):
        migrate(c, apply=True)
    assert snapshot(c) == before
    assert not (c.store.path.parent / "migration-backups").exists()


def test_plan_and_cached_responses_commit_in_one_transaction(legacy_plans):
    c = legacy_plans
    state, progress = map(json.loads, snapshot(c))
    progress["operation_receipts"]["old"] = {
        "digest": "c" * 64, "response": {"records": {"t_plans": copy.deepcopy(state["records"]["t_plans"])}}}
    replace(c, state, progress)
    before = snapshot(c)
    with c.store.connection() as db:
        db.execute("CREATE TRIGGER reject_progress BEFORE UPDATE ON gameplay_progress BEGIN SELECT RAISE(ABORT,'stop migration'); END")
    with pytest.raises(sqlite3.IntegrityError):
        migrate(c, apply=True)
    assert snapshot(c) == before
    backups = list((c.store.path.parent / "migration-backups").iterdir())
    assert len(backups) == 1
    with c.store.connection() as db:
        db.execute("DROP TRIGGER reject_progress")
    result = migrate(c, apply=True)
    assert Path(result["backup"]) == backups[0]


def test_oldest_policy_can_migrate_through_all_edges(legacy_plans):
    c = legacy_plans
    state, progress = map(json.loads, snapshot(c))
    state["policy_version"] = 1
    for name in ("t_prologue_tutorials", "t_tavern_daily_cards", "t_idle_exploration_statuses"):
        state["records"][name]["up"] = []
    replace(c, state, progress)
    result = migrate(c, apply=True)
    assert [entry["to_policy"] for entry in result["actions"]] == [2, 3, 4, 5]
    assert call(c, "api/user")["records"]["t_plans"]["up"][0]["step"] == 0


def test_multistep_migration_preserves_all_claims_and_payment_replays():
    row = {"id": 7, "m_plan_id": 101, "step": 3}
    state = {"records": {"t_plans": {"up": [copy.deepcopy(row)], "down": []}}}
    progress = {"mission_claims": {"once:11": [], "once:12": []}, "features": {"shop": {"data": {
        "receipts": {"purchase": {"response": {"records": {"t_plans": {"up": [copy.deepcopy(row)], "down": []}}}, "payment_id": 5}}}}}}
    migrate_plan_progress(state, progress, {101: [11, 12, 13]})
    assert state["records"]["t_plans"]["up"][0]["step"] == 2
    receipt = progress["features"]["shop"]["data"]["receipts"]["purchase"]
    assert receipt["response"]["records"]["t_plans"]["up"][0]["step"] == 2
    assert receipt["payment_id"] == 5 and progress["mission_claims"] == {"once:11": [], "once:12": []}
    with pytest.raises(NewGameError, match="consecutive"):
        migrate_plan_progress({"records": {"t_plans": {"up": [row]}}}, {"mission_claims": {"once:12": []}}, {101: [11, 12, 13]})
