import copy

import pytest

from dotabyss_ps.gameplay_receipts import refresh_replay_records
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot


@pytest.mark.parametrize("route,first,second,table,field", [
    ("api/tutorial/prologue-update", {"status": 1}, {"status": 2}, "t_prologue_tutorials", "status"),
    ("api/main-story/update-sequence", {"m_chapter_quest_id": 1010101, "sequence": 1},
     {"m_chapter_quest_id": 1010101, "sequence": 2}, "t_chapter_quests", "sequence"),
])
def test_old_progress_request_replays_current_records_without_mutation(setup_gameplay, route, first, second, table, field):
    c = setup_gameplay
    original_headers = {**c.headers, "x-request-id": "original-progress"}
    original = call(c, route, first, headers=original_headers)
    assert original["records"][table]["up"][0][field] == 1
    call(c, route, second, headers={**c.headers, "x-request-id": "next-progress"})
    before = snapshot(c)
    replayed = call(c, route, first, headers=original_headers)
    assert replayed["records"][table]["up"][0][field] == 2
    assert snapshot(c) == before
    assert original["records"][table]["up"][0][field] == 1


def test_refresh_tracks_only_original_ids_and_turns_missing_rows_into_deletions():
    state = {"records": {"t_items": {"up": [
        {"id": 1, "m_item_id": 10, "amount": 15},
        {"id": 3, "m_item_id": 30, "amount": 77},
        {"id": 4, "m_item_id": 40, "amount": 30},
    ], "down": []}}}
    original = {"records": {"t_items": {"up": [
        {"id": 1, "m_item_id": 10, "amount": 5},
        {"id": 2, "m_item_id": 20, "amount": 20},
    ], "down": [4, 2]}}, "receive_content_list": [{"content_type": 50, "content_id": 0, "amount": 10}]}
    state_before, response_before = copy.deepcopy(state), copy.deepcopy(original)
    refreshed = refresh_replay_records(state, original)
    assert refreshed["records"]["t_items"] == {"up": [
        {"id": 1, "m_item_id": 10, "amount": 15},
        {"id": 4, "m_item_id": 40, "amount": 30},
    ], "down": [2]}
    assert refreshed["receive_content_list"] == original["receive_content_list"]
    refreshed["records"]["t_items"]["up"][0]["amount"] = 999
    refreshed["receive_content_list"][0]["amount"] = 999
    assert state == state_before
    assert original == response_before


def test_refresh_uses_native_identity_for_event_currency_rows():
    state = {"records": {"t_event_currencies": {"up": [
        {"m_event_currency_id": 3000, "amount": 90},
        {"m_event_currency_id": 4000, "amount": 70},
    ], "down": []}}}
    original = {"records": {"t_event_currencies": {"up": [
        {"m_event_currency_id": 3000, "amount": 10},
        {"m_event_currency_id": 5000, "amount": 50},
    ], "down": []}}}
    assert refresh_replay_records(state, original) == {"records": {"t_event_currencies": {
        "up": [{"m_event_currency_id": 3000, "amount": 90}], "down": [5000],
    }}}


def test_refresh_preserves_responses_without_records():
    original = {"battle_result": {"drops": [{"content_type": 50, "content_id": 0, "amount": 10}]}}
    refreshed = refresh_replay_records({"records": {}}, original)
    assert refreshed == original
    refreshed["battle_result"]["drops"][0]["amount"] = 0
    assert original["battle_result"]["drops"][0]["amount"] == 10
