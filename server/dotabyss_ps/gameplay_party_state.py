from __future__ import annotations

from .gameplay_catalog import GameplayError, number
from .gameplay_economy import owned
from .gameplay_receipts import feature_data
from .gameplay_records import find, put, rows

PARENT_TABLES = ("t_exploration_parties", "t_preset_exploration_parties")
MEMBER_FIELDS = ("t_character_id", "t_weapon_id", "t_armor_id", "t_accessory_id")


def next_party_id(game):
    minimum = max((r["id"] for table in PARENT_TABLES for r in rows(game.state, table)), default=0) + 1
    data = feature_data(game, "party")
    value = max(minimum, number(data.get("next_party_id", minimum), 1, 2 ** 31 - 2))
    data["next_party_id"] = value + 1
    return value


def put_party_row(game, table, keys, values, user_id=None):
    record = find(game.state, table, **keys)
    if record is None:
        cursors = feature_data(game, "party").setdefault("next_ids", {})
        if not isinstance(cursors, dict):
            raise GameplayError("Stored party record counters are invalid")
        minimum = max((r["id"] for r in rows(game.state, table)), default=0) + 1
        identifier = max(minimum, number(cursors.get(table, minimum)))
        cursors[table] = number(identifier + 1)
        keys = {**keys, "id": identifier}
    return put(game.state, table, keys, values, user_id)


def parent(game, identifier, kind=None):
    identifier = number(identifier)
    for table, party_type in zip(PARENT_TABLES, (1, 2)):
        if kind is not None and kind != party_type:
            continue
        record = find(game.state, table, id=identifier)
        if record is not None:
            return record, party_type
    raise GameplayError("Party does not exist")


def ensure_party(game, number_value):
    row = find(game.state, "t_exploration_parties", party_no=number_value)
    if row is None:
        row = put(game.state, "t_exploration_parties", {"id": next_party_id(game)}, {
            "party_no": number_value, "restriction_value": 0, "name": f"Party {number_value}",
            "icon_1": 0, "icon_2": 0, "icon_3": 0, "updated_at": game.now.isoformat()}, game.user_id)
    return row


def validate_equipment(game, character, field, gear):
    if field == "t_accessory_id":
        return
    singular, plural = {"t_weapon_id": ("weapon", "weapons"), "t_armor_id": ("armor", "armors")}[field]
    definition = game.catalog.get("m_" + plural, gear[f"m_{singular}_id"])
    character_definition = game.catalog.get("m_characters", character["m_character_id"])
    if definition[f"{singular}_type"] != character_definition[f"{singular}_type"]:
        raise GameplayError("Equipment type is incompatible with this character")


def validate_layout(game, members, gems):
    if not isinstance(members, list) or len(members) > 10 or not isinstance(gems, list) or len(gems) > 3:
        raise GameplayError("Party layout exceeds its slot limit", 400, 4007)
    positions, selected, equipment = {}, set(), set()
    for member in members:
        if not isinstance(member, dict) or set(member) != {"position_no", *MEMBER_FIELDS}:
            raise GameplayError("Invalid party member fields", 400, 4007)
        position = number(member["position_no"], 1, 10)
        character_id = number(member["t_character_id"], 0)
        if position in positions or character_id and character_id in selected:
            raise GameplayError("Duplicate party position or character", 400, 4007)
        character = owned(game, "t_characters", character_id) if character_id else None
        if character_id:
            selected.add(character_id)
        for field, table in (("t_weapon_id", "t_weapons"), ("t_armor_id", "t_armors"), ("t_accessory_id", "t_accessories")):
            identifier = number(member[field], 0)
            if identifier:
                if character is None or (table, identifier) in equipment:
                    raise GameplayError("Party equipment is assigned without a character or assigned twice")
                validate_equipment(game, character, field, owned(game, table, identifier))
                equipment.add((table, identifier))
        positions[position] = dict(member)
    slots = {}
    for gem in gems:
        if not isinstance(gem, dict) or set(gem) != {"slot_no", "m_mana_gem_id"}:
            raise GameplayError("Invalid mana gem fields", 400, 4007)
        slot, identifier = number(gem["slot_no"], 1, 3), number(gem["m_mana_gem_id"])
        if slot in slots or identifier in slots.values() or find(game.state, "t_mana_gems", m_mana_gem_id=identifier) is None:
            raise GameplayError("Mana gem is not owned or is assigned twice")
        slots[slot] = identifier
    return positions, slots


def layout(game, identifier):
    parent(game, identifier)
    members = [{key: row[key] for key in ("position_no", *MEMBER_FIELDS)} for row in rows(game.state, "t_exploration_party_characters")
               if row["t_exploration_party_id"] == identifier]
    gems = [{key: row[key] for key in ("slot_no", "m_mana_gem_id")} for row in rows(game.state, "t_exploration_party_mana_gems")
            if row["t_exploration_party_id"] == identifier]
    return members, gems


def write_layout(game, record, kind, members, gems):
    positions, slots = validate_layout(game, members, gems)
    positions = {position: member for position, member in positions.items() if member["t_character_id"]}
    existing_members = rows(game.state, "t_exploration_party_characters")
    removed = [r for r in existing_members if r["t_exploration_party_id"] == record["id"] and r["position_no"] not in positions]
    if removed:
        cursors = feature_data(game, "party").setdefault("next_ids", {})
        if not isinstance(cursors, dict):
            raise GameplayError("Stored party record counters are invalid")
        table = "t_exploration_party_characters"
        cursors[table] = max(number(cursors.get(table, 1)), number(max(r["id"] for r in removed) + 1))
        existing_members[:] = [r for r in existing_members if r["t_exploration_party_id"] != record["id"] or r["position_no"] in positions]
    for position, member in sorted(positions.items()):
        key = {"t_exploration_party_id": record["id"], "position_no": position}
        existing = find(game.state, "t_exploration_party_characters", **key)
        put_party_row(game, "t_exploration_party_characters", key, {
            **{field: member[field] for field in MEMBER_FIELDS}, "party_type": 2 if kind == 2 else existing["party_type"] if existing else 0,
            "party_order": existing["party_order"] if existing else 0, "party_position": existing["party_position"] if existing else 0}, game.user_id)
    existing = rows(game.state, "t_exploration_party_mana_gems")
    existing[:] = [r for r in existing if r["t_exploration_party_id"] != record["id"] or r["slot_no"] in slots]
    for slot, identifier in slots.items():
        put_party_row(game, "t_exploration_party_mana_gems", {"t_exploration_party_id": record["id"], "slot_no": slot}, {"m_mana_gem_id": identifier}, game.user_id)


def drop_party(game, record, kind):
    for table in ("t_exploration_party_characters", "t_exploration_party_mana_gems"):
        container = rows(game.state, table)
        container[:] = [r for r in container if r["t_exploration_party_id"] != record["id"]]
    rows(game.state, PARENT_TABLES[kind - 1]).remove(record)
