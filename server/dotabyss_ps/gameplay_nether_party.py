from __future__ import annotations

import copy
import math
import struct

from .gameplay_catalog import GameplayError, number
from .gameplay_character_levels import effective_character_level
from .gameplay_effects import research_effects
from .gameplay_party_state import layout, validate_layout
from .gameplay_records import find, rows


def single(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def base_stat(value, level):
    factor = single(single(round(level * 0.1, 3)) + 1.0)
    return number(math.floor(single(single(value) * factor)), 0, 2 ** 31 - 1)


def ability(game, effect, level):
    definition = game.catalog.get("m_ability_effects", effect)
    return {"m_ability_effect_id": effect, "level": level, "type": definition["type"], "value": definition["value"]}


def equipment(game, member, singular, plural):
    identifier = member[f"t_{singular}_id"]
    if not identifier:
        return 0, 0, []
    owned = find(game.state, "t_" + plural, id=identifier)
    if owned is None:
        raise GameplayError("Nether party equipment is no longer owned")
    effects = [ability(game, row["m_ability_effect_id"], row["level"]) for row in rows(game.state, f"t_{singular}_enchantments")
               if row[f"t_{singular}_id"] == identifier and row["m_ability_effect_id"] > 0 and row["level"] > 0]
    return owned[f"m_{singular}_id"], owned["up_count"], effects


def snapshot_party(game, party_no):
    party = find(game.state, "t_exploration_parties", party_no=number(party_no, 1, 10))
    if party is None:
        raise GameplayError("Nether expedition party does not exist")
    members, gems = layout(game, party["id"])
    validate_layout(game, members, gems)
    members = [member for member in members if member["t_character_id"]]
    if not members:
        raise GameplayError("Nether expedition party is empty")
    characters = []
    for sid, member in enumerate(sorted(members, key=lambda row: row["position_no"]), 1):
        owned = find(game.state, "t_characters", id=member["t_character_id"])
        level = effective_character_level(game, owned)
        definition = game.catalog.get("m_characters", owned["m_character_id"])
        skin = game.catalog.get("m_character_skins", owned["battle_m_character_skin_id"])
        weapon_skin = game.catalog.get("m_weapon_skins", owned["battle_m_weapon_skin_id"])
        row = {"sid": sid, "assign_type": 1, "party_index": member["position_no"] - 1,
               "m_character_id": definition["id"], "character_asset_id": skin["asset_id"],
               "weapon_asset_id": weapon_skin["asset_id"], "limit_break_count": owned["limit_break_count"],
               "level": level, "exploration_power": 0, "character_abilities": [], "equipment_abilities": [],
               "general_abilities": [], "t_character_bond_parameters": []}
        for key in ("hp", "attack", "defence"):
            row[key] = base_stat(definition[key], level)
        for key in ("knockback_power", "knockback_resist", "critical_probability", "critical_damage_ratio",
                    "attack_continuous_probability", "avoid_probability", "skill_charge"):
            row[key] = definition[key]
        for index in range(1, 7):
            resistance = find(game.state, "t_character_condition_resistances", t_character_id=owned["id"], up_parameter_type=index + 7)
            row[f"resist_condition_{index}"] = resistance["level"] if resistance else 0
        for record in rows(game.state, "t_character_abilities"):
            if record["t_character_id"] == owned["id"] and record["level"]:
                master = game.catalog.get("m_character_abilities", record["m_character_ability_id"])
                awake = sum(entry["effect_type"] == 1 and entry["effect_param"] == master["id"]
                            and entry["m_character_id"] == owned["m_character_id"] and entry["limit_break_count"] <= owned["limit_break_count"]
                            for entry in game.catalog.rows("m_character_limit_breaks"))
                effects = [entry for entry in game.catalog.rows("m_abilities") if entry["ability_no"] == master["ability_no"]
                           and entry["ability_awake_level"] == awake]
                if not effects:
                    raise GameplayError("Nether character ability effect binding is missing", 503, 5036)
                row["character_abilities"].extend(ability(game, entry["m_ability_effects_id"], record["level"])
                                                   for entry in sorted(effects, key=lambda entry: (entry["display_order"], entry["id"])))
        for singular, plural in (("weapon", "weapons"), ("armor", "armors"), ("accessory", "accessories")):
            identifier, count, effects = equipment(game, member, singular, plural)
            row[f"m_{singular}_id"], row[f"{singular}_up_count"] = identifier, count
            row["equipment_abilities"].extend(effects)
        row["t_character_bond_parameters"] = [copy.deepcopy(value) for value in rows(game.state, "t_character_bond_parameters")
                                                if value["t_character_id"] == owned["id"]]
        row["exploration_power"] = row["hp"] // 10 + row["attack"] + row["defence"]
        characters.append(row)
    effects = []
    for source in research_effects(game, 400):
        fields = {1: "multiply_hp_ratio", 2: "multiply_attack_ratio", 3: "multiply_defence_ratio"}
        if source["param_2"] not in fields:
            continue
        effect = {"positions": [1, 2, 3] if source["param_1"] == 0 else [source["param_1"]],
                  "multiply_hp_ratio": 0, "multiply_attack_ratio": 0, "multiply_defence_ratio": 0}
        effect[fields[source["param_2"]]] = source["param_3"]
        effects.append(effect)
    rank = number(1 + sum(source["param_1"] for source in research_effects(game, 800)), 1, 2 ** 31 - 1)
    return {"mana_gem_rank": rank, "mana_gems": [{key: gem[key] for key in ("slot_no", "m_mana_gem_id")} for gem in gems],
            "characters": characters, "built_in_effects": effects}


def update_hp(game, run, values):
    if not isinstance(values, list) or len(values) != len(run["hp"]):
        raise GameplayError("Nether result must include every frozen party member", 400, 4007)
    result = {}
    for row in values:
        if not isinstance(row, dict) or set(row) != {"m_character_id", "current_hp_ratio"}:
            raise GameplayError("Invalid Nether character status", 400, 4007)
        identifier = str(number(row["m_character_id"]))
        if identifier not in run["hp"] or identifier in result:
            raise GameplayError("Nether result contains a foreign or duplicate character", 400, 4007)
        result[identifier] = number(row["current_hp_ratio"], 0, 1000)
    run["hp"] = result
