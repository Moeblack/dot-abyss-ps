from __future__ import annotations

import copy
import random
from datetime import timedelta

from .gameplay_catalog import GameplayError, available, number
from .gameplay_nether_configuration import frozen_nether
from .gameplay_receipts import feature_data
from .gameplay_records import find, put, rows

POINT_FIELDS = ("sphere_skill_point", "sphere_attack_point", "sphere_erosion_resist_point", "sphere_erosion_up_point")
ACTIVE_STATUSES = (2, 3, 5, 6)


def state(game):
    data = feature_data(game, "nether")
    if not data:
        data.update(next_run_id=1, run=None, points={key: 0 for key in POINT_FIELDS}, total_score=0,
                    spheres=[], free_entries={}, last_result=None, ticket_claims={})
    data.setdefault("ticket_claims", {})
    data.setdefault("ticket_day", day(game))
    if set(data) != {"next_run_id", "run", "points", "total_score", "spheres", "free_entries", "last_result", "ticket_claims", "ticket_day"}:
        raise GameplayError("Stored Nether progress is invalid")
    if data["ticket_day"] != day(game):
        for record in rows(game.state, "t_nethers"):
            record["used_ticket_day"] = 0
        data["ticket_day"] = day(game)
    number(data["next_run_id"], 1, 2 ** 31 - 1)
    number(data["total_score"], 0)
    if not isinstance(data["points"], dict) or set(data["points"]) != set(POINT_FIELDS):
        raise GameplayError("Stored Nether research points are invalid")
    for amount in data["points"].values():
        number(amount, 0, 2 ** 31 - 1)
    if not isinstance(data["ticket_claims"], dict):
        raise GameplayError("Stored Nether ticket claims are invalid")
    for amount in data["ticket_claims"].values():
        number(amount, 0, 2 ** 31 - 1)
    if not isinstance(data["spheres"], list) or len(data["spheres"]) > 1000 or not isinstance(data["free_entries"], dict):
        raise GameplayError("Stored Nether research progress is invalid")
    return data


@frozen_nether
def scope(game, nether_id=None, map_id=None, *, active=False):
    definitions = game.catalog.rows("m_nethers")
    if nether_id is None:
        definitions = [row for row in definitions if available(row, game.now, "open_at", "close_at")]
        if not definitions:
            raise GameplayError("Nether is not currently available")
        definition = min(definitions, key=lambda row: row["id"])
    else:
        definition = game.catalog.get("m_nethers", number(nether_id))
        if active and not available(definition, game.now, "open_at", "close_at"):
            raise GameplayError("Nether is not currently available")
    if map_id is None:
        maps = [row for row in game.catalog.rows("m_nether_maps") if row["m_nether_id"] == definition["id"]]
        if not maps:
            raise GameplayError("Nether map master is missing", 503, 5036)
        selected = min(maps, key=lambda row: row["id"])
    else:
        selected = game.catalog.get("m_nether_maps", number(map_id))
        if selected["m_nether_id"] != definition["id"]:
            raise GameplayError("Nether map belongs to a different season", 400, 4007)
    return definition, selected


@frozen_nether
def ensure(game):
    definition, selected = scope(game)
    existing = find(game.state, "t_nethers", m_nether_id=definition["id"])
    if existing is not None:
        return existing
    values = {key: 0 for key in ("m_nether_map_floor_id", "floor_level", "max_floor_level", "max_cleared_floor_level",
              "continuance_floor_level", "code_reload", "erosion_point", "nether_gold", "treasure_key",
              "hidden_count", "strong_enemy_count", "used_ticket_day", "latest_event_number")}
    values.update(status=1, m_nether_map_id=selected["id"], floor_index=2, used_ticket=1, m_nether_code_ids=[])
    return put(game.state, "t_nethers", {"m_nether_id": definition["id"]}, values, game.user_id)


@frozen_nether
def record(game, nether_id=None, map_id=None):
    definition, selected = scope(game, nether_id, map_id)
    current = ensure(game)
    if current["m_nether_id"] != definition["id"] or current["m_nether_map_id"] != selected["id"]:
        raise GameplayError("Requested Nether run does not match the current map")
    return current


@frozen_nether
def run(game, *, active=True):
    data = state(game)["run"]
    current = ensure(game)
    if not isinstance(data, dict) or active and (data.get("settled") or current["status"] not in (*ACTIVE_STATUSES, 8)):
        raise GameplayError("No active Nether expedition")
    data.setdefault("base_score", data["cleared_count"] * game.rules["nether_score_per_floor"])
    data.setdefault("bonus_score", 0)
    data.setdefault("score_multiplier", 1)
    repair_event_connection(game, data, current)
    return data


@frozen_nether
def repair_event_connection(game, expedition, current):
    identifier, level = current["m_nether_map_floor_id"], current["floor_level"]
    if not identifier:
        return
    actual = {row["m_nether_map_floor_id"] for row in expedition["floors"] if row["floor_level"] == level}
    following = [row for row in expedition["floors"] if row["floor_level"] == level + 1]
    stale = {value for row in following for value in row["m_nether_map_floor_prev_ids"]} - actual
    if len(stale) != 1 or identifier not in actual:
        return
    previous = next(iter(stale))
    parts = {row[f"m_nether_floor_event_part_id_{index}"] for row in game.catalog.rows("m_nether_floor_events")
             if row["m_nether_map_floor_id"] == previous for index in range(1, 5)} - {0}
    proven = any(part[f"target_type_{index}"] == 8 and part[f"select_parameter_{index}"] == identifier
                 for part in (game.catalog.get("m_nether_floor_event_parts", key) for key in parts)
                 for index in range(1, 4))
    if proven:
        for row in following:
            row["m_nether_map_floor_prev_ids"] = [identifier if value == previous else value
                                                for value in row["m_nether_map_floor_prev_ids"]]


@frozen_nether
def entry_multiplier(game, tickets):
    return 1 + max(0, tickets - 1) * game.rules["nether_score_multiplier_per_extra_ticket"]


@frozen_nether
def restrictions(game, floor):
    return [row for row in game.catalog.rows("m_nether_floor_restricts")
            if row["m_nether_map_floor_id"] == floor["m_nether_map_floor_id"]]


@frozen_nether
def reveal_routes(game):
    expedition = state(game)["run"]
    if not expedition or expedition["settled"]:
        return
    current = ensure(game)
    for floor in expedition["floors"]:
        if not floor["is_hidden"] or floor["floor_level"] != current["floor_level"] + 1:
            continue
        if current["m_nether_map_floor_id"] and current["m_nether_map_floor_id"] not in floor["m_nether_map_floor_prev_ids"]:
            continue
        conditions = restrictions(game, floor)
        if conditions and all(row["restrict_type"] == 1 and row["restrict_param_1"] <= current["erosion_point"] <= row["restrict_param_2"] for row in conditions):
            floor["is_hidden"] = 0
            return copy.deepcopy(floor)
    return None


@frozen_nether
def point_view(game):
    data, current = state(game), ensure(game)
    result = {"user_id": game.user_id, "m_nether_id": current["m_nether_id"], **data["points"],
              "max_code_reload": game.rules["nether_initial_code_reloads"], "max_nether_code": game.rules["nether_initial_code_limit"],
              "extra_drop_ratio": 0, "sphere_point_ratio": 0, "start_nether_gold": game.rules["nether_starting_gold"],
              "lock_reward": 0, "recovery_floor_level": 0, "total_score": data["total_score"]}
    effects = {1: "max_nether_code", 2: "extra_drop_ratio", 3: "sphere_point_ratio", 4: "start_nether_gold",
               10: "lock_reward", 11: "max_code_reload", 20: "recovery_floor_level"}
    for selected in data["spheres"]:
        definitions = [row for row in game.catalog.rows("m_nether_spheres")
                       if row["m_nether_sphere_board_id"] == selected["m_nether_sphere_board_id"]
                       and row["type"] == selected["type"] and row["level"] <= selected["level"]]
        for definition in definitions:
            key = effects.get(definition["effect_type"])
            if key is None:
                raise GameplayError("Nether sphere effect is not supported", 503, 5036)
            result[key] += definition["effect_parameter_1"]
    return result


@frozen_nether
def hp_view(game):
    data = state(game)["run"]
    if not data:
        return []
    return [{"id": index, "user_id": game.user_id, "m_character_id": int(identifier), "current_hp_ratio": amount}
            for index, (identifier, amount) in enumerate(data["hp"].items(), 1)]


@frozen_nether
def held_items(game, locked=False):
    return rows(game.state, "t_nether_lock_items" if locked else "t_nether_items")


@frozen_nether
def add_item(game, item_id, amount, *, locked=False):
    number(amount, 0, 2 ** 31 - 1)
    if not amount:
        return
    game.catalog.get("m_items", number(item_id))
    current = ensure(game)
    table = "t_nether_lock_items" if locked else "t_nether_items"
    item = find(game.state, table, m_item_id=item_id)
    value = number((item["amount"] if item else 0) + amount, 0, 2 ** 31 - 1)
    put(game.state, table, {"m_item_id": item_id}, {"m_nether_id": current["m_nether_id"],
        "m_nether_map_id": current["m_nether_map_id"], "amount": value}, game.user_id)


@frozen_nether
def clear_items(game):
    held_items(game).clear()
    held_items(game, True).clear()


@frozen_nether
def generate_map(game, map_id, seed):
    selected = game.catalog.get("m_nether_maps", map_id)
    definitions = [row for row in game.catalog.rows("m_nether_map_floors") if row["m_nether_map_id"] == map_id]
    rng = random.Random(f"nether-map:{seed}")
    result, previous, used = [], [], {}
    restricted = {row["m_nether_map_floor_id"] for row in game.catalog.rows("m_nether_floor_restricts")}
    for level in range(1, selected["max_floor_num"] + 1):
        eligible = [row for row in definitions if row["min_order"] <= level <= row["max_order"] and row["min_erosion_point"] == 0
                    and (not row["used_count"] or used.get(row["id"], 0) < row["used_count"])]
        bosses = [row for row in eligible if row["type"] == 2]
        if bosses:
            chosen = [rng.choice(bosses)]
        else:
            singles = [row for row in eligible if row["size"] == 1 and row["type"] != 8]
            if singles:
                chosen = rng.sample(singles, min(3, len(singles)))
            elif eligible:
                chosen = [rng.choice(eligible)]
            else:
                raise GameplayError("Nether map has no eligible floor template", 503, 5036)
        generated = []
        for position, definition in enumerate(chosen, 1):
            index = 2 if len(chosen) == 1 else position
            kind = definition["type"]
            source = "m_nether_floor_battles" if kind in (1, 2, 3) else "m_nether_floor_shops" if kind == 6 else "m_nether_floor_events"
            candidates = [row for row in game.catalog.rows(source) if row["m_nether_map_floor_id"] == definition["id"]]
            if not candidates:
                raise GameplayError("Nether floor template has no content", 503, 5036)
            weights = [max(0, row.get("weight", 1)) for row in candidates]
            extended = rng.choices(candidates, weights=weights, k=1)[0]
            row = {"m_nether_id": selected["m_nether_id"], "m_nether_map_id": map_id,
                   "m_nether_map_floor_id": definition["id"], "floor_level": level, "floor_index": index,
                   "m_nether_map_floor_prev_ids": [entry["m_nether_map_floor_id"] for entry in previous],
                   "is_hidden": int(definition["id"] in restricted), "extend_id": extended["id"]}
            generated.append(row)
            used[definition["id"]] = used.get(definition["id"], 0) + 1
        result.extend(generated)
        previous = generated
    return result


@frozen_nether
def selected_floor(game, body):
    current = record(game, body["m_nether_id"], body["m_nether_map_id"])
    data = run(game, active=False)
    level, index = number(body["floor_level"], 1, 1000), number(body["floor_index"], 1, 3)
    selected = next((row for row in data["floors"] if row["floor_level"] == level and row["floor_index"] == index), None)
    if selected is None:
        raise GameplayError("Nether floor was not generated for this expedition", 400, 4007)
    return current, data, selected


def floor_key(floor):
    return f"{floor['floor_level']}:{floor['floor_index']}"


def day(game):
    return (game.now - timedelta(hours=5)).date().isoformat()
