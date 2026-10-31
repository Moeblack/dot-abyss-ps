import copy
import json

import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_battle_notifications import battle_notifications
from dotabyss_ps.gameplay_catalog import GameplayCatalog, GameplayError
from dotabyss_ps.gameplay_library import character_read, eligible_novel, novel_read, sync_novels
from dotabyss_ps.gameplay_records import find, put, rows
from dotabyss_ps.newgame import RECORD_NAMES
from test_economy_features import update
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records, seed


def character_stories(context, identifier=1200002):
    catalog = GameplayCatalog(context.tables, context.schema)
    return [row for row in catalog.rows("m_novel_characters") if row["m_character_id"] == identifier]


def release_ids(result):
    return {row["m_novel_character_id"] for row in (result.get("notifies") or {}).get("novel_character_release", [])}


def test_release_notification_requires_a_new_unlocked_status():
    before = {name: {"up": [], "down": []} for name in RECORD_NAMES}
    before["t_novel_characters"]["up"] = [
        {"m_novel_character_id": 1, "status": 0}, {"m_novel_character_id": 2, "status": 1}]
    after = copy.deepcopy(before)
    after["t_novel_characters"]["up"] = [
        {"m_novel_character_id": 1, "status": 1}, {"m_novel_character_id": 2, "status": 1},
        {"m_novel_character_id": 3, "status": 0}, {"m_novel_character_id": 4, "status": 1},
        {"m_novel_character_id": 5, "status": 99}]
    assert battle_notifications(before, after)["novel_character_release"] == [
        {"m_novel_character_id": 1}, {"m_novel_character_id": 4}]
    assert battle_notifications(after, copy.deepcopy(after)) is None


def test_gifts_notify_new_stories_and_retries_preserve_state_across_restart(setup_gameplay):
    context = setup_gameplay
    definitions = character_stories(context, 1200016)
    seed(context, items={140001: 107})
    old = {row["m_novel_character_id"] for row in records(context)["t_novel_characters"]["up"]}
    for amount, level in ((2, 1), (104, 15)):
        headers = {**context.headers, "x-request-id": f"affection-unlock-{level}"}
        body = {"t_character_id": 1, "use_items": [{"m_item_id": 140001, "amount": amount}]}
        result = call(context, "api/character/gift/give", body, headers=headers)
        expected = {row["id"] for row in definitions if row["condition_value"] <= level} - old
        assert expected and release_ids(result) == expected
        assert {row["m_novel_character_id"] for row in result["records"]["t_novel_characters"]["up"]} == expected
        old.update(expected)
        after = snapshot(context)
        assert call(context, "api/character/gift/give", body, headers=headers) == result
        assert snapshot(context) == after
    with TestClient(create_app(context.settings)) as restarted:
        response = restarted.get("/api/library/novel/character", headers=context.headers)
        assert response.status_code == 200
        listing = response.json()["contents"]
        assert not release_ids(listing)
        assert old <= {row["m_novel_character_id"] for row in listing["t_novel_characters"]["up"]}
    result = call(context, "api/character/gift/give", {"t_character_id": 1, "use_items": [{"m_item_id": 140001, "amount": 1}]})
    assert not release_ids(result)


def test_unlocked_story_survives_affection_reduction_and_rewards_only_once(setup_gameplay):
    context = setup_gameplay
    definitions = character_stories(context)
    story = next(row for row in definitions if row["condition_value"] == 5)
    locked = next(row for row in definitions if row["condition_value"] == 15)
    seed(context, items={140001: 12})
    call(context, "api/character/gift/give", {"t_character_id": 2, "use_items": [{"m_item_id": 140001, "amount": 12}]})
    update(context, lambda state, progress: find(state, "t_characters", id=2).update(intimacy_point=0))
    before = snapshot(context)
    call(context, "api/novel/read", {"novel_type": 3, "m_novel_id": locked["id"]}, expected=409)
    assert snapshot(context) == before
    balance = records(context)["t_statuses"]["up"][0]["free_virtual_money"]
    body = {"novel_type": 3, "m_novel_id": story["id"]}
    headers = {**context.headers, "x-request-id": "lowered-affection-story"}
    result = call(context, "api/novel/read", body, headers=headers)
    assert result["read_rewards"] and not release_ids(result)
    reward = sum(row["amount"] for row in result["read_rewards"])
    after = snapshot(context)
    assert call(context, "api/novel/read", body, headers=headers) == result
    assert snapshot(context) == after
    with TestClient(create_app(context.settings)) as restarted:
        response = restarted.post("/api/novel/read", json=body, headers=context.headers)
        assert response.status_code == 200
        assert response.json()["contents"]["read_rewards"] == []
    data = records(context)
    assert data["t_statuses"]["up"][0]["free_virtual_money"] == balance + reward
    assert next(row for row in data["t_characters"]["up"] if row["id"] == 2)["intimacy_point"] == 0


def test_existing_locked_story_opens_at_threshold_without_erasing_read_receipt(setup_gameplay):
    context = setup_gameplay
    story = next(row for row in character_stories(context) if row["condition_value"] == 5)
    seed(context, items={140001: 12})

    def insert_locked(state, progress):
        put(state, "t_novel_characters", {"m_novel_character_id": story["id"]},
            {"status": 0, "is_unread": 1}, context.identity["user_id"])

    update(context, insert_locked)
    row = next(row for row in records(context)["t_novel_characters"]["up"] if row["m_novel_character_id"] == story["id"])
    before = snapshot(context)
    call(context, "api/novel/read", {"novel_type": 3, "m_novel_id": story["id"]}, expected=409)
    call(context, "api/library/novel/character/read", {"t_novel_character_ids": [row["id"]]}, expected=409)
    assert snapshot(context) == before

    def restore_receipt(state, progress):
        progress["features"]["library"]["data"]["read_novels"][f"3:{story['id']}"] = True

    update(context, restore_receipt)
    result = call(context, "api/character/gift/give", {"t_character_id": 2, "use_items": [{"m_item_id": 140001, "amount": 12}]})
    assert story["id"] in release_ids(result)
    opened = next(row for row in result["records"]["t_novel_characters"]["up"] if row["m_novel_character_id"] == story["id"])
    assert (opened["id"], opened["status"], opened["is_unread"]) == (row["id"], 1, 0)
    assert call(context, "api/novel/read", {"novel_type": 3, "m_novel_id": story["id"]})["read_rewards"] == []


def test_unlocked_story_still_requires_its_character(setup_gameplay):
    context = setup_gameplay
    seed(context, items={140001: 2})
    call(context, "api/character/gift/give", {"t_character_id": 2, "use_items": [{"m_item_id": 140001, "amount": 2}]})
    state, progress = map(json.loads, snapshot(context))
    game = Gameplay(state, progress, GameplayCatalog(context.tables, context.schema), context.identity["user_id"], NOW)
    story = next(row for row in character_stories(context) if row["condition_value"] == 1)
    record = find(state, "t_novel_characters", m_novel_character_id=story["id"])
    rows(state, "t_characters").remove(find(state, "t_characters", id=2))
    assert not eligible_novel(game, 3, story)
    before = copy.deepcopy((state, progress))
    with pytest.raises(GameplayError, match="requirements"):
        novel_read(game, {"novel_type": 3, "m_novel_id": story["id"]})
    with pytest.raises(GameplayError, match="not unlocked"):
        character_read(game, {"t_novel_character_ids": [record["id"]]})
    sync_novels(game)
    assert (state, progress) == before
