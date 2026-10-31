from __future__ import annotations

from .gameplay_catalog import GameplayError, number
from .gameplay_records import find, rows
from .gameplay_receipts import feature_data
from .gameplay_battle_modes import BATTLE_MODES


def character_bonus(game, character):
    total = 0
    for record in rows(game.state, "t_character_abilities"):
        if record["t_character_id"] != character["id"] or record["level"] <= 0:
            continue
        master = game.catalog.get("m_character_abilities", record["m_character_ability_id"])
        awake = sum(row["effect_type"] == 1 and row["effect_param"] == master["id"]
                    and row["m_character_id"] == character["m_character_id"]
                    and row["limit_break_count"] <= character["limit_break_count"]
                    for row in game.catalog.rows("m_character_limit_breaks"))
        for ability in game.catalog.rows("m_abilities"):
            if ability["ability_no"] == master["ability_no"] and ability["ability_awake_level"] == awake:
                effect = game.catalog.get("m_ability_effects", ability["m_ability_effects_id"])
                if effect["type"] == 900:
                    total += number(effect["value"], 0, 1000000)
    return total


def member_bonus(game, member):
    character = find(game.state, "t_characters", id=member["t_character_id"])
    if character is None:
        return 0
    total = character_bonus(game, character)
    for singular in ("weapon", "armor", "accessory"):
        identifier = member.get(f"t_{singular}_id", 0)
        if not identifier:
            continue
        for enchantment in rows(game.state, f"t_{singular}_enchantments"):
            if (enchantment[f"t_{singular}_id"] != identifier or enchantment["level"] <= 0
                    or enchantment["m_ability_effect_id"] <= 0):
                continue
            effect = game.catalog.get("m_ability_effects", enchantment["m_ability_effect_id"])
            if effect["type"] != 900:
                continue
            total += number(effect["value"], 0, 1000000)
    return total


def party_bonus(game, party_no, *, idle=False):
    if not party_no:
        return 0
    if idle:
        members = [row for row in rows(game.state, "t_idle_exploration_party_characters") if row["party_no"] == party_no]
    else:
        party = find(game.state, "t_exploration_parties", party_no=party_no)
        members = [] if party is None else [row for row in rows(game.state, "t_exploration_party_characters")
                                           if row["t_exploration_party_id"] == party["id"]]
    return min(1000, sum(member_bonus(game, member) for member in members if member["t_character_id"]))


def snapshots(game):
    values = feature_data(game, "party").setdefault("drop_snapshots", {})
    if not isinstance(values, dict) or len(values) > 10000:
        raise GameplayError("Stored party drop snapshots are invalid")
    for key, value in values.items():
        parts = key.split(":") if isinstance(key, str) else []
        if len(parts) != 2 or parts[0] not in BATTLE_MODES or not parts[1].isascii() or not parts[1].isdigit():
            raise GameplayError("Stored party drop snapshot key is invalid")
        identifier = number(int(parts[1]))
        if str(identifier) != parts[1]:
            raise GameplayError("Stored party drop snapshot key is not canonical")
        game.catalog.get(BATTLE_MODES[parts[0]].master, identifier)
        number(value, 0, 1000)
    return values


def saved_bonus(game, kind, battle_id, party_no=0):
    values = snapshots(game)
    key = f"{kind}:{battle_id}"
    if key in values:
        return values[key]
    return party_bonus(game, party_no)


def save_bonus(game, session):
    if "ability_extra_drop" in session:
        values = snapshots(game)
        values[f"{session['kind']}:{session['battle_id']}"] = number(session["ability_extra_drop"], 0, 1000)
        if session["kind"] == "main":
            values[f"repeat:{session['battle_id']}"] = session["ability_extra_drop"]
        snapshots(game)
