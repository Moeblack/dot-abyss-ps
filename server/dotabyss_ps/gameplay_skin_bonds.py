from __future__ import annotations

import re

from .gameplay_catalog import GameplayError, number
from .gameplay_receipts import feature_data
from .gameplay_records import rows

SKIN_TABLES = {
    "character": ("t_character_skins", "m_character_skin_id", "m_character_skins"),
    "weapon": ("t_weapon_skins", "m_weapon_skin_id", "m_weapon_skins"),
}
REWARD_KEY = re.compile(r"(character|weapon):([1-9][0-9]{0,18}):([1-9][0-9]{0,18})")
MAX_REWARDS = 100000


def matches_character(kind, skin, character):
    if kind == "character":
        return skin["m_character_id"] == character["id"]
    return skin["m_character_id"] in (0, character["id"]) and skin["weapon_type"] == character["weapon_type"]


def validate_skin_bond_rewards(game):
    data = game.progress.get("features", {}).get("character", {}).get("data", {})
    ledger = data.get("skin_bond_rewards", {})
    if not isinstance(ledger, dict) or len(ledger) > MAX_REWARDS:
        raise GameplayError("Stored skin bond rewards are invalid")
    for key, amount in ledger.items():
        match = REWARD_KEY.fullmatch(key) if isinstance(key, str) else None
        if match is None:
            raise GameplayError("Stored skin bond reward key is invalid")
        kind, skin_id, character_id = match.groups()
        skin = game.catalog.get(SKIN_TABLES[kind][2], number(int(skin_id)))
        character = game.catalog.get("m_characters", number(int(character_id)))
        number(amount, 0, 2 ** 31 - 1)
        if not matches_character(kind, skin, character):
            raise GameplayError("Stored skin bond reward does not match its character")
    return ledger


def reconcile_skin_bonds(game):
    ledger = validate_skin_bond_rewards(game)
    characters = {row["m_character_id"]: row for row in rows(game.state, "t_characters")}
    masters = {identifier: game.catalog.get("m_characters", identifier) for identifier in characters}
    rewards, totals = {}, {}
    for kind, (table, field, master_table) in SKIN_TABLES.items():
        for identifier in sorted({row[field] for row in rows(game.state, table)}):
            skin = game.catalog.get(master_table, identifier)
            amount = number(skin.get("bonus_bond_point", 0), 0, 2 ** 31 - 1)
            for character_id, character in masters.items():
                key = f"{kind}:{identifier}:{character_id}"
                if key in ledger or not matches_character(kind, skin, character):
                    continue
                rewards[key] = amount
                totals[character_id] = totals.get(character_id, 0) + amount
    if len(ledger) + len(rewards) > MAX_REWARDS:
        raise GameplayError("Skin bond reward history exceeds its limit")
    balances = {identifier: number(characters[identifier]["bond_point"] + amount, 0, 2 ** 31 - 1)
                for identifier, amount in totals.items()}
    for identifier, value in balances.items():
        characters[identifier]["bond_point"] = value
    feature_data(game, "character").setdefault("skin_bond_rewards", {}).update(rewards)
