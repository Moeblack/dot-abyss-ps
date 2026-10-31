import copy
import json

import pytest

from dotabyss_ps.newgame import NewGameError, validate_saved_state
from test_gameplay import call, gameplay_master, setup_gameplay, snapshot
from test_permanent_features import records


MEMBER_TABLE = "t_exploration_party_characters"


def member(position, character):
    return {"position_no": position, "t_character_id": character,
            "t_weapon_id": 0, "t_armor_id": 0, "t_accessory_id": 0}


def sparse_body():
    return {"party_no": 1, "characters": [member(1, 1), member(2, 2), member(8, 3)], "mana_gems": []}


def party(context, number=1):
    return next(row for row in records(context)["t_exploration_parties"]["up"] if row["party_no"] == number)


def members(context, party_id):
    return [row for row in records(context)[MEMBER_TABLE]["up"] if row["t_exploration_party_id"] == party_id]


def positions(rows):
    return {row["position_no"]: row["t_character_id"] for row in rows}


def test_party_partial_layout_has_only_occupied_rows_and_preserves_holes(setup_gameplay):
    c = setup_gameplay
    body = sparse_body()
    body["characters"].append(member(10, 0))
    response = call(c, "api/party/exploration/edit", body)
    assert positions(response["records"][MEMBER_TABLE]["up"]) == {1: 1, 2: 2, 8: 3}
    stored = members(c, party(c)["id"])
    assert len(stored) == 3
    assert positions(stored) == {1: 1, 2: 2, 8: 3}
    assert all(row["t_weapon_id"] == row["t_armor_id"] == row["t_accessory_id"] == 0 for row in stored)
    assert call(c, "api/party/exploration/edit", body)["records"] == {}


@pytest.mark.parametrize("explicit_zero", [False, True])
def test_party_remove_refill_and_clear_use_deletions_without_reusing_ids(setup_gameplay, explicit_zero):
    c = setup_gameplay
    body = sparse_body()
    call(c, "api/party/exploration/edit", body)
    parent = party(c)
    original = members(c, parent["id"])
    removed = next(row for row in original if row["position_no"] == 8)
    body["characters"] = body["characters"][:2] + ([member(8, 0)] if explicit_zero else [])
    headers = {**c.headers, "x-request-id": "remove-sparse-member"}
    response = call(c, "api/party/exploration/edit", body, headers=headers)
    assert response["records"][MEMBER_TABLE] == {"up": [], "down": [removed["id"]]}
    before = snapshot(c)
    assert call(c, "api/party/exploration/edit", body, headers=headers) == response
    assert snapshot(c) == before
    assert members(c, parent["id"]) == [row for row in original if row["id"] != removed["id"]]
    call(c, "api/party/exploration/edit", sparse_body())
    refilled = members(c, parent["id"])
    replacement = next(row for row in refilled if row["position_no"] == 8)
    assert replacement["id"] > max(row["id"] for row in original)
    cleared = call(c, "api/party/exploration/edit", {"party_no": 1, "characters": [], "mana_gems": []})
    assert cleared["records"][MEMBER_TABLE] == {"up": [], "down": sorted(row["id"] for row in refilled)}
    assert members(c, parent["id"]) == []
    assert call(c, "api/party/exploration/edit", {"party_no": 1, "characters": [], "mana_gems": []})["records"] == {}


def test_party_single_member_edit_can_remove_and_refill_an_empty_position(setup_gameplay):
    c = setup_gameplay
    call(c, "api/party/exploration/edit", sparse_body())
    parent = party(c)
    original = members(c, parent["id"])
    removed = next(row for row in original if row["position_no"] == 8)
    value = {"t_exploration_party_id": parent["id"], "party_order": 8, "party_position": 0,
             "t_character_id": 0, "t_weapon_id": 0, "t_armor_id": 0, "t_accessory_id": 0}
    body = {"t_exploration_party_character": value, "t_exploration_party_rune_character": []}
    response = call(c, "api/party/edit/character", body)
    assert response["records"][MEMBER_TABLE] == {"up": [], "down": [removed["id"]]}
    assert positions(response[MEMBER_TABLE]) == {1: 1, 2: 2}
    assert call(c, "api/party/edit/character", body)["records"] == {}
    value["t_character_id"] = 3
    response = call(c, "api/party/edit/character", body)
    replacement = next(row for row in response[MEMBER_TABLE] if row["position_no"] == 8)
    assert replacement["id"] > removed["id"]
    assert replacement["party_order"] == 8
    assert positions(response[MEMBER_TABLE]) == {1: 1, 2: 2, 8: 3}


def test_party_batch_member_edit_can_delete_without_invalid_lookup_or_replay(setup_gameplay):
    c = setup_gameplay
    call(c, "api/party/exploration/edit", sparse_body())
    parent = party(c)
    original = members(c, parent["id"])
    removed = next(row for row in original if row["position_no"] == 8)
    body = {"t_exploration_party_id": parent["id"], "t_exploration_party_characters": [
        {"t_exploration_party_character_id": removed["id"], "party_order": 8, "t_character_id": 0}],
        "t_exploration_party_mana_gems": []}
    headers = {**c.headers, "x-request-id": "batch-remove-member"}
    response = call(c, "api/party/edit/all-character", body, headers=headers)
    assert response["records"][MEMBER_TABLE] == {"up": [], "down": [removed["id"]]}
    before = snapshot(c)
    assert call(c, "api/party/edit/all-character", body, headers=headers) == response
    assert snapshot(c) == before
    assert members(c, parent["id"]) == [row for row in original if row["id"] != removed["id"]]


def test_party_copy_and_preset_restore_preserve_sparse_positions(setup_gameplay):
    c = setup_gameplay
    (c.settings.data_dir / "gameplay-rules.json").write_text(json.dumps({"party_slots": 3}), encoding="utf-8")
    call(c, "api/party/exploration/edit", sparse_body())
    source = party(c)
    call(c, "api/party/exploration/copy", {"original_party_no": 1, "replica_party_no": 2})
    target = party(c, 2)
    assert positions(members(c, target["id"])) == {1: 1, 2: 2, 8: 3}
    call(c, "api/party/push/preset", {"t_recent_exploration_party_id": target["id"], "preset_no": 1, "party_no": 2})
    preset = records(c)["t_preset_exploration_parties"]["up"][0]
    assert positions(members(c, preset["id"])) == {1: 1, 2: 2, 8: 3}
    assert not {row["id"] for row in members(c, target["id"])} & {row["id"] for row in members(c, preset["id"])}
    call(c, "api/party/exploration/edit", {"party_no": 2, "characters": [], "mana_gems": []})
    call(c, "api/party/pop/preset", {"t_recent_exploration_party_id": target["id"], "t_preset_exploration_party_id": preset["id"]})
    assert positions(members(c, target["id"])) == positions(members(c, source["id"])) == {1: 1, 2: 2, 8: 3}


def test_party_saved_zero_character_is_invalid_but_zero_equipment_is_valid(setup_gameplay):
    c = setup_gameplay
    call(c, "api/party/exploration/edit", sparse_body())
    state = json.loads(snapshot(c)[0])
    validate_saved_state(state)
    damaged = copy.deepcopy(state)
    damaged["records"][MEMBER_TABLE]["up"][0]["t_character_id"] = 0
    with pytest.raises(NewGameError, match="party character"):
        validate_saved_state(damaged)
