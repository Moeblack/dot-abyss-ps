from __future__ import annotations

import copy
import random

from .gameplay_battle_session import battle_state
from .gameplay_catalog import GameplayError, identifiers, number
from .gameplay_delivery import deliver
from .gameplay_economy import consume
from .gameplay_records import find, rows
from .gameplay_routes import route
from .gameplay_stamina import settle_stamina
from .gameplay_statistics import record as statistic
from .gameplay_nether_battle import clear_battle, close_battle, on_battle_suspended, start_battle
from .gameplay_nether_events import code_rows, erosion, fix_code, offer_codes, reload_code, resolve_floor, shop_history, update_event, update_shop, upgrade_sphere
from .gameplay_nether_party import snapshot_party
from .gameplay_nether_configuration import capture_configuration, frozen_nether
from .gameplay_nether_missions import mission_list, mission_receive
from .gameplay_nether_state import POINT_FIELDS, clear_items, day, ensure, entry_multiplier, floor_key, generate_map, held_items, hp_view, point_view, record, restrictions, reveal_routes, run, scope, selected_floor, state
from .gameplay_access import require_feature


@frozen_nether
def listing(game, body):
    data = state(game)
    if data["run"] is None or data["run"]["settled"]:
        require_feature(game, 5)
    current = ensure(game)
    expedition = data["run"]
    reveal_routes(game)
    stamina = settle_stamina(game.state, game.catalog, game.user_id, game.now)
    return {"t_nether_map_floors": copy.deepcopy(expedition["floors"]) if expedition else [],
            "t_nether_sphere_levels": copy.deepcopy(data["spheres"]), "t_nether_codes": copy.deepcopy(code_rows(game)),
            "party_detail": copy.deepcopy(expedition["party"]) if expedition else None,
            "t_nether_point": point_view(game), "t_nether_characters": hp_view(game),
            "stamina_max": stamina["capacity"], "stamina": stamina["amount"], "shop_histories": shop_history(game)}


@frozen_nether
def pay_entry(game, count):
    count = number(count, 0, 100)
    data = state(game)
    today = day(game)
    if count:
        consume(game, [{"content_type": 30, "content_id": 200002, "amount": count}])
        statistic(game.progress, game.now, 120018, count)
    else:
        used = data["free_entries"].get(today, 0)
        if used >= game.rules["nether_free_entries_per_day"]:
            raise GameplayError("No free Nether entries remain today")
        data["free_entries"] = {today: used + 1}
    return count


@frozen_nether
def start(game, body):
    require_feature(game, 5)
    definition, selected = scope(game, body["m_nether_id"], body["m_nether_map_id"], active=True)
    current, data = ensure(game), state(game)
    if data["run"] and not data["run"]["settled"]:
        raise GameplayError("Finish the current Nether expedition before starting again")
    if battle_state(game)["active"] is not None:
        raise GameplayError("Another battle is already active")
    party_no = number(body["party_no"], 1, 10)
    party = snapshot_party(game, party_no)
    starting = number(body["start_floor_level"], 0, selected["max_floor_num"])
    starting = max(1, starting + 1 if starting % 10 == 0 else starting)
    if starting > 1 and (starting - 1 > current["max_cleared_floor_level"]
                         or starting - 1 > point_view(game)["recovery_floor_level"] or (starting - 1) % 10):
        raise GameplayError("Requested Nether starting floor is not unlocked")
    paid = pay_entry(game, body["use_ticket"])
    identifier = data["next_run_id"]
    floors = generate_map(game, selected["id"], identifier)
    data["next_run_id"] += 1
    data["run"] = {"id": identifier, "map_id": selected["id"], "party_no": party_no, "party": party,
                   "hp": {str(row["m_character_id"]): 1000 for row in party["characters"]}, "floors": floors,
                   "visited": [], "resolved": [], "rewards": [], "code_draws": 0, "code_roll": 0,
                   "shop_purchases": {}, "cleared_count": 0, "event_battle": 0, "last_battle": None,
                   "last_continue": None, "settled": False, "base_score": 0, "bonus_score": 0,
                   "score_multiplier": entry_multiplier(game, paid), "configuration": capture_configuration(game)}
    data["last_result"] = None
    clear_items(game)
    for code in code_rows(game):
        code["amount"] = 0
    current.update(status=2, m_nether_id=definition["id"], m_nether_map_id=selected["id"],
                   m_nether_map_floor_id=0, floor_level=starting - 1, floor_index=2, code_reload=0,
                   erosion_point=0, m_nether_code_ids=[], nether_gold=point_view(game)["start_nether_gold"],
                   treasure_key=1, hidden_count=0, strong_enemy_count=0, used_ticket=paid, latest_event_number=0)
    current["used_ticket_day"] += paid
    reveal_routes(game)
    return {"t_nether_map_floors": copy.deepcopy(floors), "t_nether_characters": hp_view(game)}


@frozen_nether
def update(game, body):
    current, data, floor = selected_floor(game, body)
    if current["status"] not in (2, 3):
        raise GameplayError("Nether expedition cannot move in its current state")
    same = (current["floor_level"], current["floor_index"]) == (floor["floor_level"], floor["floor_index"])
    if not same:
        if current["status"] == 3 and current["floor_level"] % 10 == 0:
            raise GameplayError("Confirm Nether continuation before leaving the checkpoint")
        if current["m_nether_code_ids"]:
            raise GameplayError("Choose the pending Nether code before moving")
        previous_key = f"{current['floor_level']}:{current['floor_index']}"
        if current["m_nether_map_floor_id"] and previous_key not in data["resolved"]:
            raise GameplayError("Complete the current Nether floor before moving")
        if floor["floor_level"] != current["floor_level"] + 1:
            raise GameplayError("Nether floors cannot be skipped or rewound")
        if current["m_nether_map_floor_id"] and current["m_nether_map_floor_id"] not in floor["m_nether_map_floor_prev_ids"]:
            raise GameplayError("Nether floor is not connected to the previous floor")
        conditions = restrictions(game, floor)
        for restriction in conditions:
            if restriction["restrict_type"] != 1:
                raise GameplayError("Nether floor restriction is not supported", 503, 5036)
            if not restriction["restrict_param_1"] <= current["erosion_point"] <= restriction["restrict_param_2"]:
                raise GameplayError("Nether erosion requirement is not met")
        current.update(m_nether_map_floor_id=floor["m_nether_map_floor_id"], floor_level=floor["floor_level"], floor_index=floor["floor_index"], latest_event_number=0)
        data["visited"].append(floor_key(floor))
        if conditions:
            current["hidden_count"] += 1
            floor["is_hidden"] = 0
        erosion(game, 1)
        kind = game.catalog.get("m_nether_map_floors", floor["m_nether_map_floor_id"])["type"]
        if kind in (6, 8):
            resolve_floor(game, floor)
    opened = reveal_routes(game)
    return {"t_nether_map_floors": copy.deepcopy(data["floors"]), "t_nether_map_floor": opened}


@frozen_nether
def secure_items(game, selections):
    from .gameplay_nether_state import add_item

    if not isinstance(selections, list) or len(selections) > point_view(game)["lock_reward"]:
        raise GameplayError("Nether protected-item selection exceeds the unlocked capacity")
    seen = set()
    protected = {row["m_item_id"] for row in held_items(game, True) if row["amount"]}
    for selection in selections:
        if not isinstance(selection, dict) or set(selection) != {"m_item_id", "amount"}:
            raise GameplayError("Invalid protected Nether item", 400, 4007)
        identifier, amount = number(selection["m_item_id"]), number(selection["amount"])
        item = find(game.state, "t_nether_items", m_item_id=identifier)
        if identifier in seen or item is None or item["amount"] < amount:
            raise GameplayError("Protected Nether item is not available")
        seen.add(identifier)
        protected.add(identifier)
        if len(protected) > point_view(game)["lock_reward"]:
            raise GameplayError("Nether protected inventory is at capacity")
        item["amount"] -= amount
        add_item(game, identifier, amount, locked=True)


@frozen_nether
def continue_run(game, body):
    from .gameplay_receipts import operation_digest

    current = record(game, body["m_nether_id"], body["m_nether_map_id"])
    data = run(game, active=False)
    target = number(body["floor_level"], 0, 150)
    if target not in (current["floor_level"], current["floor_level"] + 1, current["continuance_floor_level"]):
        raise GameplayError("Nether continuation floor does not match the saved expedition")
    digest = operation_digest("api/nether/continue", "POST", body)
    if current["status"] == 2:
        if data.get("last_continue") == digest:
            return {}
        raise GameplayError("Nether expedition is already in progress")
    if current["status"] not in (3, 6, 7) or data["settled"]:
        raise GameplayError("Nether expedition cannot be continued")
    if battle_state(game)["active"] is not None:
        raise GameplayError("Another battle is still active")
    secure_items(game, body["lock_items"])
    paid = pay_entry(game, body["use_ticket"])
    data["score_multiplier"] = entry_multiplier(game, paid)
    current["used_ticket"] += paid
    current["used_ticket_day"] += paid
    if current["status"] == 7:
        data["hp"] = {key: 1000 for key in data["hp"]}
    current["status"] = 2
    data["last_continue"] = digest
    return {}


@frozen_nether
def cancel(game, body):
    data, current = state(game), ensure(game)
    active = battle_state(game)["active"]
    if active is not None and active["kind"] == "nether":
        battle_state(game)["active"] = None
    data["run"] = None
    current.update(status=1, floor_level=0, floor_index=2, m_nether_map_floor_id=0, m_nether_code_ids=[])
    clear_items(game)
    for code in code_rows(game):
        code["amount"] = 0
    return {}


@frozen_nether
def result(game, body):
    current = record(game, body["m_nether_id"], body["m_nether_map_id"])
    data = state(game)
    expedition = run(game, active=False)
    recover = number(body["use_item_flag"], 0, 1)
    if expedition["settled"]:
        return copy.deepcopy(data["last_result"])
    if current["m_nether_code_ids"] or expedition["code_draws"]:
        raise GameplayError("Choose the pending Nether codes before returning")
    if battle_state(game)["active"] is not None:
        raise GameplayError("Finish the active battle before returning from Nether")
    lost = current["status"] == 7
    if lost and recover:
        consume(game, [{"content_type": 30, "content_id": 200001, "amount": 1}])
    rewards = [{"content_type": 30, "content_id": row["m_item_id"], "amount": row["amount"]}
               for row in held_items(game, True) if row["amount"]]
    if not lost or recover:
        rewards.extend({"content_type": 30, "content_id": row["m_item_id"], "amount": row["amount"]}
                       for row in held_items(game) if row["amount"])
        rewards.extend(expedition["rewards"])
    score = number(expedition["base_score"] + expedition["bonus_score"], 0, 2 ** 31 - 1)
    codes = [game.catalog.get("m_nether_codes", row["m_nether_code_id"]) for row in code_rows(game) if row["amount"]]
    code_points = {}
    for category, name in enumerate(("Skill", "Attack", "ErosionResist", "ErosionUp"), 1):
        base = sum(row["amount"] * game.rules["nether_points_per_code"] for row in code_rows(game)
                   if game.catalog.get("m_nether_codes", row["m_nether_code_id"])["category"] == category)
        ratio = point_view(game)["sphere_point_ratio"] + sum(code["effect_parameter_2"] for code in codes
                 if code["effect_type"] == 12 and code["effect_parameter_1"] == category)
        amount = base + base * ratio // 1000
        code_points[name] = amount
        data["points"][POINT_FIELDS[category - 1]] += amount
    data["total_score"] += score
    statistic(game.progress, game.now, 120002, score)
    if not lost:
        from .gameplay_nether_missions import record_return

        record_return(game, expedition)
    received, mailed = deliver(game, rewards)
    clear_items(game)
    expedition["settled"] = True
    current["status"] = 8 if not lost else 7
    levels = [row for row in game.catalog.rows("m_nether_result_reward_levels") if row["m_nether_id"] == current["m_nether_id"] and row["total_score"] <= score]
    response = {"result_score": score, "result_boost_score": expedition["bonus_score"], "result_score_bonus": 1,
                "result_rank": max((row["score_level"] for row in levels), default=0),
                "result_rewards": [{**reward, "additional_info": None} for reward in received + mailed],
                "t_nether_point": point_view(game), "nether_code_points": code_points}
    data["last_result"] = copy.deepcopy(response)
    return response


@frozen_nether
def skip(game, body):
    current = record(game, body["m_nether_id"], body["m_nether_map_id"])
    data = run(game)
    starting = number(body["start_floor_level"], 0, 150)
    if starting not in (current["floor_level"], current["floor_level"] + 1):
        raise GameplayError("Nether skip start does not match the current floor")
    if current["status"] not in (2, 3) or battle_state(game)["active"] is not None or current["m_nether_code_ids"]:
        raise GameplayError("Current Nether floor cannot be skipped")
    if number(body["party_no"], 1, 10) != data["party_no"]:
        raise GameplayError("Nether skip party differs from the frozen expedition party")
    rare = number(body["is_rare"], 0, 1)
    secure_items(game, body["lock_items"])
    consume(game, [{"content_type": 30, "content_id": 200008 if rare else 200007, "amount": 1}])
    maximum = game.catalog.get("m_nether_maps", current["m_nether_map_id"])["max_floor_num"]
    target = min(maximum, current["floor_level"] + 10)
    for level in range(current["floor_level"] + 1, target + 1):
        floor = next(row for row in data["floors"] if row["floor_level"] == level)
        current.update(floor_level=level, floor_index=floor["floor_index"], m_nether_map_floor_id=floor["m_nether_map_floor_id"])
        data["visited"].append(floor_key(floor))
        resolve_floor(game, floor)
    offer_codes(game, 6 if rare else 3)
    drops = []
    if rare:
        from .gameplay_nether_state import add_item

        identifier = 200003 + min(3, (target - 1) // 40)
        add_item(game, identifier, 10)
        drops = [{"content_type": 31, "content_id": identifier, "amount": 10, "bonus_type": 0, "is_rare_drop": 0, "additional_info": None}]
    reveal_routes(game)
    return {"t_nether_map_floors": copy.deepcopy(data["floors"]), "t_nether_characters": hp_view(game),
            "party_detail": copy.deepcopy(data["party"]), "drops": drops}


NETHER_ROUTES = {
    "api/nether": route("GET", "", listing),
    "api/nether/start": route("POST", "m_nether_id m_nether_map_id party_no use_ticket start_floor_level", start),
    "api/nether/update": route("POST", "m_nether_id m_nether_map_id floor_level floor_index", update),
    "api/nether/start-battle": route("POST", "m_nether_id m_nether_map_id floor_level floor_index", start_battle),
    "api/nether/clear-battle": route("POST", "m_nether_id m_nether_map_id floor_level floor_index character_hps stage_results", clear_battle),
    "api/nether/close-battle": route("POST", "m_nether_id m_nether_map_id floor_level floor_index", close_battle),
    "api/nether/update-event": route("POST", "m_nether_id m_nether_map_id floor_level floor_index select_number m_nether_code_id", update_event),
    "api/nether/update-shop": route("POST", "shop_item_amount", update_shop),
    "api/nether/fix-code": route("POST", "m_nether_id m_nether_map_id m_nether_code_id remove_m_nether_code_id", fix_code),
    "api/nether/reload-code": route("POST", "", reload_code),
    "api/nether/upgrade-sphere": route("POST", "m_nether_id m_nether_map_id m_nether_sphere_board_id m_nether_sphere_id current_level", upgrade_sphere),
    "api/nether/continue": route("POST", "m_nether_id m_nether_map_id floor_level use_ticket lock_items", continue_run),
    "api/nether/result": route("POST", "m_nether_id m_nether_map_id use_item_flag", result),
    "api/nether/cancel": route("POST", "", cancel),
    "api/nether/skip": route("POST", "m_nether_id m_nether_map_id start_floor_level is_rare party_no lock_items", skip),
    "api/nether-mission/list": route("GET", "", mission_list),
    "api/nether-mission/reward/receive": route("POST", "m_mission_ids", mission_receive),
}
