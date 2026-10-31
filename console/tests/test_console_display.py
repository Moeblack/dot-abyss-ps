import copy

from console.display import cell_value, display_value, matches_query, record_rows, row_identity


def test_record_labels_keep_exact_identity_and_nested_raw_values():
    snapshot = {"state": {"records": {"t_event_currencies": {"up": [
        {"m_event_id": 900, "m_event_currency_id": 17, "amount": 3}], "down": []}}},
        "record_display": {"t_event_currencies": {"17": {"name": "活動硬幣", "m_event_currency_id": "活動硬幣"}}}}
    original = copy.deepcopy(snapshot)
    row = record_rows(snapshot, "t_event_currencies")[0]
    assert cell_value(row, "name") == "活動硬幣"
    assert cell_value(row, "m_event_currency_id") == "活動硬幣（ID: 17）"
    assert row_identity(row, "m_event_currency_id") == 17
    assert matches_query(row, "活動硬幣") and matches_query(row, "17")
    assert snapshot == original
    virtual = {"state": {"records": {}}, "record_sources": {"t_presents": ["progress", "features", "presents", "data", "entries"],
               "t_local_profiles": ["profile_choices"]},
               "progress": {"features": {"presents": {"data": {"entries": [{"id": 4, "content_id": 17}]}}}},
               "profile_choices": [{"id": 8, "name": "本地存档"}],
               "record_display": {"t_presents": {"4": {"name": "补给礼物", "content_id": "活動硬幣"}}}}
    assert cell_value(record_rows(virtual, "t_presents")[0], "content_id") == "活動硬幣（ID: 17）"
    assert record_rows(virtual, "t_local_profiles")[0]["name"] == "本地存档"
    assert record_rows(virtual, "t_unknown") == []
    raw = {"rewards": [{"content_id": 17, "amount": 3}, {"content_id": 999}],
           "m_character_ids": [1, 999], "name": "原名", "_display": {
               "name": "譯名", "rewards": [{"content_id": "活動硬幣"}, None], "m_character_ids": ["艾莉西亞", None]}}
    before = copy.deepcopy(raw)
    viewed = display_value(raw)
    assert viewed["rewards"] == [{"content_id": "活動硬幣（ID: 17）", "amount": 3}, {"content_id": 999}]
    assert viewed["m_character_ids"] == ["艾莉西亞（ID: 1）", 999]
    assert viewed["name"] == "譯名" and raw == before
    assert cell_value({"m_item_id": "9223372036854775807", "_display": {"m_item_id": "道具"}}, "m_item_id") == "道具（ID: 9223372036854775807）"
