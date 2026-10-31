import copy
import hashlib
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import msgpack
import pytest

from dotabyss_ps import migrations
from dotabyss_ps.newgame import NewGameError
from test_localstate import game, local_setup, login, replace_state, saved
from test_newgame import PC_CLIENT, PLAY_CLIENT


@pytest.fixture
def migration_setup(local_setup):
    context = local_setup
    identity, headers = login(context.client, "migrate-me")
    game(context.client, headers)
    state = json.loads(saved(context.store, identity["user_id"])["contents"])
    state["policy_version"] = 1
    for plan in state["records"]["t_plans"]["up"]:
        plan["step"] += 1
    state["records"]["t_prologue_tutorials"]["up"] = []
    state["records"]["t_tavern_daily_cards"]["up"] = []
    state["records"]["t_idle_exploration_statuses"]["up"] = []
    state["records"]["t_statuses"]["up"][0]["gold"] = 17
    replace_state(context.store, identity["user_id"], state)
    context.identity, context.headers = identity, headers
    return context


def run(context, *, apply=False, target_policy=3):
    return migrations.migrate_profile(context.store.path, "migrate-me", apply=apply, target_policy=target_policy)


def dormant_tavern(profile_id):
    return {"id": 1, "user_id": profile_id, "day_of_week": 0, "day": 0, "use_ticket": 0, "work_seed": 0,
            "worked_count": 0, "selected_count": 0, "score": 0, "selected_result": "",
            "selected_cards": {"selected_cards": [], "use_cards": [], "selected_character_cards": [], "use_character_cards": []}}


def dormant_exploration(profile_id):
    return {"id": 1, "user_id": profile_id, "level": 1, "point": 0, "explorable_duration": 43200,
            "exploring_state": 0, "exploring_start_at": None, "has_rare_drop": 0, "stacked_treasure": 0}


def test_preview_and_read_requests_do_not_migrate_legacy_state(migration_setup):
    context = migration_setup
    before = saved(context.store, context.identity["user_id"])
    preview = run(context)
    assert preview["status"] == "ready" and preview["from_policy"] == 1 and preview["to_policy"] == 3
    assert preview["actions"] == [{"from_policy": 1, "to_policy": 2, "action": "add_completed_tutorial"},
                                  {"from_policy": 2, "to_policy": 3, "action": "add_dormant_tavern"}]
    for route in ("/api/user", "/api/home", "/api/mypage/login"):
        response = context.client.get(route, headers=context.headers)
        assert response.status_code == 409
        assert "migration" in response.json()["errors"]["message"]
    assert saved(context.store, context.identity["user_id"]) == before
    assert not (context.store.path.parent / "migration-backups").exists()


@pytest.mark.parametrize("local_setup", [None, PLAY_CLIENT, PC_CLIENT], indirect=True, ids=["fanza", "play", "pc"])
def test_migration_backs_up_original_and_preserves_other_records_and_profiles(migration_setup):
    context = migration_setup
    other, headers = login(context.client, "other-player")
    game(context.client, headers)
    other_before = saved(context.store, other["user_id"])
    before = saved(context.store, context.identity["user_id"])
    result = run(context, apply=True, target_policy=4)
    assert result["status"] == "applied"
    backup = json.loads(Path(result["backup"]).read_text(encoding="utf-8"))
    assert backup["game_state"] == before
    assert backup["before_sha256"] == hashlib.sha256(before["contents"].encode()).hexdigest()
    after = saved(context.store, context.identity["user_id"])
    expected = json.loads(before["contents"])
    expected["policy_version"] = 4
    expected["records"]["t_prologue_tutorials"]["up"] = [{"id": 1, "user_id": context.identity["user_id"], "status": 99}]
    expected["records"]["t_tavern_daily_cards"]["up"] = [dormant_tavern(context.identity["user_id"])]
    expected["records"]["t_idle_exploration_statuses"]["up"] = [dormant_exploration(context.identity["user_id"])]
    assert json.loads(after["contents"]) == expected
    assert {key: value for key, value in after.items() if key != "contents"} == {key: value for key, value in before.items() if key != "contents"}
    assert saved(context.store, other["user_id"]) == other_before
    assert result["after_sha256"] == hashlib.sha256(after["contents"].encode()).hexdigest()
    for route in ("/api/user", "/api/home", "/api/mypage/login"):
        for accept in ("application/json", "application/x-msgpack"):
            response = context.client.get(route, headers={**context.headers, "accept": accept})
            assert response.status_code == 409
            body = response.json() if accept.endswith("json") else msgpack.unpackb(response.content, raw=False)
            assert "migration" in body["errors"]["message"]
    assert saved(context.store, context.identity["user_id"]) == after


@pytest.mark.parametrize("status", [*range(16), 99])
def test_existing_tutorial_progress_is_never_overwritten(migration_setup, status):
    context = migration_setup
    state = json.loads(saved(context.store, context.identity["user_id"])["contents"])
    state["records"]["t_prologue_tutorials"]["up"] = [{"id": 81, "user_id": context.identity["user_id"], "status": status}]
    replace_state(context.store, context.identity["user_id"], state)
    assert run(context, apply=True)["actions"][0]["action"] == "preserve_tutorial_progress"
    state["policy_version"] = 3
    state["records"]["t_tavern_daily_cards"]["up"] = [dormant_tavern(context.identity["user_id"])]
    assert json.loads(saved(context.store, context.identity["user_id"])["contents"]) == state


def test_rerun_is_idempotent_and_does_not_overwrite_backup(migration_setup):
    context = migration_setup
    result = run(context, apply=True)
    before = saved(context.store, context.identity["user_id"])
    backup = Path(result["backup"])
    original_bytes, original_time = backup.read_bytes(), backup.stat().st_mtime_ns
    assert run(context, apply=True)["status"] == "unchanged"
    assert run(context)["status"] == "unchanged"
    assert saved(context.store, context.identity["user_id"]) == before
    assert backup.read_bytes() == original_bytes and backup.stat().st_mtime_ns == original_time
    assert len(list(backup.parent.iterdir())) == 1


@pytest.mark.parametrize("damage", ["owner", "tutorial-owner", "status", "duplicate", "policy", "schema", "master", "runtime", "client"])
def test_invalid_state_or_binding_never_writes_backup_or_changes_save(migration_setup, damage):
    context = migration_setup
    state = json.loads(saved(context.store, context.identity["user_id"])["contents"])
    if damage == "owner":
        for container in state["records"].values():
            for row in container["up"]:
                if "user_id" in row:
                    row["user_id"] += 10
    elif damage in ("tutorial-owner", "status", "duplicate"):
        row = {"id": 1, "user_id": context.identity["user_id"], "status": 99}
        state["records"]["t_prologue_tutorials"]["up"] = [row]
        if damage == "tutorial-owner":
            row["user_id"] += 10
        elif damage == "status":
            row["status"] = 98
        else:
            state["records"]["t_prologue_tutorials"]["up"].append({**row, "id": 2})
    elif damage == "policy":
        state["policy_version"] = 9
    replace_state(context.store, context.identity["user_id"], state)
    with context.store.connection() as db:
        if damage == "schema":
            db.execute("UPDATE game_states SET schema_version=9")
        elif damage == "master":
            db.execute("UPDATE settings SET value='other' WHERE name='active_master'")
        elif damage == "runtime":
            db.execute("UPDATE settings SET value='{}' WHERE name='runtime'")
        elif damage == "client":
            metadata = copy.deepcopy(context.record)
            metadata["client"]["version"] = "unverified"
            db.execute("UPDATE master_datasets SET metadata=?", (json.dumps(metadata),))
    before = saved(context.store, context.identity["user_id"])
    with pytest.raises(NewGameError):
        run(context, apply=True)
    assert saved(context.store, context.identity["user_id"]) == before
    assert not (context.store.path.parent / "migration-backups").exists()


def test_backup_failure_leaves_state_unchanged(migration_setup, monkeypatch):
    context = migration_setup
    before = saved(context.store, context.identity["user_id"])

    def fail(*_):
        raise OSError("Backup failed")

    monkeypatch.setattr(migrations.os, "fsync", fail)
    with pytest.raises(OSError):
        run(context, apply=True)
    assert saved(context.store, context.identity["user_id"]) == before
    with pytest.raises(OSError):
        run(context, apply=True)
    assert saved(context.store, context.identity["user_id"]) == before


def test_future_policy_does_not_silently_retarget_this_migration(migration_setup, monkeypatch):
    context = migration_setup
    before = saved(context.store, context.identity["user_id"])
    monkeypatch.setattr(migrations, "POLICY_VERSION", 6)
    with pytest.raises(NewGameError, match="reviewed"):
        run(context, apply=True)
    assert saved(context.store, context.identity["user_id"]) == before
    assert not (context.store.path.parent / "migration-backups").exists()


@pytest.mark.parametrize("source_policy", [1, 2])
def test_failed_update_retains_backup_and_rerun_can_reuse_it(migration_setup, source_policy):
    context = migration_setup
    if source_policy == 2:
        run(context, apply=True, target_policy=2)
    before = saved(context.store, context.identity["user_id"])
    with context.store.connection() as db:
        db.execute("CREATE TRIGGER reject_migration BEFORE UPDATE ON game_states BEGIN SELECT RAISE(ABORT,'blocked test update'); END")
    with pytest.raises(sqlite3.IntegrityError):
        run(context, apply=True)
    assert saved(context.store, context.identity["user_id"]) == before
    backup = next((context.store.path.parent / "migration-backups").glob(f"policy-{source_policy}-to-3-*.json"))
    original_bytes = backup.read_bytes()
    assert json.loads(original_bytes)["game_state"] == before
    with context.store.connection() as db:
        db.execute("DROP TRIGGER reject_migration")
    assert run(context, apply=True)["backup"] == str(backup)
    assert backup.read_bytes() == original_bytes


def test_conflicting_backup_cannot_be_overwritten(migration_setup):
    context = migration_setup
    before = saved(context.store, context.identity["user_id"])
    digest = hashlib.sha256(before["contents"].encode()).hexdigest()
    path = context.store.path.parent / "migration-backups" / f"policy-1-to-3-profile-{context.identity['user_id']}-{digest}.json"
    path.parent.mkdir()
    path.write_text("{}", encoding="utf-8")
    with pytest.raises(NewGameError, match="backup"):
        run(context, apply=True)
    assert saved(context.store, context.identity["user_id"]) == before and path.read_text() == "{}"


def test_reader_sees_original_until_backed_up_transaction_commits(migration_setup, monkeypatch):
    context = migration_setup
    before = saved(context.store, context.identity["user_id"])
    backed_up, release = threading.Event(), threading.Event()
    original = migrations._write_backup

    def pause(*args):
        original(*args)
        backed_up.set()
        assert release.wait(timeout=5)

    monkeypatch.setattr(migrations, "_write_backup", pause)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run, context, apply=True)
        assert backed_up.wait(timeout=5)
        second = pool.submit(run, context, apply=True)
        try:
            assert saved(context.store, context.identity["user_id"]) == before
        finally:
            release.set()
        assert first.result(timeout=5)["status"] == "applied"
        assert second.result(timeout=5)["status"] == "unchanged"
    assert len(list((context.store.path.parent / "migration-backups").iterdir())) == 1


def test_explicit_edges_keep_separate_backups_and_only_add_the_missing_tavern(migration_setup):
    context = migration_setup
    first = run(context, apply=True, target_policy=2)
    assert first["actions"] == [{"from_policy": 1, "to_policy": 2, "action": "add_completed_tutorial"}]
    original_backup = Path(first["backup"]).read_bytes()
    before = saved(context.store, context.identity["user_id"])
    state = json.loads(before["contents"])
    assert state["policy_version"] == 2 and state["records"]["t_tavern_daily_cards"]["up"] == []
    assert context.client.get("/api/home", headers=context.headers).status_code == 409
    preview = run(context)
    assert preview["actions"] == [{"from_policy": 2, "to_policy": 3, "action": "add_dormant_tavern"}]
    second = run(context, apply=True)
    assert json.loads(Path(second["backup"]).read_bytes())["game_state"] == before
    state["policy_version"] = 3
    state["records"]["t_tavern_daily_cards"]["up"] = [dormant_tavern(context.identity["user_id"])]
    after = saved(context.store, context.identity["user_id"])
    assert json.loads(after["contents"]) == state
    assert run(context, apply=True)["status"] == "unchanged"
    assert saved(context.store, context.identity["user_id"]) == after
    assert Path(first["backup"]).read_bytes() == original_backup
    assert len(list(Path(first["backup"]).parent.iterdir())) == 2
    with pytest.raises(NewGameError, match="downgrades"):
        run(context, apply=True, target_policy=2)


def test_existing_tavern_progress_is_preserved_without_reinterpreting_date_or_results(migration_setup):
    context = migration_setup
    run(context, apply=True, target_policy=2)
    state = json.loads(saved(context.store, context.identity["user_id"])["contents"])
    row = dormant_tavern(context.identity["user_id"])
    row.update(id=81, day=17, day_of_week=3, use_ticket=2, work_seed=-12345, worked_count=3,
               selected_count=2, score=21, selected_result='{"preserved":true}')
    row["selected_cards"] = {
        "selected_cards": [{"turn": 1, "m_tavern_card_id": 1}],
        "use_cards": [{"turn": 2, "m_tavern_card_id": 2}],
        "selected_character_cards": [{"turn": 3, "m_character_id": 1200016}],
        "use_character_cards": [{"turn": 4, "m_character_id": 1200002}],
    }
    state["records"]["t_tavern_daily_cards"]["up"] = [row]
    replace_state(context.store, context.identity["user_id"], state)
    assert run(context, apply=True)["action"] == "preserve_tavern_progress"
    state["policy_version"] = 3
    assert json.loads(saved(context.store, context.identity["user_id"])["contents"]) == state


@pytest.mark.parametrize("damage", [
    lambda row: row.update(id=2 ** 63),
    lambda row: row.update(user_id=999),
    lambda row: row.update(score=2 ** 31),
    lambda row: row.update(work_seed=-(2 ** 31) - 1),
    lambda row: row.update(worked_count=True),
    lambda row: row.update(selected_result={}),
    lambda row: row["selected_cards"].pop("use_cards"),
    lambda row: row["selected_cards"].update(use_cards={}),
    lambda row: row["selected_cards"].update(use_cards=[{"turn": 1, "m_tavern_card_id": 1, "extra": 1}]),
    lambda row: row["selected_cards"].update(use_cards=[{"turn": True, "m_tavern_card_id": 1}]),
    lambda row: row["selected_cards"].update(use_cards=[{"turn": 2 ** 31, "m_tavern_card_id": 1}]),
    lambda row: row["selected_cards"].update(use_cards=[{"turn": 1, "m_tavern_card_id": 0}]),
    lambda row: row["selected_cards"].update(use_cards=[{"turn": 1, "m_tavern_card_id": 2 ** 63}]),
    lambda row: row["selected_cards"].update(use_cards=[{"turn": 1, "m_tavern_card_id": 1}] * 10001),
])
def test_invalid_tavern_rows_are_not_overwritten_or_backed_up(migration_setup, damage):
    context = migration_setup
    state = json.loads(saved(context.store, context.identity["user_id"])["contents"])
    row = dormant_tavern(context.identity["user_id"])
    damage(row)
    state["records"]["t_tavern_daily_cards"]["up"] = [row]
    replace_state(context.store, context.identity["user_id"], state)
    before = saved(context.store, context.identity["user_id"])
    with pytest.raises(NewGameError):
        run(context, apply=True)
    assert saved(context.store, context.identity["user_id"]) == before
    assert not (context.store.path.parent / "migration-backups").exists()


def test_missing_registered_edge_cannot_commit_a_partial_chain(migration_setup, monkeypatch):
    context = migration_setup
    before = saved(context.store, context.identity["user_id"])
    monkeypatch.delitem(migrations.MIGRATION_STEPS, (2, 3))
    with pytest.raises(NewGameError, match="registered migration"):
        run(context, apply=True)
    assert saved(context.store, context.identity["user_id"]) == before
    assert not (context.store.path.parent / "migration-backups").exists()


@pytest.mark.parametrize("target", [1, 6, True])
def test_unregistered_target_policy_is_rejected(migration_setup, target):
    context = migration_setup
    before = saved(context.store, context.identity["user_id"])
    with pytest.raises(NewGameError, match="target policy"):
        run(context, apply=True, target_policy=target)
    assert saved(context.store, context.identity["user_id"]) == before


@pytest.mark.parametrize("local_setup", [None, PLAY_CLIENT, PC_CLIENT], indirect=True, ids=["fanza", "play", "pc"])
def test_idle_status_migration_matches_captured_inactive_contract(migration_setup):
    context = migration_setup
    run(context, apply=True, target_policy=3)
    before = saved(context.store, context.identity["user_id"])
    assert context.client.get("/api/home", headers=context.headers).status_code == 409
    preview = migrations.migrate_profile(context.store.path, "migrate-me", target_policy=4)
    assert preview["actions"] == [{"from_policy": 3, "to_policy": 4, "action": "add_dormant_idle_exploration"}]
    assert saved(context.store, context.identity["user_id"]) == before
    result = migrations.migrate_profile(context.store.path, "migrate-me", apply=True, target_policy=4)
    assert json.loads(Path(result["backup"]).read_text())["game_state"] == before
    expected = json.loads(before["contents"])
    expected["policy_version"] = 4
    expected["records"]["t_idle_exploration_statuses"]["up"] = [dormant_exploration(context.identity["user_id"])]
    after = saved(context.store, context.identity["user_id"])
    assert json.loads(after["contents"]) == expected
    assert migrations.migrate_profile(context.store.path, "migrate-me", apply=True, target_policy=4)["status"] == "unchanged"
    assert saved(context.store, context.identity["user_id"]) == after
    for route in ("/api/user", "/api/home", "/api/mypage/login"):
        for accept in ("application/json", "application/x-msgpack"):
            response = context.client.get(route, headers={**context.headers, "accept": accept})
            assert response.status_code == 409
            body = response.json() if accept.endswith("json") else msgpack.unpackb(response.content, raw=False)
            assert "migration" in body["errors"]["message"]


def test_existing_idle_progress_is_preserved(migration_setup):
    context = migration_setup
    run(context, apply=True, target_policy=3)
    state = json.loads(saved(context.store, context.identity["user_id"])["contents"])
    row = dormant_exploration(context.identity["user_id"])
    row.update(id=91, level=3, point=500, exploring_state=1, exploring_start_at="2026-10-31T12:59:40+09:00",
               has_rare_drop=1, stacked_treasure=7)
    state["records"]["t_idle_exploration_statuses"]["up"] = [row]
    replace_state(context.store, context.identity["user_id"], state)
    result = migrations.migrate_profile(context.store.path, "migrate-me", apply=True, target_policy=4)
    assert result["action"] == "preserve_idle_exploration_progress"
    state["policy_version"] = 4
    assert json.loads(saved(context.store, context.identity["user_id"])["contents"]) == state


@pytest.mark.parametrize("field,value", [("user_id", 999), ("level", 0), ("explorable_duration", 0),
                                        ("has_rare_drop", 2), ("point", True), ("point", 2 ** 31),
                                        ("exploring_start_at", {}), ("exploring_start_at", "invalid")])
def test_invalid_idle_progress_is_not_repaired_or_written(migration_setup, field, value):
    context = migration_setup
    state = json.loads(saved(context.store, context.identity["user_id"])["contents"])
    row = dormant_exploration(context.identity["user_id"])
    row[field] = value
    state["records"]["t_idle_exploration_statuses"]["up"] = [row]
    replace_state(context.store, context.identity["user_id"], state)
    before = saved(context.store, context.identity["user_id"])
    with pytest.raises(NewGameError):
        migrations.migrate_profile(context.store.path, "migrate-me", apply=True, target_policy=4)
    assert saved(context.store, context.identity["user_id"]) == before
    assert not (context.store.path.parent / "migration-backups").exists()
