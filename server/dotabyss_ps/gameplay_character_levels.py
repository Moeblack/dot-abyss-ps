from __future__ import annotations

from .gameplay_catalog import GameplayError, number
from .gameplay_records import rows


def synchronization_unlocked(game):
    return any(row["skill_type"] == 11001 and row["skill_level"] >= 1
               for row in rows(game.state, "t_building_skills"))


def effective_character_level(game, character):
    original = number(character["level"], 1, 2 ** 31 - 1)
    if not synchronization_unlocked(game):
        return original
    synchronized = rows(game.state, "t_level_synchros")
    if len(synchronized) != 1:
        raise GameplayError("Unlocked synchronization requires one level record", 503, 5036)
    return number(synchronized[0]["level"], 1, 2 ** 31 - 1)
