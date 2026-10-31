import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from dotabyss_ps.admin_profiles import ProfileError, ProfileManager
from dotabyss_ps.storage import Store
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot


def manager(context):
    return ProfileManager(context.store, context.client.app.state.admin.local)


def saved(context, profile_id):
    with context.store.connection() as db:
        state = json.loads(db.execute("SELECT contents FROM game_states WHERE profile_id=?", (profile_id,)).fetchone()[0])
        progress = json.loads(db.execute("SELECT contents FROM gameplay_progress WHERE profile_id=?", (profile_id,)).fetchone()[0])
    return state, progress


def counts(context):
    with context.store.connection() as db:
        return {table: db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0]
                for table in ("profiles", "sessions", "game_states", "gameplay_progress", "profile_labels", "profile_create_receipts")}


def test_create_is_fresh_guided_atomic_and_does_not_create_sessions(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    profiles = manager(c)
    before, original_counts = snapshot(c), counts(c)
    result = profiles.create("新的旅程", "旅人", "new-save-1")
    profile = result["profile"]
    assert result["created"] and profile["has_state"]
    assert profile["label"] == "新的旅程" and profile["player_name"] == "旅人"
    assert profile["name"].startswith("local-") and profile["name"].isascii()
    state, progress = saved(c, profile["id"])
    assert state["records"]["t_prologue_tutorials"]["up"][0]["status"] == 0
    assert state["records"]["t_statuses"]["up"][0]["max_party_count"] == 1
    assert state["records"]["t_statuses"]["up"][0]["soul"] == 10000
    assert progress["features"]["tutorial"]["data"]["mode"] == "guided"
    assert not progress["features"]["tutorial"]["data"]["reward_claims"]
    assert counts(c)["sessions"] == original_counts["sessions"]
    assert snapshot(c) == before


def test_labels_preserve_login_identity_game_name_and_old_saves(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    profiles = manager(c)
    original = profiles.get(c.identity["user_id"])
    before = snapshot(c)
    renamed = profiles.rename(original["id"], "旧档 · 主线", original["label"])["profile"]
    assert renamed["name"] == original["name"]
    assert renamed["player_name"] == original["player_name"]
    assert renamed["label"] == "旧档 · 主线" and snapshot(c) == before
    assert c.store.login(renamed["name"])["user_id"] == original["id"]
    with pytest.raises(ProfileError, match="名称已变化"):
        profiles.rename(original["id"], "旧版本编辑", original["label"])
    assert profiles.rename(original["id"], "旧档 · 主线", original["label"])["profile"]["label"] == "旧档 · 主线"
    c.client.app.state.admin.journal.touch(original["id"], "profile-test")
    assert profiles.get(original["id"])["online"] is True
    assert profiles.get(original["id"])["last_seen"] is not None


def test_invalid_names_and_failed_initialization_leave_no_partial_save(setup_gameplay, monkeypatch):
    c = setup_gameplay
    profiles = manager(c)
    before = counts(c)
    for label in ("", "   ", "x" * 65, "bad\nname", "bad\u200bname", "bad\ud800name", 1, None):
        with pytest.raises(ProfileError):
            profiles.create(label)
    for player in (" " * 3, "a" * 33, "bad\x00name", True):
        with pytest.raises(ProfileError):
            profiles.create("有效档名", player)
    for operation in ([], "", "../other", "x" * 129):
        with pytest.raises(ProfileError):
            profiles.create("有效档名", operation_id=operation)
    assert counts(c) == before
    def fail(*args, **kwargs):
        raise RuntimeError("Initialization failed")
    monkeypatch.setattr("dotabyss_ps.admin_profiles.Gameplay.visit", fail)
    with pytest.raises(RuntimeError, match="Initialization failed"):
        profiles.create("失败的初始化", operation_id="atomic-failure")
    assert counts(c) == before


def test_create_is_idempotent_across_concurrent_requests_and_restart(setup_gameplay):
    c = setup_gameplay
    profiles = manager(c)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: profiles.create("冒险存档", "司令官", "same-request"), range(2)))
    assert results[0]["profile"]["id"] == results[1]["profile"]["id"]
    assert sum(result["created"] for result in results) == 1
    restarted = manager(c)
    assert restarted.create("冒险存档", "司令官", "same-request")["created"] is False
    with pytest.raises(ProfileError) as error:
        restarted.create("另一份档", "司令官", "same-request")
    assert error.value.code == "operation_conflict"


def test_duplicate_labels_check_unicode_and_legacy_fallback(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    profiles = manager(c)
    old = profiles.get(c.identity["user_id"])
    with pytest.raises(ProfileError) as error:
        profiles.create(old["label"])
    assert error.value.code == "label_conflict"
    first = profiles.create("Save A")["profile"]
    with pytest.raises(ProfileError):
        profiles.create("ｓａｖｅ ａ")
    with pytest.raises(ProfileError):
        profiles.rename(first["id"], old["label"], first["label"])
    long = profiles.create("长" * 64)["profile"]
    assert long["label"] == "长" * 64 and long["player_name"] == "司令官"


def test_list_handles_uninitialized_legacy_profiles_without_migration(tmp_path):
    store = Store(tmp_path / "local.sqlite3")
    identity = store.login("old-profile")
    profiles = ProfileManager(store, None)
    profile = profiles.get(identity["user_id"])
    assert profile["label"] == "old-profile" and profile["has_state"] is False
    assert profile["online"] is False
    with store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM profile_labels").fetchone()[0] == 0
    assert profiles.rename(identity["user_id"], "暂未登录的存档", "old-profile")["profile"]["name"] == "old-profile"
    with pytest.raises(ProfileError) as error:
        profiles.create("新的")
    assert error.value.status == 503
