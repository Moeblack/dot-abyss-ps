from __future__ import annotations

import copy

from .gameplay_catalog import GameplayError, identifiers, number
from .gameplay_economy import text_argument
from .gameplay_party_state import MEMBER_FIELDS, drop_party, ensure_party, layout, next_party_id, parent, write_layout
from .gameplay_records import find, put, rows
from .gameplay_routes import route


def fields(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected.split()):
        raise GameplayError("Invalid party edit fields", 400, 4007)
    return value


def preset(game, preset_no, party_no, *, create=False):
    preset_no = number(preset_no, 1, game.rules["party_presets"])
    party_no = number(party_no, 1, rows(game.state, "t_statuses")[0]["max_party_count"])
    record = find(game.state, "t_preset_exploration_parties", preset_no=preset_no, party_no=party_no)
    if record is None and create:
        record = put(game.state, "t_preset_exploration_parties", {"id": next_party_id(game)},
                     {"preset_no": preset_no, "party_no": party_no, "name": f"Preset {preset_no}", "icon": 0}, game.user_id)
    if record is None:
        raise GameplayError("Preset does not exist")
    return record


def push_preset(game, body):
    source, _ = parent(game, body["t_recent_exploration_party_id"], 1)
    target = preset(game, body["preset_no"], body["party_no"], create=True)
    write_layout(game, target, 2, *layout(game, source["id"]))
    return {"status": 0}


def pop_preset(game, body):
    target, _ = parent(game, body["t_recent_exploration_party_id"], 1)
    source, _ = parent(game, body["t_preset_exploration_party_id"], 2)
    write_layout(game, target, 1, *layout(game, source["id"]))
    return {"status": 0}


def preset_name(game, body):
    value = fields(body["t_preset_exploration_party"], "preset_no party_no name icon")
    record = preset(game, value["preset_no"], value["party_no"], create=True)
    record.update(name=text_argument(value["name"], game.rules["max_party_name_length"]), icon=number(value["icon"], 0, 2 ** 31 - 1))
    return {"status": 0}


def delete(game, body):
    kind = number(body["party_type"], 1, 2)
    record, _ = parent(game, body["t_exploration_party_id"], kind)
    drop_party(game, record, kind)
    return {"status": 0}


def member_records(game, identifier):
    return [copy.deepcopy(r) for r in rows(game.state, "t_exploration_party_characters") if r["t_exploration_party_id"] == identifier]


def unsupported_runes(value):
    if value:
        raise GameplayError("Legacy rune-character records are not present in this client record schema", 501, 5010)


def edit_character(game, body):
    value = fields(body["t_exploration_party_character"], "t_exploration_party_id party_order party_position t_character_id t_weapon_id t_armor_id t_accessory_id")
    unsupported_runes(body["t_exploration_party_rune_character"])
    record, kind = parent(game, value["t_exploration_party_id"])
    order, position = number(value["party_order"], 1, 10), number(value["party_position"], 0, 2)
    slot = number(order if position == 0 else (position - 1) * 5 + number(order, 1, 5), 1, 10)
    members, gems = layout(game, record["id"])
    members = [r for r in members if r["position_no"] != slot]
    members.append({"position_no": slot, **{key: value[key] for key in MEMBER_FIELDS}})
    write_layout(game, record, kind, members, gems)
    member = find(game.state, "t_exploration_party_characters", t_exploration_party_id=record["id"], position_no=slot)
    if member is not None:
        member.update(party_order=order, party_position=position)
    return {"status": 0, "t_exploration_party_characters": member_records(game, record["id"])}


def edit_all_characters(game, body):
    record, kind = parent(game, body["t_exploration_party_id"])
    values, mana = body["t_exploration_party_characters"], body["t_exploration_party_mana_gems"]
    if not isinstance(values, list) or len(values) > 10 or not isinstance(mana, list) or len(mana) > 3:
        raise GameplayError("Party edit batch is invalid", 400, 4007)
    members, gems = layout(game, record["id"])
    selected, orders = set(), set()
    by_id = {r["id"]: r for r in member_records(game, record["id"])}
    for value in values:
        fields(value, "t_exploration_party_character_id party_order t_character_id")
        identifier = number(value["t_exploration_party_character_id"])
        order = number(value["party_order"], 1, 10)
        if identifier in selected or identifier not in by_id or order in orders:
            raise GameplayError("Party member is duplicated, missing or belongs to another party")
        selected.add(identifier)
        orders.add(order)
        old = by_id[identifier]
        member = next(r for r in members if r["position_no"] == old["position_no"])
        character = number(value["t_character_id"], 0)
        if member["t_character_id"] != character:
            member.update(t_character_id=character, t_weapon_id=0, t_armor_id=0, t_accessory_id=0)
    for value in mana:
        fields(value, "t_exploration_party_mana_gem_id t_exploration_party_rune_characters")
        mana_record = find(game.state, "t_exploration_party_mana_gems", id=number(value["t_exploration_party_mana_gem_id"]), t_exploration_party_id=record["id"])
        if mana_record is None:
            raise GameplayError("Mana gem slot does not belong to this party")
        unsupported_runes(value["t_exploration_party_rune_characters"])
    write_layout(game, record, kind, members, gems)
    for value in values:
        member = find(game.state, "t_exploration_party_characters", id=value["t_exploration_party_character_id"])
        if member is not None:
            member["party_order"] = value["party_order"]
    return {}


def edit_mana(game, body):
    record, kind = parent(game, body["t_exploration_party_id"])
    value = fields(body["t_exploration_party_mana_gem"], "order m_mana_gem_id")
    unsupported_runes(body["t_exploration_party_rune_characters"])
    slot, identifier = number(value["order"], 1, 3), number(value["m_mana_gem_id"], 0)
    members, gems = layout(game, record["id"])
    gems = [r for r in gems if r["slot_no"] != slot]
    if identifier:
        gems.append({"slot_no": slot, "m_mana_gem_id": identifier})
    write_layout(game, record, kind, members, gems)
    return {"status": 0}


def exchange_order(game, body):
    record, _ = parent(game, body["t_exploration_party_id"])
    selected = identifiers(body["t_exploration_party_character_ids"], 10)
    members = [r for r in rows(game.state, "t_exploration_party_characters") if r["t_exploration_party_id"] == record["id"]]
    by_id = {r["id"]: r for r in members}
    if set(selected) != set(by_id):
        raise GameplayError("Party order must include every occupied slot exactly once")
    for position, identifier in enumerate(selected, 1):
        by_id[identifier].update(position_no=position, party_order=(position - 1) % 5 + 1, party_position=(position - 1) // 5 + 1)
    return {"t_exploration_party_characters": member_records(game, record["id"])}


def make_auto(game, body):
    number(body["quest_type"], 1, 1000)
    restriction = number(body["restriction_value"], 0, 2 ** 31 - 1)
    condition = fields(body["party_condition"], "element_type union_type")
    elements, unions = identifiers(condition["element_type"], 6), identifiers(condition["union_type"], 10)
    if any(i > 6 for i in elements) or any(i > 10 for i in unions):
        raise GameplayError("Invalid automatic party filters", 400, 4007)
    characters = []
    for character in rows(game.state, "t_characters"):
        definition = game.catalog.get("m_characters", character["m_character_id"])
        if (not elements or definition["element_type"] in elements) and (not unions or definition["union_type"] in unions):
            characters.append((character, definition))
    characters.sort(key=lambda value: (-value[0]["level"], -value[0]["limit_break_count"], -value[1]["rarity"], value[0]["id"]))
    if not characters:
        raise GameplayError("No owned characters match the party filters")
    members, used = [], set()
    for position, (character, definition) in enumerate(characters[:10], 1):
        member = {"position_no": position, "t_character_id": character["id"], "t_weapon_id": 0, "t_armor_id": 0, "t_accessory_id": 0}
        for singular, plural in (("weapon", "weapons"), ("armor", "armors"), ("accessory", "accessories")):
            candidates = []
            for gear in rows(game.state, "t_" + plural):
                gear_definition = game.catalog.get("m_" + plural, gear[f"m_{singular}_id"])
                if (singular, gear["id"]) not in used and (singular == "accessory" or gear_definition[f"{singular}_type"] == definition[f"{singular}_type"]):
                    candidates.append((gear, gear_definition))
            if candidates:
                gear, _ = max(candidates, key=lambda pair: (pair[1]["rank"], pair[1]["rarity"], pair[0]["up_count"], -pair[0]["id"]))
                member[f"t_{singular}_id"] = gear["id"]
                used.add((singular, gear["id"]))
        members.append(member)
    target = ensure_party(game, 1)
    target["restriction_value"] = restriction
    gems = [{"slot_no": slot, "m_mana_gem_id": r["m_mana_gem_id"]} for slot, r in enumerate(sorted(rows(game.state, "t_mana_gems"), key=lambda r: r["m_mana_gem_id"])[:3], 1)]
    write_layout(game, target, 1, members, gems)
    return {"status": 0}


PARTY_EDIT_ROUTES = {
    "api/party/push/preset": route("POST", "t_recent_exploration_party_id preset_no party_no", push_preset),
    "api/party/pop/preset": route("POST", "t_recent_exploration_party_id t_preset_exploration_party_id", pop_preset),
    "api/party/edit/name": route("POST", "t_preset_exploration_party", preset_name),
    "api/party/delete": route("POST", "t_exploration_party_id party_type", delete),
    "api/party/edit/character": route("POST", "t_exploration_party_character t_exploration_party_rune_character", edit_character),
    "api/party/edit/all-character": route("POST", "t_exploration_party_id t_exploration_party_characters t_exploration_party_mana_gems", edit_all_characters),
    "api/party/edit/mana-gem": route("POST", "t_exploration_party_id t_exploration_party_mana_gem t_exploration_party_rune_characters", edit_mana),
    "api/party/exchange/party-order": route("POST", "t_exploration_party_id t_exploration_party_character_ids", exchange_order),
    "api/party/make-auto": route("POST", "quest_type restriction_value party_condition", make_auto),
}
