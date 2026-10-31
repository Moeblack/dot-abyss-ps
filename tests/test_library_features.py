import pytest

from dotabyss_ps.gameplay_catalog import GameplayCatalog
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records, seed


def test_character_stories_unlock_at_intimacy_and_read_reward_is_once(setup_gameplay):
    c = setup_gameplay
    master = GameplayCatalog(c.tables, c.schema)
    story = next(r for r in master.rows("m_novel_characters") if r["m_character_id"] == 1200016 and r["condition_value"] == 1)
    seed(c, items={140001: 4})
    before = snapshot(c)
    body = {"novel_type": 3, "m_novel_id": story["id"]}
    call(c, "api/novel/read", body, expected=409)
    assert snapshot(c) == before
    call(c, "api/character/gift/give", {"t_character_id": 1, "use_items": [{"m_item_id": 140001, "amount": 2}]})
    listing = call(c, "api/library/novel/character")
    unlocked = next(r for r in listing["t_novel_characters"]["up"] if r["m_novel_character_id"] == story["id"])
    assert unlocked["is_unread"] == 1
    gold_before = records(c)["t_statuses"]["up"][0]["free_virtual_money"]
    result = call(c, "api/novel/read", body)
    assert result["read_rewards"] == [{"content_type": 10, "content_id": 0, "amount": 50, "bonus_type": 0, "is_rare_drop": 0, "additional_info": None}]
    assert call(c, "api/novel/read", body)["read_rewards"] == []
    data = records(c)
    assert data["t_statuses"]["up"][0]["free_virtual_money"] == gold_before + 50
    assert next(r for r in data["t_novel_characters"]["up"] if r["id"] == unlocked["id"])["is_unread"] == 0


def test_library_seen_marker_does_not_claim_story_reward(setup_gameplay):
    c = setup_gameplay
    seed(c, items={140001: 4})
    call(c, "api/character/gift/give", {"t_character_id": 1, "use_items": [{"m_item_id": 140001, "amount": 2}]})
    row = call(c, "api/library/novel/character")["t_novel_characters"]["up"][0]
    before = snapshot(c)
    call(c, "api/library/novel/character/read", {"t_novel_character_ids": [row["id"], 999999]}, expected=409)
    assert snapshot(c) == before
    call(c, "api/library/novel/character/read", {"t_novel_character_ids": [row["id"]]})
    assert call(c, "api/novel/read", {"novel_type": 3, "m_novel_id": row["m_novel_character_id"]})["read_rewards"]


def test_dictionary_records_follow_release_and_chapter_requirements(setup_gameplay):
    c = setup_gameplay
    call(c, "api/library/dictionary")
    data = records(c)
    master = GameplayCatalog(c.tables, c.schema)
    assert data["t_dictionary_worlds"]["up"]
    for plural, singular in (("non_player_characters", "non_player_character"), ("enemies", "enemy"), ("worlds", "world")):
        assert all(master.get("m_dictionary_" + plural, r["m_dictionary_" + singular + "_id"])["quest_type"] == 0 for r in data["t_dictionary_" + plural]["up"])
    row = data["t_dictionary_worlds"]["up"][0]
    call(c, "api/library/dictionary/read", {"dictionary_type": 4, "t_dictionary_ids": [str(row["id"])]})
    assert next(r for r in records(c)["t_dictionary_worlds"]["up"] if r["id"] == row["id"])["is_unread"] == 0
    before = snapshot(c)
    call(c, "api/library/dictionary/read", {"dictionary_type": 4, "t_dictionary_ids": [str(row["id"]), "999999"]}, expected=409)
    assert snapshot(c) == before


@pytest.mark.parametrize("values", [[1], ["0"], ["-1"], ["01"], ["1", "1"], ["1e3"]])
def test_dictionary_rejects_malformed_ids_without_creating_save(setup_gameplay, values):
    c = setup_gameplay
    call(c, "api/library/dictionary/read", {"dictionary_type": 4, "t_dictionary_ids": values}, expected=400)
    assert snapshot(c) == (None, None)


def test_main_story_replay_requires_reached_sequence(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    before = snapshot(c)
    call(c, "api/novel/read", {"novel_type": 1, "m_novel_id": 1}, expected=409)
    assert snapshot(c) == before
    call(c, "api/main-story/update-sequence", {"m_chapter_quest_id": 1010101, "sequence": 1})
    assert call(c, "api/novel/read", {"novel_type": 1, "m_novel_id": 1})["read_rewards"] == []
