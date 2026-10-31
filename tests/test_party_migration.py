import copy
import json
import sqlite3
from pathlib import Path

import pytest

from dotabyss_ps import party_migration
from dotabyss_ps.newgame import NewGameError
from test_gameplay import call, gameplay_master, party_body, setup_gameplay, snapshot
from test_localstate import login

TABLE = "t_exploration_party_characters"


def replace(c, state, progress):
    with c.store.connection() as db:
        db.execute("UPDATE game_states SET contents=? WHERE profile_id=?", (json.dumps(state), c.identity["user_id"]))
        db.execute("UPDATE gameplay_progress SET contents=? WHERE profile_id=?", (json.dumps(progress), c.identity["user_id"]))


@pytest.fixture
def legacy_party(setup_gameplay):
    c = setup_gameplay
    call(c, "api/party/exploration/edit", party_body())
    state, progress = map(json.loads, snapshot(c))
    empty = {**state["records"][TABLE]["up"][0], "id": 99, "position_no": 10, "t_character_id": 0}
    state["records"][TABLE]["up"].append(empty)
    progress["operation_receipts"]["old-party"] = {"digest": "a" * 64, "response": {
        "records": {TABLE: {"up": copy.deepcopy(state["records"][TABLE]["up"]), "down": [700]}},
        TABLE: [{**empty, "id": 501}], "received_items": [{"content_type": 50, "content_id": 0, "amount": 7}]}}
    progress["features"]["shop"] = {"version": 1, "data": {"receipts": {"old": {"response": {
        TABLE: {"up": [{**empty, "id": 1}], "down": [1]}}}}}}
    replace(c, state, progress)
    return c


def clean(c, *, apply=False):
    return party_migration.clean_profile(c.store.path, "local-test", apply=apply)


def test_cleanup_preserves_members_rewards_and_other_profiles_and_never_reuses_ids(legacy_party):
    c = legacy_party
    other, headers = login(c.client, "other-player")
    call(c, "api/user", headers=headers)
    with c.store.connection() as db:
        other_before = db.execute("SELECT contents FROM game_states WHERE profile_id=?", (other["user_id"],)).fetchone()[0]
    before = snapshot(c)
    preview = clean(c)
    assert preview["status"] == "ready" and preview["removed_record_ids"] == [99]
    assert snapshot(c) == before
    assert not (c.store.path.parent / "migration-backups").exists()
    applied = clean(c, apply=True)
    backup = json.loads(Path(applied["backup"]).read_text())
    assert backup["game_state"]["contents"] == before[0]
    assert backup["gameplay_progress"]["contents"] == before[1]
    state, progress = map(json.loads, snapshot(c))
    original = json.loads(before[0])
    original["records"][TABLE]["up"] = [r for r in original["records"][TABLE]["up"] if r["t_character_id"]]
    assert state == original
    assert applied["next_member_id"] == 701
    receipt = progress["operation_receipts"]["old-party"]
    assert receipt["digest"] == "a" * 64
    assert receipt["response"]["received_items"] == [{"content_type": 50, "content_id": 0, "amount": 7}]
    assert receipt["response"][TABLE] == []
    assert receipt["response"]["records"][TABLE]["down"] == [99, 700]
    assert progress["features"]["shop"]["data"]["receipts"]["old"]["response"][TABLE] == {"up": [], "down": []}
    with c.store.connection() as db:
        assert db.execute("SELECT contents FROM game_states WHERE profile_id=?", (other["user_id"],)).fetchone()[0] == other_before
    after = snapshot(c)
    assert clean(c, apply=True)["status"] == "unchanged"
    assert snapshot(c) == after
    body = party_body()
    body["characters"][-1]["position_no"] = 10
    response = call(c, "api/party/exploration/edit", body)
    assert next(r for r in response["records"][TABLE]["up"] if r["position_no"] == 10)["id"] >= 701


@pytest.mark.parametrize("damage", ["gear", "owner", "parent", "position", "boolean", "missing-field", "cached-gear"])
def test_ambiguous_empty_rows_are_rejected_without_changing_data(legacy_party, damage):
    c = legacy_party
    state, progress = map(json.loads, snapshot(c))
    row = state["records"][TABLE]["up"][-1]
    if damage == "gear":
        row["t_weapon_id"] = 1
    elif damage == "owner":
        row["user_id"] += 1
    elif damage == "parent":
        row["t_exploration_party_id"] = 999
    elif damage == "position":
        row["position_no"] = 1
    elif damage == "boolean":
        row["t_character_id"] = False
    elif damage == "missing-field":
        del row["party_type"]
    else:
        progress["operation_receipts"]["old-party"]["response"][TABLE][0]["t_armor_id"] = 1
    replace(c, state, progress)
    before = snapshot(c)
    with pytest.raises(NewGameError):
        clean(c, apply=True)
    assert snapshot(c) == before
    assert not (c.store.path.parent / "migration-backups").exists()


def test_cleanup_rolls_back_both_tables_when_progress_update_fails(legacy_party):
    c = legacy_party
    before = snapshot(c)
    with c.store.connection() as db:
        db.execute("CREATE TRIGGER reject_party_progress BEFORE UPDATE ON gameplay_progress BEGIN SELECT RAISE(ABORT,'stop'); END")
    with pytest.raises(sqlite3.IntegrityError):
        clean(c, apply=True)
    assert snapshot(c) == before
    backups = list((c.store.path.parent / "migration-backups").iterdir())
    assert len(backups) == 1
    with c.store.connection() as db:
        db.execute("DROP TRIGGER reject_party_progress")
    assert clean(c, apply=True)["backup"] == str(backups[0])


def test_missing_ledger_gets_id_reservation_without_regranting_welcome_supplies(legacy_party):
    c = legacy_party
    state = json.loads(snapshot(c)[0])
    before = state["records"]["t_statuses"]["up"]
    with c.store.connection() as db:
        db.execute("DELETE FROM gameplay_progress WHERE profile_id=?", (c.identity["user_id"],))
    assert clean(c, apply=True)["next_member_id"] == 100
    progress = json.loads(snapshot(c)[1])
    assert progress["login_days"] == []
    assert progress["features"]["presents"]["data"] == {"seeded": True, "entries": []}
    assert call(c, "api/user")["records"]["t_statuses"]["up"] == before


def test_cleanup_preserves_legacy_policy_and_checks_master_binding(legacy_party):
    c = legacy_party
    state, progress = map(json.loads, snapshot(c))
    state["policy_version"] = 1
    for plan in state["records"]["t_plans"]["up"]:
        plan["step"] += 1
    replace(c, state, progress)
    clean(c, apply=True)
    assert json.loads(snapshot(c)[0])["policy_version"] == 1
    with c.store.connection() as db:
        db.execute("UPDATE settings SET value='wrong-master' WHERE name='active_master'")
    before = snapshot(c)
    with pytest.raises(NewGameError, match="master"):
        clean(c, apply=True)
    assert snapshot(c) == before
