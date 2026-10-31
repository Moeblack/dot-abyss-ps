import json
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from dotabyss_ps.gameplay_catalog import GameplayCatalog, available
from dotabyss_ps.gameplay_records import put
from test_economy_features import update
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records, seed


def configure(context, **settings):
    (context.settings.data_dir / "gameplay-rules.json").write_text(json.dumps({"event_open_all": 0, **settings}), encoding="utf-8")


def story(context, identifier):
    return next(row for row in records(context)["t_novel_events"]["up"] if row["m_novel_event_id"] == identifier)


def test_event_recollection_unlock_does_not_read_or_grant_rewards(setup_gameplay):
    c = setup_gameplay
    call(c, "api/novel/read", {"novel_type": 2, "m_novel_id": 30000101}, expected=409)
    assert snapshot(c) == (None, None)
    before = records(c)["t_statuses"]["up"][0]["free_virtual_money"]
    configure(c, event_recollection_unlock_all=1)
    data = records(c)
    catalog = GameplayCatalog(c.tables, c.schema)
    released = {row["id"] for row in catalog.rows("m_novel_events") if available(row, NOW, "open_at")}
    assert {row["m_novel_event_id"] for row in data["t_novel_events"]["up"]} == released
    assert all(set(row) == {"id", "user_id", "m_novel_event_id", "is_unread"} and row["is_unread"] == 1 for row in data["t_novel_events"]["up"])
    assert data["t_statuses"]["up"][0]["free_virtual_money"] == before
    library = json.loads(snapshot(c)[1])["features"]["library"]["data"]
    assert library["read_novels"] == {}
    assert library.get("event_reward_claims", {}) == {}


def test_selected_event_unlock_persists_without_unlocking_other_or_future_events(setup_gameplay):
    c = setup_gameplay
    configure(c, event_recollection_event_ids=[300001, 300006])
    assert {row["m_novel_event_id"] for row in records(c)["t_novel_events"]["up"]} == set(range(30000101, 30000109))
    configure(c)
    assert call(c, "api/novel/read", {"novel_type": 2, "m_novel_id": 30000101})["read_rewards"]
    before = snapshot(c)
    for identifier in (30000201, 30000601):
        call(c, "api/novel/read", {"novel_type": 2, "m_novel_id": identifier}, expected=409)
        assert snapshot(c) == before


@pytest.mark.parametrize(("identifier", "amount"), [(30000101, 50), (30000301, 50), (50000101, 50), (90000101, 50), (100000101, 0)])
def test_event_first_read_matches_native_rewards_and_survives_restart(setup_gameplay, identifier, amount):
    c = setup_gameplay
    configure(c, event_recollection_unlock_all=1)
    before = records(c)["t_statuses"]["up"][0]["free_virtual_money"]
    body = {"novel_type": 2, "m_novel_id": identifier}
    result = call(c, "api/novel/read", body)
    expected = [{"content_type": 10, "content_id": 0, "amount": amount, "bonus_type": 0, "is_rare_drop": 0, "additional_info": None}] if amount else []
    assert result["read_rewards"] == expected
    assert result["records"]["t_novel_events"]["up"][0]["is_unread"] == 0
    assert story(c, identifier)["is_unread"] == 0
    progress = json.loads(snapshot(c)[1])["features"]["library"]["data"]
    assert progress["read_novels"][f"2:{identifier}"] is True
    assert progress["event_reward_claims"][str(identifier)]["source"] == "local_read"
    with TestClient(create_app(c.settings)) as restarted:
        replay = restarted.post("/api/novel/read", headers=c.headers, json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["contents"]["read_rewards"] == []
    assert records(c)["t_statuses"]["up"][0]["free_virtual_money"] == before + amount


def test_existing_event_read_marker_preserves_read_history_without_duplicate_reward(setup_gameplay):
    c = setup_gameplay
    update(c, lambda state, progress: put(state, "t_novel_events", {"m_novel_event_id": 30000101}, {"is_unread": 0}, c.identity["user_id"]))
    before = records(c)["t_statuses"]["up"][0]["free_virtual_money"]
    assert call(c, "api/novel/read", {"novel_type": 2, "m_novel_id": 30000101})["read_rewards"] == []
    assert records(c)["t_statuses"]["up"][0]["free_virtual_money"] == before
    receipt = json.loads(snapshot(c)[1])["features"]["library"]["data"]["event_reward_claims"]["30000101"]
    assert receipt == {"source": "existing_read", "rewards": []}


def test_event_reward_claim_is_independent_from_unread_marker_and_concurrent_replays(setup_gameplay):
    c = setup_gameplay
    configure(c, event_recollection_event_ids=[300001])
    before = records(c)["t_statuses"]["up"][0]["free_virtual_money"]
    body = {"novel_type": 2, "m_novel_id": 30000101}
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: call(c, "api/novel/read", body), range(2)))
    assert sum(sum(row["amount"] for row in result["read_rewards"]) for result in results) == 50

    def reset_marker(state, progress):
        next(row for row in state["records"]["t_novel_events"]["up"] if row["m_novel_event_id"] == 30000101)["is_unread"] = 1
        del progress["features"]["library"]["data"]["read_novels"]["2:30000101"]

    update(c, reset_marker)
    assert call(c, "api/novel/read", body)["read_rewards"] == []
    assert story(c, 30000101)["is_unread"] == 0
    assert records(c)["t_statuses"]["up"][0]["free_virtual_money"] == before + 50


def test_bad_event_reward_receipt_rolls_back_read_and_currency(setup_gameplay):
    c = setup_gameplay
    configure(c, event_recollection_event_ids=[300001])
    update(c, lambda state, progress: progress["features"]["library"]["data"].update(event_reward_claims={"30000101": False}))
    before = snapshot(c)
    call(c, "api/novel/read", {"novel_type": 2, "m_novel_id": 30000101}, expected=409)
    assert snapshot(c) == before


def test_gift_response_includes_new_character_story_records(setup_gameplay):
    c = setup_gameplay
    seed(c, items={140001: 4})
    result = call(c, "api/character/gift/give", {"t_character_id": 1, "use_items": [{"m_item_id": 140001, "amount": 2}]})
    assert result["records"]["t_novel_characters"]["up"]
    assert all(row["is_unread"] == 1 for row in result["records"]["t_novel_characters"]["up"])


def test_other_novel_categories_keep_their_existing_unlock_and_reward_rules(setup_gameplay):
    c = setup_gameplay
    catalog = GameplayCatalog(c.tables, c.schema)
    home = catalog.rows("m_novel_homes")[0]
    skin = catalog.rows("m_novel_character_skins")[0]

    def unlock(state, progress):
        state["records"]["t_prologue_tutorials"]["up"][0]["status"] = 99
        put(state, "t_novel_homes", {"m_novel_home_id": home["id"]}, {}, c.identity["user_id"])
        put(state, "t_character_skins", {"m_character_skin_id": skin["m_character_skin_id"]}, {}, c.identity["user_id"])

    update(c, unlock)
    examples = [(4, home["id"]), (5, skin["id"]), (6, catalog.rows("m_novel_prologues")[0]["id"]), (7, catalog.rows("m_novel_others")[0]["id"])]
    for kind, identifier in examples:
        body = {"novel_type": kind, "m_novel_id": identifier}
        result = call(c, "api/novel/read", body)
        assert sum(row["amount"] for row in result["read_rewards"]) == (50 if kind == 5 else 0)
        assert call(c, "api/novel/read", body)["read_rewards"] == []
