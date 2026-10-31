from __future__ import annotations

import random
from collections import defaultdict

from .gameplay_catalog import GameplayError, number
from .gameplay_battle_modes import battle_mode
from .gameplay_effects import research_effects


class StageBuilder:
    def __init__(self, game, seed, kind="main", quest_id=0, defensive_type=None, defensive_quest_id=0, defensive_boss_id=0):
        number(seed, 0, 2 ** 31 - 1)
        self.game = game
        self.catalog = game.catalog
        self.random = random.Random(seed)
        self.sid_base = seed << 20
        self.sid_count = 0
        self.groups = {}
        self.enemies = []
        self.resources = []
        self.mode = battle_mode(kind)
        self.quest_id = quest_id
        self.defensive_type = self.mode.defensive_type if defensive_type is None else defensive_type
        self.defensive_quest_id = defensive_quest_id
        self.defensive_boss_id = defensive_boss_id

    def sid(self):
        self.sid_count += 1
        if self.sid_count >= 4096:
            raise GameplayError("Battle stage contains too many entities", 503, 5036)
        return self.sid_base + self.sid_count

    def group(self, table, field, value):
        key = table, field
        if key not in self.groups:
            groups = defaultdict(list)
            for row in self.catalog.rows(table):
                groups[row[field]].append(row)
            self.groups[key] = groups
        return self.groups[key].get(value, [])

    def enemy(self, definition):
        master = self.catalog.get("m_enemies", definition["m_enemy_id"])
        self.catalog.get("m_enemy_parameters", master["m_enemy_parameter_id"])
        return {
            "sid": self.sid(), "m_enemy_id": master["id"],
            "set_position": number(definition.get("set_position", 0), 0, 2 ** 31 - 1),
            "drop_exp": number(definition.get("drop_exp", 0), 0, 2 ** 31 - 1),
            "drop_energy": number(definition.get("drop_energy", 0), 0, 2 ** 31 - 1),
            "is_target": number(definition.get("is_target", 0), 0, 1),
            "is_respawn": number(definition.get("is_respawn", 0), 0, 1), "drops": [],
        }

    def enemy_group(self, group_id):
        entries = self.group("m_dungeon_floor_part_enemies", "group_id", group_id)
        if not entries:
            raise GameplayError("Dungeon enemy group is missing", 503, 5036)
        selected = []
        for row in sorted(entries, key=lambda row: (row["order"], row["id"])):
            probability = number(row["probability"], 0, 1000)
            if probability == 1000 or self.random.randrange(1000) < probability:
                selected.append(row)
        return selected

    def lottery_groups(self, part):
        entries = self.group("m_dungeon_floor_part_enemy_lotteries", "group_id", part["m_dungeon_floor_part_enemy_lottery_id"])
        eligible = [(row, number(row["weight"], 0, 2 ** 31 - 1)) for row in entries]
        eligible = [(row, weight) for row, weight in eligible if weight]
        if not eligible:
            raise GameplayError("Dungeon enemy lottery has no eligible groups", 503, 5036)
        if not self.game.rules["battle_dungeon_lottery_weighted"]:
            eligible = [(row, 1) for row, _ in eligible]
        target = number(part["destroy_enemy_max"], 1, 1000)
        total_weight = sum(weight for _, weight in eligible)
        groups, remaining, attempts = [], target, 0
        while remaining:
            attempts += 1
            if attempts > target * 10:
                raise GameplayError("Dungeon enemy lottery cannot produce its required enemies", 503, 5036)
            draw = self.random.randrange(total_weight)
            for entry, weight in eligible:
                if draw < weight:
                    selected = self.enemy_group(entry["enemy_group_id"])[:remaining]
                    if selected:
                        groups.append(selected)
                        remaining -= len(selected)
                    break
                draw -= weight
        return groups

    def floor_part(self, part, fork_group):
        if part["role"] not in (100, 102, 200, 201, 300, 301, 302, 303, 304):
            raise GameplayError("This dungeon floor role is not supported", 501, 5010)
        result = {key: part[key] for key in (
            "role", "fork_group_no", "asset_id", "bgm_id", "m_novel_id", "m_effect_id",
            "max_enemy_entry", "destroy_enemy_max", "clear_type",
        )}
        result.update(sid=self.sid(), enemy_groups=[], resources=[], role_option={
            "seamless_battle": None, "treasure_battle": None, "mining_battle": None,
        })
        groups = []
        if part["m_dungeon_floor_part_enemy_lottery_id"]:
            groups = self.lottery_groups(part)
        elif part["m_dungeon_floor_part_enemy_group_id"]:
            groups = [self.enemy_group(part["m_dungeon_floor_part_enemy_group_id"])]
        for group in groups:
            enemies = [self.enemy(row) for row in group]
            self.enemies.extend(enemies)
            result["enemy_groups"].append({"enemies": [enemy["sid"] for enemy in enemies]})
        if part["role"] == 304:
            result["role_option"]["seamless_battle"] = {
                "time_limit": 0, "time_limit_ratio": 1000, "mana_energy_max_ratio": 1000,
                "success_to": 0, "failure_to": 0, "fork_group_no": fork_group,
            }
        elif part["role"] == 303:
            s_seconds = number(self.game.rules["battle_treasure_s_seconds"], 1, 86400)
            a_seconds = number(self.game.rules["battle_treasure_a_seconds"], s_seconds, 86400)
            result["role_option"]["treasure_battle"] = {"ranks": [
                {"rank": 3, "asset_id": "BoxGold", "time_limit": s_seconds, "drops": []},
                {"rank": 2, "asset_id": "BoxSilver", "time_limit": a_seconds, "drops": []},
                {"rank": 1, "asset_id": "BoxBronze", "time_limit": 0, "drops": []},
            ]}
        group_id = part["m_dungeon_floor_part_object_group_id"]
        if group_id:
            objects = self.group("m_dungeon_floor_part_objects", "group_id", group_id)
            if not objects:
                raise GameplayError("Dungeon resource group is missing", 503, 5036)
            for definition in sorted(objects, key=lambda row: row["id"]):
                resource = {key: definition[key] for key in (
                    "asset_id", "affiliation", "hp", "drop_energy", "m_effect_id", "pos_x", "pos_y", "pos_z",
                )}
                resource.update(sid=self.sid(), drops=[])
                self.resources.append(resource)
                result["resources"].append(resource["sid"])
        return result

    def dungeon(self, stage_id):
        self.catalog.get("m_dungeons", stage_id)
        parts = sorted(self.group("m_dungeon_floor_parts", "m_dungeon_id", stage_id), key=lambda row: row["id"])
        if not parts:
            raise GameplayError("Dungeon has no floor parts", 503, 5036)
        fork = min((part["fork_group_no"] for part in parts if part["fork_group_no"] > 0), default=0)
        floors = [self.floor_part(part, fork) for part in parts]
        return {"floor_parts": floors, "enemies": self.enemies, "resources": self.resources, "drops": [], "mining": None}

    def records(self, name):
        return self.game.state.get("records", {}).get(name, {}).get("up", [])

    def defensive_level(self, unit_id, level):
        master = self.catalog.get("m_defensive_units", unit_id)
        for effect in research_effects(self.game, 500):
            if effect["param_1"] == unit_id:
                level = max(level, effect["param_2"])
        levels = self.group("m_defensive_unit_levels", "m_defensive_unit_id", unit_id)
        if not any(row["level"] == level for row in levels):
            raise GameplayError("Defensive unit level is not defined", 503, 5036)
        return master, number(level, 1, 2 ** 31 - 1)

    def defensive_unit(self, row, available_lines, *, research=True):
        unit_id, level = row["m_defensive_unit_id"], row["level"]
        if research:
            master, level = self.defensive_level(unit_id, level)
        else:
            master = self.catalog.get("m_defensive_units", unit_id)
            if not any(candidate["level"] == level for candidate in self.group("m_defensive_unit_levels", "m_defensive_unit_id", unit_id)):
                raise GameplayError("Fixed defensive unit level is not defined", 503, 5036)
        if master["type"] == 900:
            raise GameplayError("The defensive object cannot occupy a unit slot", 503, 5036)
        return {
            "sid": self.sid(), "m_defensive_unit_id": unit_id, "level": level,
            "placement_line": number(row["placement_line"], 0, available_lines),
            "placement_index": number(row["placement_index"], 0, 1000),
        }

    def fort(self, fixed_fort_id, available_lines, party_no):
        if fixed_fort_id:
            fixed = self.catalog.get("m_fixed_forts", fixed_fort_id)
            self.catalog.get("m_defensive_units", fixed["m_defensive_unit_id"])
            unit_rows = self.group("m_fixed_defensive_units", "m_fixed_fort_id", fixed_fort_id)
            units = [self.defensive_unit(row, available_lines, research=False) for row in sorted(unit_rows, key=lambda row: row["id"])]
            return {"m_defensive_unit_id": fixed["m_defensive_unit_id"], "level": fixed["level"]}, units
        objects = [row for row in self.catalog.rows("m_defensive_units") if row["type"] == 900]
        if len(objects) != 1:
            raise GameplayError("Main-story defensive object is ambiguous", 503, 5036)
        object_id = objects[0]["id"]
        owned = {row["id"]: row for row in self.records("t_defensive_units")}
        object_level = max((row["level"] for row in owned.values() if row["m_defensive_unit_id"] == object_id), default=1)
        _, object_level = self.defensive_level(object_id, object_level)
        parties = {row["id"] for row in self.records("t_exploration_parties") if row["party_no"] == party_no}
        defensive_parties = {row["id"] for row in self.records("t_disaster_parties")
                             if row["t_exploration_party_id"] in parties and row["disaster_type"] == self.defensive_type
                             and (not self.defensive_boss_id or row["m_disaster_boss_id"] in (0, self.defensive_boss_id))
                             and (not self.defensive_quest_id or row["m_disaster_quest_id"] in (0, self.defensive_quest_id)
                                  or self.defensive_boss_id and row["m_disaster_boss_id"] == self.defensive_boss_id)}
        units = []
        for placement in self.records("t_disaster_party_defensive_units"):
            if placement["t_disaster_party_id"] not in defensive_parties:
                continue
            unit = owned.get(placement["t_defensive_unit_id"])
            if unit is None:
                raise GameplayError("Stored defensive formation refers to an unowned unit")
            units.append(self.defensive_unit({**unit, **placement}, available_lines))
        return {"m_defensive_unit_id": object_id, "level": object_level}, units

    def disaster(self, stage_id, fixed_fort_id, party_no):
        master = self.catalog.get("m_defend_stages", stage_id)
        available_lines = number(master["available_defence_line"], 1, 1000)
        enemies = []
        if master["m_defend_stage_enemy_group_id"]:
            if not self.mode.enemy_table:
                raise GameplayError("Defend stage has no enemy view binding", 503, 5036)
            battles = {self.catalog.get(self.mode.master, self.quest_id)[self.mode.reference_field]} if self.quest_id else {row[self.mode.reference_field] for row in self.catalog.rows(self.mode.master)
                       if row.get("stage_type") == 2 and row.get("stage_id") == stage_id}
            views = [row for row in self.catalog.rows(self.mode.enemy_table) if row[self.mode.enemy_key] in battles]
            if not views:
                raise GameplayError("Defend stage has no bound enemy lineup", 503, 5036)
            candidates = {row["m_enemy_id"] for row in views if not row["is_boss"]}
            for _ in range(self.game.rules["battle_defence_enemy_copies"]):
                for index, enemy_id in enumerate(sorted(candidates)):
                    enemies.append(self.enemy({"m_enemy_id": enemy_id, "set_position": index,
                                               "drop_energy": self.game.rules["battle_defence_enemy_energy"]}))
        boss = self.enemy({"m_enemy_id": master["m_enemy_id"], "drop_exp": master["drop_exp"], "is_target": 1})
        defensive_object, units = self.fort(fixed_fort_id, available_lines, party_no)
        return {"available_defence_line": available_lines, "defensive_object": defensive_object,
                "defensive_units": units, "enemies": enemies, "boss_enemy": boss, "drops": []}


def build_stage(game, stage_type, stage_id, seed, *, fixed_fort_id=0, party_no=1, kind="main", quest_id=0,
                defensive_type=None, defensive_quest_id=0, defensive_boss_id=0):
    number(stage_type, 1, 3)
    number(stage_id)
    number(fixed_fort_id, 0)
    number(party_no, 0, 10)
    number(quest_id, 0)
    if defensive_type is not None:
        number(defensive_type, 1, 100)
    number(defensive_quest_id, 0)
    number(defensive_boss_id, 0)
    builder = StageBuilder(game, seed, kind, quest_id, defensive_type, defensive_quest_id, defensive_boss_id)
    if stage_type == 3:
        from .gameplay_mining_stage import build_mining

        if kind != "event_miner" or fixed_fort_id:
            raise GameplayError("Mining stages require the mining event mode", 400, 4007)
        return build_mining(builder, stage_id)
    if stage_type == 1:
        if fixed_fort_id:
            raise GameplayError("Dungeon battles cannot use a fixed fort", 503, 5036)
        return builder.dungeon(stage_id)
    return builder.disaster(stage_id, fixed_fort_id, party_no)
