import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from test_gameplay import call, gameplay_master, setup_gameplay, snapshot
from test_localstate import login


def peer(context, name):
    identity, headers = login(context.client, name)
    call(context, "api/user", headers=headers)
    return identity["user_id"], headers


def test_local_friend_search_exposes_only_public_fields_and_counts_persist(setup_gameplay):
    c = setup_gameplay
    other, headers = peer(c, "friend-one")
    call(c, "api/profile/edit/name", {"name": "Friend Name", "is_sync_dmm": 0}, headers=headers)
    result = call(c, f"api/friend/search/{other}", method="GET")
    assert result["t_status"]["name"] == "Friend Name"
    assert set(result["t_status"]) == {"user_id", "name", "user_rank", "last_login_at"}
    assert set(result["t_profile"]) == {"user_id", "favorite_m_character_skin_id", "m_degree_id", "comment"}
    call(c, "api/friend/follow", {"target_user_id": other})
    call(c, "api/friend/follow", {"target_user_id": other})
    assert call(c, "api/profile/top")["follow_count"] == 1
    assert call(c, "api/profile/top", headers=headers)["follower_count"] == 1
    assert [r["t_status"]["user_id"] for r in call(c, "api/friend/top")["follow_list"]] == [other]
    assert not call(c, "api/friend/top")["user_list"]


def test_follower_removal_changes_only_the_authenticated_targets_relation(setup_gameplay):
    c = setup_gameplay
    second, second_headers = peer(c, "friend-second")
    third, third_headers = peer(c, "friend-third")
    call(c, "api/friend/follow", {"target_user_id": second})
    call(c, "api/friend/follow", {"target_user_id": third})
    call(c, "api/friend/follow", {"target_user_id": third}, headers=second_headers)
    call(c, "api/friend/follower/remove", {"target_user_id": c.identity["user_id"]}, headers=third_headers)
    assert call(c, "api/profile/top")["follow_count"] == 1
    assert call(c, "api/profile/top", headers=second_headers)["follow_count"] == 1
    assert call(c, "api/profile/top", headers=third_headers)["follower_count"] == 1
    call(c, "api/friend/unfollow", {"target_user_id": second})
    assert call(c, "api/profile/top")["follow_count"] == 0


def test_follow_limits_self_target_and_concurrent_retries(setup_gameplay):
    c = setup_gameplay
    second, _ = peer(c, "friend-limit-one")
    third, _ = peer(c, "friend-limit-two")
    (c.settings.data_dir / "gameplay-rules.json").write_text(json.dumps({"friend_limit": 1}), encoding="utf-8")
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda _: call(c, "api/friend/follow", {"target_user_id": second}), range(4)))
    before = snapshot(c)
    call(c, "api/friend/follow", {"target_user_id": third}, expected=409)
    call(c, "api/friend/follow", {"target_user_id": c.identity["user_id"]}, expected=409)
    call(c, "api/friend/follow", {"target_user_id": 999999}, expected=409)
    assert snapshot(c) == before
    assert call(c, "api/profile/top")["follow_count"] == 1


def test_relation_and_game_state_roll_back_in_one_transaction(setup_gameplay):
    c = setup_gameplay
    other, _ = peer(c, "friend-rollback")
    call(c, "api/user")
    before = snapshot(c)
    def fail(state, progress, db):
        db.execute("INSERT INTO follows VALUES (?,?)", (c.identity["user_id"], other))
        state["records"]["t_statuses"]["up"][0]["gold"] += 100
        raise ValueError("failure after relation mutation")
    with pytest.raises(ValueError, match="after relation mutation"):
        c.store.game_state(c.identity["user_id"], c.record["revision"], lambda *_: None, transaction_operation=fail)
    assert snapshot(c) == before
    assert call(c, "api/profile/top")["follow_count"] == 0
