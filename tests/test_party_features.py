import copy
import json

from dotabyss_ps.gameplay_catalog import GameplayCatalog
from test_economy_features import give
from test_gameplay import call, gameplay_master, party_body, setup_gameplay, snapshot
from test_permanent_features import records


def test_party_copy_preserves_destination_profile_and_separates_records(setup_gameplay):
    c = setup_gameplay
    (c.settings.data_dir / "gameplay-rules.json").write_text(json.dumps({"party_slots": 5}), encoding="utf-8")
    call(c, "api/party/exploration/edit", party_body())
    call(c, "api/party/exploration/edit/profile", {"party_no": 2, "name": "Second", "icon_1": 1, "icon_2": 0, "icon_3": 0})
    call(c, "api/party/exploration/copy", {"original_party_no": 1, "replica_party_no": 2})
    data = records(c)
    parties = {r["party_no"]: r for r in data["t_exploration_parties"]["up"]}
    assert parties[2]["name"] == "Second" and parties[2]["icon_1"] == 1
    members = data["t_exploration_party_characters"]["up"]
    for number in (1, 2):
        assert len([r for r in members if r["t_exploration_party_id"] == parties[number]["id"] and r["t_character_id"]]) == 3
    modified = copy.deepcopy(party_body())
    modified.update(party_no=2, characters=[], mana_gems=[])
    call(c, "api/party/exploration/edit", modified)
    data = records(c)
    assert len([r for r in data["t_exploration_party_characters"]["up"] if r["t_exploration_party_id"] == parties[1]["id"] and r["t_character_id"]]) == 3
    assert not [r for r in data["t_exploration_party_mana_gems"]["up"] if r["t_exploration_party_id"] == parties[2]["id"]]
    before = snapshot(c)
    call(c, "api/party/exploration/copy", {"original_party_no": 4, "replica_party_no": 2}, expected=409)
    assert snapshot(c) == before


def test_party_equipment_checks_type_and_disassembly_references(setup_gameplay):
    c = setup_gameplay
    catalog = GameplayCatalog(c.tables, c.schema)
    definition = catalog.get("m_characters", 1200016)
    compatible = next(r for r in catalog.rows("m_weapons") if r["weapon_type"] == definition["weapon_type"])
    incompatible = next(r for r in catalog.rows("m_weapons") if r["weapon_type"] != definition["weapon_type"])
    give(c, [{"content_type": 70, "content_id": compatible["id"], "amount": 1}, {"content_type": 70, "content_id": incompatible["id"], "amount": 1}])
    gears = {r["m_weapon_id"]: r["id"] for r in records(c)["t_weapons"]["up"]}
    body = party_body()
    body["characters"][0]["t_weapon_id"] = gears[incompatible["id"]]
    before = snapshot(c)
    call(c, "api/party/exploration/edit", body, expected=409)
    assert snapshot(c) == before
    body["characters"][0]["t_weapon_id"] = gears[compatible["id"]]
    call(c, "api/party/exploration/edit", body)
    before = snapshot(c)
    call(c, "api/equipment/disassembly/execute", {"t_weapon_ids": [gears[compatible["id"]]], "t_armor_ids": [], "t_accessory_ids": []}, expected=409)
    assert snapshot(c) == before


def test_party_slots_and_invalid_profiles(setup_gameplay):
    c = setup_gameplay
    assert call(c, "api/party/slot")["capacity"] == {"character": 10, "mana_gem": 3}
    before = snapshot(c)
    call(c, "api/party/exploration/edit/profile", {"party_no": 1, "name": "", "icon_1": 0, "icon_2": 0, "icon_3": 0}, expected=400)
    call(c, "api/party/exploration/copy", {"original_party_no": 1, "replica_party_no": 2}, expected=400)
    assert snapshot(c) == before


def test_presets_have_distinct_parents_survive_edits_and_delete_children(setup_gameplay):
    c = setup_gameplay
    (c.settings.data_dir / "gameplay-rules.json").write_text(json.dumps({"party_slots": 5}), encoding="utf-8")
    call(c, "api/party/exploration/edit", party_body())
    source = records(c)["t_exploration_parties"]["up"][0]
    call(c, "api/party/push/preset", {"t_recent_exploration_party_id": source["id"], "preset_no": 1, "party_no": 1})
    preset = records(c)["t_preset_exploration_parties"]["up"][0]
    call(c, "api/party/exploration/edit/profile", {"party_no": 2, "name": "Other", "icon_1": 0, "icon_2": 0, "icon_3": 0})
    data = records(c)
    parent_ids = [r["id"] for t in ("t_exploration_parties", "t_preset_exploration_parties") for r in data[t]["up"]]
    assert len(set(parent_ids)) == 3
    call(c, "api/party/exploration/edit", {"party_no": 1, "characters": [], "mana_gems": []})
    call(c, "api/party/pop/preset", {"t_recent_exploration_party_id": source["id"], "t_preset_exploration_party_id": preset["id"]})
    data = records(c)
    assert len([r for r in data["t_exploration_party_characters"]["up"] if r["t_exploration_party_id"] == source["id"] and r["t_character_id"]]) == 3
    ids = [r["id"] for r in data["t_exploration_party_characters"]["up"] if r["t_exploration_party_id"] == preset["id"]]
    reply = call(c, "api/party/delete", {"t_exploration_party_id": preset["id"], "party_type": 2})
    assert reply["records"]["t_exploration_party_characters"]["down"] == ids
    call(c, "api/party/push/preset", {"t_recent_exploration_party_id": source["id"], "preset_no": 1, "party_no": 1})
    data = records(c)
    assert data["t_preset_exploration_parties"]["up"][0]["id"] > max(parent_ids)
    assert not set(ids) & {r["id"] for r in data["t_exploration_party_characters"]["up"]}


def test_order_edits_preserve_characters_and_fail_on_foreign_rows(setup_gameplay):
    c = setup_gameplay
    call(c, "api/party/exploration/edit", party_body())
    data = records(c)
    party = data["t_exploration_parties"]["up"][0]
    members = data["t_exploration_party_characters"]["up"]
    ids = [r["id"] for r in members]
    reply = call(c, "api/party/exchange/party-order", {"t_exploration_party_id": party["id"], "t_exploration_party_character_ids": list(reversed(ids))})
    assert {r["id"]: r["position_no"] for r in reply["t_exploration_party_characters"]} == {identifier: len(ids) - i for i, identifier in enumerate(ids)}
    before = snapshot(c)
    call(c, "api/party/edit/all-character", {"t_exploration_party_id": party["id"], "t_exploration_party_characters": [{"t_exploration_party_character_id": 999, "party_order": 1, "t_character_id": 1}], "t_exploration_party_mana_gems": []}, expected=409)
    assert snapshot(c) == before
    call(c, "api/party/edit/mana-gem", {"t_exploration_party_id": party["id"], "t_exploration_party_mana_gem": {"order": 1, "m_mana_gem_id": 0}, "t_exploration_party_rune_characters": []})
    assert [r["slot_no"] for r in records(c)["t_exploration_party_mana_gems"]["up"]] == [2]
    occupied = [r["id"] for r in records(c)["t_exploration_party_characters"]["up"] if r["t_character_id"]]
    reply = call(c, "api/party/exchange/party-order", {"t_exploration_party_id": party["id"], "t_exploration_party_character_ids": occupied})
    assert sorted(r["position_no"] for r in reply["t_exploration_party_characters"] if r["t_character_id"]) == [1, 2, 3]


def test_automatic_party_uses_owned_content_and_filters(setup_gameplay):
    c = setup_gameplay
    call(c, "api/party/make-auto", {"quest_type": 2, "restriction_value": 0, "party_condition": {"element_type": [], "union_type": []}})
    data = records(c)
    assert len([r for r in data["t_exploration_party_characters"]["up"] if r["t_character_id"]]) == len(data["t_characters"]["up"])
    owned_ids = {r["id"] for r in data["t_characters"]["up"]}
    assert all(r["t_character_id"] in owned_ids for r in data["t_exploration_party_characters"]["up"])
    before = snapshot(c)
    call(c, "api/party/make-auto", {"quest_type": 2, "restriction_value": 0, "party_condition": {"element_type": [7], "union_type": []}}, expected=400)
    assert snapshot(c) == before
