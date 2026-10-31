from __future__ import annotations

from datetime import datetime, timedelta

from .gameplay_catalog import GameplayError, JST, number, parse_timestamp
from .gameplay_content import weighted_choice
from .gameplay_delivery import deliver
from .gameplay_economy import combined_costs, consume, owned, text_argument
from .gameplay_library import chapter_complete
from .gameplay_party_state import put_party_row, validate_equipment
from .gameplay_receipts import feature_data
from .gameplay_records import find, put, rows
from .gameplay_routes import route
from .gameplay_effects import idle_capacity, idle_reward_amount, vip_bonus, extra_drop_bonus


def status(game):
    return rows(game.state, "t_idle_exploration_statuses")[0]


def progress(game):
    data = feature_data(game, "idle")
    if not data:
        data.update(pending=[], logs=[], next_sequence=1, carry={})
    if set(data) - {"pending", "logs", "next_sequence", "carry", "initial_assignment_checked"} or not {"pending", "logs", "next_sequence", "carry"} <= set(data) or not isinstance(data["pending"], list) or not isinstance(data["logs"], list) or not isinstance(data["carry"], dict):
        raise GameplayError("Stored idle exploration state is invalid")
    if "initial_assignment_checked" in data and type(data["initial_assignment_checked"]) is not bool:
        raise GameplayError("Stored initial exploration assignment is invalid")
    number(data["next_sequence"])
    return data


def stamp(value):
    try:
        result = parse_timestamp(value)
    except (TypeError, ValueError) as error:
        raise GameplayError("Stored exploration timestamp is invalid") from error
    return result if result.tzinfo else result.replace(tzinfo=JST)


def reconcile(game, *, settle_pending=True):
    if settle_pending:
        settle(game)
    current = status(game)
    capacity = idle_capacity(game)
    for relation in list(rows(game.state, "t_idle_exploration_party_relations")):
        if relation["party_no"] > capacity:
            remove_relation(game, relation)
    duration = game.rules["idle_max_seconds"] + vip_bonus(game, 2)
    difference = duration - current["explorable_duration"]
    for relation in rows(game.state, "t_idle_exploration_party_relations"):
        if difference and stamp(relation["exploring_stop_at"]) > game.now:
            relation["exploring_stop_at"] = max(game.now, stamp(relation["exploring_stop_at"]) + timedelta(seconds=difference)).isoformat()
    current["explorable_duration"] = duration
    difficulties = game.catalog.rows("m_idle_exploration_map_area_difficulties")
    first_area = min(r["m_idle_exploration_map_area_id"] for r in difficulties)
    unlocked = {min((r for r in difficulties if r["m_idle_exploration_map_area_id"] == first_area), key=lambda r: r["difficulty"])["id"]}
    points = 0
    for area in game.catalog.rows("m_chapter_areas"):
        quests = [r for r in game.catalog.rows("m_chapter_quests") if r["m_chapter_area_id"] == area["id"]]
        if quests and all(chapter_complete(game, r["id"]) for r in quests):
            points += area["exploration_point"]
            if area["release_m_idle_exploration_map_area_difficulty_id"]:
                unlocked.add(area["release_m_idle_exploration_map_area_difficulty_id"])
    if game.rules["idle_unlock_all_areas"]:
        unlocked.update(r["id"] for r in difficulties)
    for identifier in sorted(unlocked):
        difficulty = game.catalog.get("m_idle_exploration_map_area_difficulties", identifier)
        area = game.catalog.get("m_idle_exploration_map_areas", difficulty["m_idle_exploration_map_area_id"])
        previous = find(game.state, "t_idle_exploration_map_areas", m_idle_exploration_map_area_id=area["id"])
        put(game.state, "t_idle_exploration_map_areas", {"m_idle_exploration_map_area_id": area["id"]},
            {"difficulty": max(difficulty["difficulty"], previous["difficulty"] if previous else 0)}, game.user_id)
        put(game.state, "t_idle_exploration_maps", {"m_idle_exploration_map_id": area["m_idle_exploration_map_id"]}, {}, game.user_id)
    before = current["level"]
    current["point"] = max(current["point"], points)
    current["level"] = max(r["level"] for r in game.catalog.rows("m_idle_exploration_levels") if r["require_point"] <= current["point"])
    for party_no in range(1, capacity + 1):
        if find(game.state, "t_idle_exploration_parties", party_no=party_no) is None:
            put(game.state, "t_idle_exploration_parties", {"party_no": party_no}, {"name": f"Party {party_no}", "icon_1": 0, "icon_2": 0, "icon_3": 0})
            for position in range(1, 11):
                put_party_row(game, "t_idle_exploration_party_characters", {"party_no": party_no, "position_no": position}, {"t_character_id": 0, "t_weapon_id": 0}, game.user_id)
    update_exploring_state(game)
    return before, current["level"]


def world(game):
    return reconcile(game)


def encounter_capacity(game):
    level = next((row for row in game.catalog.rows("m_idle_exploration_levels") if row["level"] == status(game)["level"]), None)
    if level is None:
        raise GameplayError("Encounter level settings are missing", 503, 5036)
    return number(level["encounter_quest_slot_count"] + vip_bonus(game, 6), 0, 100)


def update_exploring_state(game):
    relations = rows(game.state, "t_idle_exploration_party_relations")
    current = status(game)
    if not relations:
        current.update(exploring_state=0, exploring_start_at=None)
    else:
        current["exploring_state"] = 1 if any(stamp(row["exploring_stop_at"]) > game.now for row in relations) else 5


def party(game, value):
    identifier = number(value, 1, idle_capacity(game))
    record = find(game.state, "t_idle_exploration_parties", party_no=identifier)
    if record is None:
        raise GameplayError("Idle party does not exist")
    return record


def drop_rule(game, relation):
    area = find(game.state, "t_idle_exploration_map_areas", m_idle_exploration_map_area_id=relation["m_idle_exploration_map_area_id"])
    if area is None:
        raise GameplayError("Assigned exploration area is not open")
    definitions = [r for r in game.catalog.rows("m_idle_exploration_map_area_difficulties")
                   if r["m_idle_exploration_map_area_id"] == area["m_idle_exploration_map_area_id"] and r["difficulty"] <= area["difficulty"]]
    definition = max(definitions, key=lambda r: r["difficulty"])
    rule = game.catalog.get("m_idle_exploration_drop_rules", definition["m_idle_exploration_drop_rule_id"])
    number(rule["drop_interval"], 1, 86400)
    return rule


def base_rewards(game):
    level = next(r for r in game.catalog.rows("m_idle_exploration_levels") if r["level"] == status(game)["level"])
    rewards = [{key: row[key] for key in ("content_type", "content_id", "amount")} for row in game.catalog.rows("m_idle_exploration_explore_rewards")
               if row["group_id"] == level["explore_reward_group_id"]]
    if not rewards:
        raise GameplayError("Exploration reward group is missing", 503, 5036)
    return level, rewards


def credit(game, relation, ticks, *, random_drops=True, first_tick_at=None):
    if not ticks:
        return
    data = progress(game)
    level, rewards = base_rewards(game)
    rule = drop_rule(game, relation)
    for tick in range(ticks):
        at = game.now if first_tick_at is None else first_tick_at + timedelta(seconds=tick * rule["drop_interval"])
        data["pending"].extend({**r, "amount": idle_reward_amount(game, r["content_type"], r["amount"], at)} for r in rewards)
        if not random_drops:
            continue
        relation["drop_guaranty_count"] += 1
        rare = rule["rare_drop_guaranty"] > 0 and relation["drop_guaranty_count"] >= rule["rare_drop_guaranty"]
        if not rare and rule["rare_drop_probability"]:
            rare = weighted_choice([{"rare": True, "weight": rule["rare_drop_probability"]}, {"rare": False, "weight": max(0, 10000 - rule["rare_drop_probability"])}])["rare"]
        group_id = rule["m_idle_exploration_drop_group_id_rare" if rare else "m_idle_exploration_drop_group_id_normal"]
        options = [r for r in game.catalog.rows("m_idle_exploration_view_drops") if r["group_id"] == group_id and r["border_item_rank_low"] <= level["item_rank"] <= r["border_item_rank_high"]]
        if not options:
            continue
        selected = weighted_choice([{**r, "weight": 1} for r in options])
        reward = {"content_type": selected["content_type"], "content_id": selected["content_id"], "amount": 1}
        data["pending"].append(reward)
        from .gameplay_drop_bonuses import party_bonus
        bonus = min(1000, extra_drop_bonus(game, at) + party_bonus(game, relation["party_no"], idle=True))
        if bonus and weighted_choice([{"extra": True, "weight": bonus}, {"extra": False, "weight": 1000 - bonus}])["extra"]:
            data["pending"].append(dict(reward))
        if rare:
            relation["drop_guaranty_count"] = 0
            status(game)["has_rare_drop"] = 1
        data["logs"].append({"sequence": str(data["next_sequence"]), "type": 100, "bind": {
            "party_no": relation["party_no"], "content_type": reward["content_type"], "content_id": reward["content_id"], "is_rare": int(rare),
            "m_encounter_quest_id": 0, "m_idle_exploration_map_area_id": relation["m_idle_exploration_map_area_id"], "m_idle_exploration_request_id": 0}})
        data["next_sequence"] += 1
    data["logs"] = data["logs"][-100:]
    data["pending"] = combined_costs(data["pending"])
    status(game)["stacked_treasure"] = number(status(game)["stacked_treasure"] + ticks, 0, 2 ** 31 - 1)


def settle(game):
    progress(game)
    for relation in rows(game.state, "t_idle_exploration_party_relations"):
        cursor, end = stamp(relation["last_drop_at"]), min(game.now, stamp(relation["exploring_stop_at"]))
        if end <= cursor:
            continue
        interval = drop_rule(game, relation)["drop_interval"]
        ticks = int((end - cursor).total_seconds()) // interval
        if ticks > game.rules["idle_max_settle_ticks"]:
            raise GameplayError("Exploration timer exceeds the settlement limit")
        credit(game, relation, ticks, first_tick_at=cursor + timedelta(seconds=interval))
        relation["last_drop_at"] = (cursor + timedelta(seconds=ticks * interval)).isoformat()
    update_exploring_state(game)
    from .gameplay_encounter import settle_encounters

    settle_encounters(game)


def listing(game, body):
    sequence = body["sequence"]
    if not isinstance(sequence, str) or len(sequence) > 19 or sequence and not sequence.isascii() or sequence and not sequence.isdecimal():
        raise GameplayError("Invalid exploration log sequence", 400, 4007)
    before, after = world(game)
    initialize_assignment(game)
    settle(game)
    logs = [r for r in progress(game)["logs"] if int(r["sequence"]) > int(sequence or 0)]
    from .gameplay_encounter_campaign import joined_campaigns

    return {"log_messages": logs, "t_encounter_campaigns": joined_campaigns(game), "update_exploration_levels": {"before_level": before, "after_level": after}}


def initialize_assignment(game):
    data = progress(game)
    if not game.rules["idle_auto_start"] or data.get("initial_assignment_checked"):
        return
    if (rows(game.state, "t_idle_exploration_party_relations")
            or any(row["t_character_id"] for row in rows(game.state, "t_idle_exploration_party_characters"))
            or data["pending"] or data["logs"] or data["carry"]):
        data["initial_assignment_checked"] = True
        return
    characters = rows(game.state, "t_characters")
    if not characters:
        return
    selected = []
    main_party = find(game.state, "t_exploration_parties", party_no=1)
    if main_party:
        selected = [row["t_character_id"] for row in sorted(rows(game.state, "t_exploration_party_characters"), key=lambda row: row["position_no"])
                    if row["t_exploration_party_id"] == main_party["id"] and row["t_character_id"]]
    if not selected:
        selected = [min(characters, key=lambda row: row["id"])["id"]]
    members = [{"position_no": index, "t_character_id": identifier, "t_weapon_id": 0}
               for index, identifier in enumerate(selected[:10], 1)]
    party_edit(game, {"party_no": 1, "characters": members})
    area_id = min(row["m_idle_exploration_map_area_id"] for row in rows(game.state, "t_idle_exploration_map_areas"))
    assign(game, {"party_no": 1, "assign_to": area_id})
    data["initial_assignment_checked"] = True


def remove_relation(game, relation):
    elapsed = max(0, int((min(game.now, stamp(relation["exploring_stop_at"])) - stamp(relation["last_drop_at"])).total_seconds()))
    progress(game)["carry"][str(relation["party_no"])] = {
        "seconds": elapsed, "interval": drop_rule(game, relation)["drop_interval"],
        "area": relation["m_idle_exploration_map_area_id"], "guaranty": relation["drop_guaranty_count"]}
    rows(game.state, "t_idle_exploration_party_relations").remove(relation)


def party_profile(game, body):
    world(game)
    record = party(game, body["party_no"])
    record.update(name=text_argument(body["name"], game.rules["max_party_name_length"]),
                  **{key: number(body[key], 0, 2 ** 31 - 1) for key in ("icon_1", "icon_2", "icon_3")})
    return {}


def party_edit(game, body):
    world(game)
    number_value = party(game, body["party_no"])["party_no"]
    members = body["characters"]
    if not isinstance(members, list) or len(members) > 10:
        raise GameplayError("Idle party has too many members", 400, 4007)
    positions, selected, weapons = {}, set(), set()
    for member in members:
        if not isinstance(member, dict) or set(member) != {"position_no", "t_character_id", "t_weapon_id"}:
            raise GameplayError("Invalid idle party member fields", 400, 4007)
        position, character_id, weapon_id = number(member["position_no"], 1, 10), number(member["t_character_id"], 0), number(member["t_weapon_id"], 0)
        if position in positions or character_id and character_id in selected or weapon_id and weapon_id in weapons:
            raise GameplayError("Duplicated idle party slot, character or weapon", 400, 4007)
        character = owned(game, "t_characters", character_id) if character_id else None
        if weapon_id:
            if character is None:
                raise GameplayError("An empty slot cannot hold a weapon")
            validate_equipment(game, character, "t_weapon_id", owned(game, "t_weapons", weapon_id))
            weapons.add(weapon_id)
        if character_id:
            selected.add(character_id)
        positions[position] = member
    settle(game)
    for record in rows(game.state, "t_idle_exploration_party_characters"):
        if record["party_no"] != number_value:
            if record["t_character_id"] in selected:
                record.update(t_character_id=0, t_weapon_id=0)
            elif record["t_weapon_id"] and record["t_weapon_id"] in weapons:
                record["t_weapon_id"] = 0
    for position in range(1, 11):
        value = positions.get(position, {"t_character_id": 0, "t_weapon_id": 0})
        put_party_row(game, "t_idle_exploration_party_characters", {"party_no": number_value, "position_no": position},
                      {key: value[key] for key in ("t_character_id", "t_weapon_id")}, game.user_id)
    relations = rows(game.state, "t_idle_exploration_party_relations")
    for relation in list(relations):
        if not any(m["t_character_id"] and m["party_no"] == relation["party_no"] for m in rows(game.state, "t_idle_exploration_party_characters")):
            remove_relation(game, relation)
    if not relations:
        status(game).update(exploring_state=0, exploring_start_at=None)
    return {}


def assign(game, body):
    world(game)
    number_value = party(game, body["party_no"])["party_no"]
    area_id = number(body["assign_to"], 0)
    if area_id and find(game.state, "t_idle_exploration_map_areas", m_idle_exploration_map_area_id=area_id) is None:
        raise GameplayError("Exploration area is not unlocked")
    if area_id and not any(r["party_no"] == number_value and r["t_character_id"] for r in rows(game.state, "t_idle_exploration_party_characters")):
        raise GameplayError("An empty party cannot explore")
    settle(game)
    relation = find(game.state, "t_idle_exploration_party_relations", party_no=number_value)
    if relation is not None and area_id == relation["m_idle_exploration_map_area_id"] and stamp(relation["exploring_stop_at"]) > game.now:
        return {}
    if relation is not None:
        remove_relation(game, relation)
    if area_id:
        for other in list(rows(game.state, "t_idle_exploration_party_relations")):
            if other["m_idle_exploration_map_area_id"] == area_id:
                remove_relation(game, other)
        relation = put_party_row(game, "t_idle_exploration_party_relations", {"party_no": number_value}, {
            "m_idle_exploration_map_area_id": area_id, "drop_guaranty_count": 0,
            "exploring_stop_at": (game.now + timedelta(seconds=status(game)["explorable_duration"])).isoformat(),
            "last_drop_at": game.now.isoformat(), "last_encount_at": game.now.isoformat()})
        carry = progress(game)["carry"].pop(str(number_value), None)
        if carry:
            if not isinstance(carry, dict) or set(carry) != {"seconds", "interval", "area", "guaranty"}:
                raise GameplayError("Stored exploration remainder is invalid")
            previous_interval = number(carry["interval"], 1, 86400)
            remainder = number(carry["seconds"], 0, previous_interval - 1)
            seconds = remainder * drop_rule(game, relation)["drop_interval"] // previous_interval
            relation["last_drop_at"] = (game.now - timedelta(seconds=seconds)).isoformat()
            if carry["area"] == area_id:
                relation["drop_guaranty_count"] = number(carry["guaranty"], 0, 2 ** 31 - 1)
        status(game).update(exploring_state=1, exploring_start_at=game.now.isoformat())
    elif not rows(game.state, "t_idle_exploration_party_relations"):
        status(game).update(exploring_state=0, exploring_start_at=None)
    return {}


def drop_contents(rewards):
    return [{**r, "bonus_type": 0, "is_rare_drop": 0, "additional_info": None} for r in rewards]


def receive(game, body):
    world(game)
    settle(game)
    data = progress(game)
    granted, _ = deliver(game, data["pending"])
    data["pending"] = []
    status(game).update(stacked_treasure=0, has_rare_drop=0)
    relations = rows(game.state, "t_idle_exploration_party_relations")
    for relation in relations:
        if stamp(relation["exploring_stop_at"]) <= game.now:
            relation.update(last_drop_at=game.now.isoformat(), last_encount_at=game.now.isoformat())
        relation["exploring_stop_at"] = (game.now + timedelta(seconds=status(game)["explorable_duration"])).isoformat()
    if relations:
        status(game)["exploring_state"] = 1
    return {"grant_character_exp": sum(r["amount"] for r in granted if r["content_type"] == 110),
            "grant_gold": sum(r["amount"] for r in granted if r["content_type"] == 50),
            "drops": drop_contents([r for r in granted if r["content_type"] not in (50, 110)])}


def boost(game, body):
    world(game)
    boosts = body["boosts"]
    if not isinstance(boosts, list) or not 1 <= len(boosts) <= idle_capacity(game):
        raise GameplayError("Invalid exploration boost batch", 400, 4007)
    changes, costs, selected = [], [], set()
    for value in boosts:
        if not isinstance(value, dict) or set(value) != {"party_no", "use_items"}:
            raise GameplayError("Invalid exploration boost fields", 400, 4007)
        number_value = party(game, value["party_no"])["party_no"]
        relation = find(game.state, "t_idle_exploration_party_relations", party_no=number_value)
        if relation is None or stamp(relation["exploring_stop_at"]) <= game.now or number_value in selected:
            raise GameplayError("Boost requires a distinct active party")
        selected.add(number_value)
        items = value["use_items"]
        if not isinstance(items, list) or not 1 <= len(items) <= 100:
            raise GameplayError("Invalid boost items", 400, 4007)
        ticks = 0
        for item in items:
            if not isinstance(item, dict) or set(item) != {"m_item_id", "amount"}:
                raise GameplayError("Invalid boost item fields", 400, 4007)
            definition = game.catalog.get("m_items", number(item["m_item_id"]))
            amount = number(item["amount"], 1, game.rules["item_max_batch"])
            if definition["type"] != 60:
                raise GameplayError("Item is not an exploration booster")
            ticks += definition["value"] * amount
            costs.append({"content_type": 30, "content_id": definition["id"], "amount": amount})
        changes.append((relation, number(ticks, 1, game.rules["idle_max_boost_ticks"])))
    consume(game, costs)
    settle(game)
    for relation, ticks in changes:
        credit(game, relation, ticks)
    return receive(game, {})


def instant_item(game, body):
    world(game)
    definition = game.catalog.get("m_items", number(body["m_item_id"]))
    amount = number(body["amount"], 1, game.rules["item_max_batch"])
    if definition["type"] not in (61, 62, 63):
        raise GameplayError("Item is not an instant exploration resource")
    active = [r for r in rows(game.state, "t_idle_exploration_party_relations") if stamp(r["exploring_stop_at"]) > game.now]
    if not active:
        raise GameplayError("Instant exploration resources require an active party")
    seconds = number(definition["value"] * amount, 1, 2 ** 31 - 1)
    ticks = sum((seconds + drop_rule(game, relation)["drop_interval"] - 1) // drop_rule(game, relation)["drop_interval"] for relation in active)
    _, base = base_rewards(game)
    kind = {61: 50, 62: 110, 63: 30}[definition["type"]]
    rewards = [{**r, "amount": number(idle_reward_amount(game, r["content_type"], r["amount"]) * ticks, 1, 2 ** 31 - 1)} for r in base if r["content_type"] == kind]
    consume(game, [{"content_type": 30, "content_id": definition["id"], "amount": amount}])
    received, _ = deliver(game, rewards)
    return {"receive_contents": drop_contents(received)}


IDLE_ROUTES = {
    "api/idle-exploration": route("POST", "sequence", listing),
    "api/idle-exploration/receive": route("POST", "", receive),
    "api/idle-exploration/boost": route("POST", "boosts", boost),
    "api/idle-exploration/instant-item": route("POST", "m_item_id amount", instant_item),
    "api/party/idle-exploration/edit": route("POST", "party_no characters", party_edit),
    "api/party/idle-exploration/edit/profile": route("POST", "party_no name icon_1 icon_2 icon_3", party_profile),
    "api/party/idle-exploration/assign": route("POST", "party_no assign_to", assign),
}
