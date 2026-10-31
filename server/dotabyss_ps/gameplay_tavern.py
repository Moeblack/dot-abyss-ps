from __future__ import annotations

import copy
import json
import secrets
from datetime import date

from .gameplay_catalog import GameplayError, number
from .gameplay_delivery import deliver
from .gameplay_economy import combined_costs, consume, text_argument
from .gameplay_library import intimacy_level, sync_novels
from .gameplay_receipts import feature_data
from .gameplay_records import find, put, rows
from .gameplay_routes import route
from .gameplay_stamina import recovery_day
from .gameplay_tavern_score import apply_card, draw_hand, score_turn
from .gameplay_access import staff_registered


def state(game):
    data = feature_data(game, "tavern")
    if not data:
        data.update(day="", free_used=0, assignments={}, active=None)
    if set(data) != {"day", "free_used", "assignments", "active"} or not isinstance(data["assignments"], dict) or not isinstance(data["day"], str):
        raise GameplayError("Stored tavern state is invalid")
    number(data["free_used"], 0, 2 ** 31 - 1)
    for key, members in data["assignments"].items():
        if not isinstance(key, str) or not key.isascii() or not key.isdecimal() or not isinstance(members, list) or len(members) > 3:
            raise GameplayError("Stored tavern assignments are invalid")
        for identifier in members:
            number(identifier, 0)
    if data["active"] is not None and not isinstance(data["active"], dict):
        raise GameplayError("Stored tavern session is invalid")
    cycle = recovery_day(game.now)
    day = "reset05:" + cycle.isoformat()
    if data["day"]:
        try:
            previous = date.fromisoformat(data["day"].removeprefix("reset05:"))
        except ValueError as error:
            raise GameplayError("Stored tavern reset date is invalid") from error
        if not data["day"].startswith("reset05:"):
            previous = cycle if previous == game.now.date() else previous
            data["day"] = "reset05:" + previous.isoformat()
            daily(game).update(day=int(previous.strftime("%Y%m%d")), day_of_week=(previous.weekday() + 1) % 7)
    if data["day"] != day and data["active"] is None:
        data.update(day=day, free_used=0)
        daily(game).update(day=int(cycle.strftime("%Y%m%d")), day_of_week=(cycle.weekday() + 1) % 7, worked_count=0,
                           selected_count=0, score=0, work_seed=0, selected_result="", use_ticket=0,
                           selected_cards={k: [] for k in ("selected_cards", "use_cards", "selected_character_cards", "use_character_cards")})
    if data["active"] is None:
        registered = {row["m_character_id"] for row in rows(game.state, "t_characters") if staff_registered(game, row)}
        data["assignments"] = {key: [identifier if identifier in registered else 0 for identifier in members]
                               for key, members in data["assignments"].items()}
    return data


def daily(game, identifier=None):
    record = rows(game.state, "t_tavern_daily_cards")[0]
    if identifier is not None and number(identifier) != record["id"]:
        raise GameplayError("Tavern daily card is not owned")
    return record


def shop(game):
    record = find(game.state, "t_tavern_shops", user_id=game.user_id)
    if record is None:
        record = put(game.state, "t_tavern_shops", {}, {"name": "Local Tavern", "work_level": 1, "card_level": 1,
                     "reward_level": 1, "total_play_count": 0, "max_score": 0}, game.user_id)
    skill_levels = {(r["m_building_id"], r["skill_type"]): r["skill_level"] for r in rows(game.state, "t_building_skills")}
    reached = {r["id"] for r in game.catalog.rows("m_building_skill_levels") if r["m_building_id"] == 200 and r["skill_level"] <= skill_levels.get((200, r["skill_type"]), 0)}
    for param, field in ((200, "work_level"), (210, "card_level"), (220, "reward_level")):
        values = [r["param_1"] for r in game.catalog.rows("m_building_skill_params") if r["m_building_skill_level_id"] in reached and r["param_type"] == param]
        record[field] = max([1, *values])
    return record


def shop_view(game):
    return {key: value for key, value in shop(game).items() if key != "id"}


def unlocked_works(game):
    level = shop(game)["work_level"]
    ids = {r["m_tavern_work_id"] for r in game.catalog.rows("m_tavern_work_levels") if r["work_level"] == level}
    return {r["id"]: r for r in game.catalog.rows("m_tavern_works") if r["id"] in ids}


def staff(game):
    assigned = {c for members in state(game)["assignments"].values() for c in members if c}
    result = []
    for character in rows(game.state, "t_characters"):
        level = intimacy_level(game, character)
        if staff_registered(game, character):
            result.append({"m_character_id": character["m_character_id"], "t_character_id": character["id"],
                           "job_level": max(1, level), "is_assign": int(character["m_character_id"] in assigned)})
    return result


def works(game):
    data = state(game)
    result = []
    for identifier in sorted(unlocked_works(game)):
        members = data["assignments"].get(str(identifier), [])
        result.append({"m_tavern_work_id": identifier, "status": int(data["active"] is not None),
                       **{f"t_character_id_{n + 1}": find(game.state, "t_characters", m_character_id=members[n])["id"] if n < len(members) and members[n] else 0 for n in range(3)}})
    return result


def listing(game, body):
    state(game)
    return {"tavern_shop": shop_view(game), "tavern_characters": staff(game), "tavern_works": works(game), "tavern_daily_cards": [copy.deepcopy(daily(game))]}


def interaction(game, body):
    sync_novels(game)
    return {"t_tavern_characters": [{**r, "visitor_amount": 0} for r in staff(game)]}


def set_assignments(game, assignments):
    data = state(game)
    if data["active"] is not None:
        raise GameplayError("Staff cannot change during active work")
    unlocked, available = unlocked_works(game), {r["m_character_id"] for r in staff(game)}
    selected = set()
    for key, members in assignments.items():
        identifier = int(key)
        if identifier not in unlocked or len(members) > unlocked[identifier]["max_employees"]:
            raise GameplayError("Work slot is closed or exceeds its capacity")
        for character in members:
            number(character, 0)
            if character and (character not in available or character in selected):
                raise GameplayError("Tavern character is locked, unowned or assigned twice")
            if character:
                selected.add(character)
    data["assignments"] = assignments


def set_characters(game, body):
    values = body["set_info"]
    if not isinstance(values, list) or len(values) > 27:
        raise GameplayError("Invalid tavern staff batch", 400, 4007)
    assignments = {}
    for value in values:
        if not isinstance(value, dict) or set(value) != {"m_tavern_work_id", "m_character_id"}:
            raise GameplayError("Invalid tavern staff fields", 400, 4007)
        assignments.setdefault(str(number(value["m_tavern_work_id"])), []).append(number(value["m_character_id"], 0))
    set_assignments(game, assignments)
    return {"tavern_characters": staff(game), "tavern_works": works(game)}


def set_character(game, body):
    identifier, position, character = number(body["m_tavern_work_id"]), number(body["position"], 1, 3), number(body["m_character_id"], 0)
    assignments = copy.deepcopy(state(game)["assignments"])
    for members in assignments.values():
        for i, existing in enumerate(members):
            if character and existing == character:
                members[i] = 0
    members = assignments.setdefault(str(identifier), [])
    members.extend([0] * (position - len(members)))
    members[position - 1] = character
    set_assignments(game, assignments)
    return {"tavern_characters": staff(game), "tavern_work": next(r for r in works(game) if r["m_tavern_work_id"] == identifier)}


def random_seeds(game, session):
    assigned = {r["character"] for r in session["workers"]}
    return [{"m_tavern_character_card_id": row["id"], "seed": (session["seed"] + session["turn"] * 1009 + row["id"]) % (2 ** 31 - 1)}
            for row in game.catalog.rows("m_tavern_character_cards") if row["m_character_id"] in assigned]


def hand(game, session):
    return [{"id": i + 1, "card_level": session["card_level"], "m_tavern_card_id": identifier} for i, identifier in enumerate(session["hand"])]


def start(game, body):
    data = state(game)
    record = daily(game, body["t_tavern_daily_card_id"])
    ticket = number(body["use_ticket"], 0, 1)
    if data["active"] is not None:
        session = data["active"]
        if record["use_ticket"] != ticket:
            raise GameplayError("A different tavern session is already active")
        return {"tavern_daily_card": copy.deepcopy(record), "tavern_cards": hand(game, session), "tavern_character_cards": staff(game), "random_seeds": random_seeds(game, session)}
    staff_by_id = {r["m_character_id"]: r for r in staff(game)}
    if any(c and c not in staff_by_id for members in data["assignments"].values() for c in members):
        raise GameplayError("An assigned character no longer meets the staff requirement")
    workers = [{"character": c, "work": int(key), "job_level": staff_by_id[c]["job_level"],
                "intimacy_level": intimacy_level(game, find(game.state, "t_characters", m_character_id=c)),
                "rate": 0, "flat": 0, "categories": [], "score": 0}
               for key, members in data["assignments"].items() for c in members if c]
    if not workers:
        raise GameplayError("Assign at least one staff member before working")
    if ticket:
        consume(game, [{"content_type": 30, "content_id": 40001, "amount": 1}])
    elif data["free_used"] >= game.rules["tavern_free_runs"]:
        raise GameplayError("No free tavern work remains today")
    else:
        data["free_used"] += 1
    building = shop(game)
    session = {"seed": secrets.randbelow(2 ** 31 - 2) + 1, "turn": 1, "max_turn": building["work_level"] + 2,
               "card_level": building["card_level"], "work_level": building["work_level"], "reward_level": building["reward_level"],
               "score_rules": {key: game.rules[key] for key in ("tavern_base_score", "tavern_combo_permille")},
               "workers": workers, "hand": [], "rerolls": 0, "character_used": False}
    session["score_rules"]["tavern_use_client_score_parameters"] = game.rules["tavern_use_client_score_parameters"]
    data["active"] = session
    draw_hand(game, session)
    record.update(work_seed=session["seed"], use_ticket=ticket, selected_count=0, selected_result="", score=0,
                  selected_cards={k: [] for k in ("selected_cards", "use_cards", "selected_character_cards", "use_character_cards")})
    return {"tavern_daily_card": copy.deepcopy(record), "tavern_cards": hand(game, session), "tavern_character_cards": staff(game), "random_seeds": random_seeds(game, session)}


def active(game, identifier):
    daily(game, identifier)
    session = state(game)["active"]
    if session is None:
        raise GameplayError("No tavern work is active")
    return session


def character_card(game, session, identifier):
    definition = game.catalog.get("m_tavern_character_cards", identifier)
    if session["character_used"] or definition["m_character_id"] not in {r["character"] for r in session["workers"]}:
        raise GameplayError("Character card is unavailable this turn")
    if find(game.state, "t_character_skins", m_character_skin_id=definition["m_character_skin_id"]) is None:
        raise GameplayError("Character card skin is not owned")
    apply_card(game, session, definition, session["seed"] + identifier + session["turn"] * 1009)
    session["character_used"] = True
    daily(game)["selected_cards"]["selected_character_cards"].append({"turn": session["turn"], "m_character_id": definition["m_character_id"]})


def finish(game, session):
    record, building = daily(game), shop(game)
    level = max(r["score_level"] for r in game.catalog.rows("m_tavern_score_levels") if r["work_level"] == session["work_level"] and r["total_score"] <= record["score"])
    mvp = max(session["workers"], key=lambda r: (r["score"], -r["character"]))
    union = game.catalog.get("m_characters", mvp["character"])["union_type"]
    rewards = [{k: r[k] for k in ("content_type", "content_id", "amount")} for r in game.catalog.rows("m_tavern_reward_levels")
               if r["reward_level"] == session["reward_level"] and r["result_level"] == level and r["target_union"] in (0, union)]
    received, mailed = deliver(game, rewards)
    building["total_play_count"] += 1
    building["max_score"] = max(building["max_score"], record["score"])
    record["worked_count"] += 1
    record["selected_result"] = json.dumps({"score": record["score"], "level": level}, separators=(",", ":"))
    record["work_seed"] = 0
    data = state(game)
    data["active"] = None
    cycle = recovery_day(game.now)
    day = "reset05:" + cycle.isoformat()
    if data["day"] != day:
        data.update(day=day, free_used=0)
        record.update(day=int(cycle.strftime("%Y%m%d")), day_of_week=(cycle.weekday() + 1) % 7, worked_count=0)
    return received + mailed, level, mvp["character"]


def play_turn(game, session, identifier):
    identifier = number(identifier)
    if identifier not in session["hand"]:
        raise GameplayError("Card was not offered in this turn")
    record = daily(game)
    record["selected_cards"]["use_cards"].extend({"turn": session["turn"], "m_tavern_card_id": c} for c in session["hand"])
    record["selected_cards"]["selected_cards"].append({"turn": session["turn"], "m_tavern_card_id": identifier})
    apply_card(game, session, game.catalog.get("m_tavern_cards", identifier), session["seed"] + session["turn"])
    record["score"] = number(record["score"] + score_turn(game, session), 0, 2 ** 31 - 1)
    record["selected_count"] += 1
    ended = session["turn"] >= session["max_turn"]
    rewards, level, mvp = finish(game, session) if ended else ([], 0, 0)
    if not ended:
        session["turn"] += 1
        session["character_used"] = False
        draw_hand(game, session)
    return {"tavern_daily_card": copy.deepcopy(record), "tavern_cards": [] if ended else hand(game, session),
            "is_work_end": int(ended), "result_rewards": rewards, "result_score": record["score"], "result_level": level}, mvp


def select_card(game, body):
    session = active(game, body["t_tavern_daily_card_id"])
    result, _ = play_turn(game, session, body["m_tavern_card_id"])
    return {**result, "tavern_character_cards": staff(game)}


def select_character(game, body):
    session = active(game, body["t_tavern_daily_card_id"])
    identifier = number(body["m_character_id"])
    character = find(game.state, "t_characters", m_character_id=identifier)
    definitions = [r for r in game.catalog.rows("m_tavern_character_cards") if r["m_character_id"] == identifier and character and r["m_character_skin_id"] == character["tavern_m_character_skin_id"]]
    if not definitions:
        raise GameplayError("No character card matches the equipped tavern skin")
    character_card(game, session, min(definitions, key=lambda r: r["id"])["id"])
    if "reload" in session:
        session["rerolls"] += 1
        draw_hand(game, session)
    return {"tavern_daily_card": copy.deepcopy(daily(game)), "tavern_cards": hand(game, session), "tavern_character_cards": staff(game)}


def select_cards(game, body):
    session = active(game, body["t_tavern_daily_card_id"])
    targets = body["random_target_ids"]
    if targets is not None:
        if not isinstance(targets, dict) or set(targets) != {"tavern_character_card", "tavern_card"}:
            raise GameplayError("Invalid tavern random-target fields", 400, 4007)
        for values in targets.values():
            if not isinstance(values, list) or len(values) > 27 or any(type(v) is not int or not 0 <= v <= 2 ** 31 - 1 for v in values):
                raise GameplayError("Invalid tavern random targets", 400, 4007)
    identifier = number(body["m_tavern_character_card_id"], 0)
    if identifier:
        character_card(game, session, identifier)
    result, _ = play_turn(game, session, body["m_tavern_card_id"])
    return {**result, "tavern_shop": shop_view(game), "random_seeds": [] if result["is_work_end"] else random_seeds(game, session)}


def skip(game, body):
    count = number(body["exec_count"], 1, game.rules["tavern_max_skip"])
    ticket = number(body["use_ticket"], 0, 1)
    if state(game)["active"] is not None:
        raise GameplayError("Finish the active work before skipping another run")
    if not ticket and state(game)["free_used"] + count > game.rules["tavern_free_runs"]:
        raise GameplayError("Skip count exceeds remaining free work")
    rewards, last, mvp = [], None, 0
    for _ in range(count):
        start(game, {"use_ticket": ticket, "t_tavern_daily_card_id": body["t_tavern_daily_card_id"]})
        session = active(game, body["t_tavern_daily_card_id"])
        while state(game)["active"] is not None:
            last, mvp = play_turn(game, session, session["hand"][0])
        rewards.extend(last["result_rewards"])
    return {"result_rewards": combined_costs(rewards), "result_score": last["result_score"], "result_level": last["result_level"],
            "combo_map": [{"combo": combo, "character_count": sum(max(0, len(w["categories"]) - 1) == combo for w in session["workers"])} for combo in (0, 1, 2)], "mvp_m_character_id": mvp}


def rename(game, body):
    shop(game)["name"] = text_argument(body["name"], game.rules["max_party_name_length"])
    return {"tavern_shop": shop_view(game)}


TAVERN_ROUTES = {
    "api/tavern": route("GET", "", listing),
    "api/interaction/top": route("GET", "", interaction),
    "api/tavern/set/character": route("POST", "m_tavern_work_id position m_character_id", set_character),
    "api/tavern/set/characters": route("POST", "set_info", set_characters),
    "api/tavern/exec/work": route("POST", "use_ticket t_tavern_daily_card_id", start),
    "api/tavern/select/card": route("POST", "t_tavern_daily_card_id m_tavern_card_id", select_card),
    "api/tavern/select/character-card": route("POST", "m_character_id t_tavern_daily_card_id", select_character),
    "api/tavern/select/cards": route("POST", "t_tavern_daily_card_id m_tavern_card_id m_tavern_character_card_id random_target_ids", select_cards),
    "api/tavern/skip/work": route("POST", "use_ticket t_tavern_daily_card_id exec_count", skip),
    "api/tavern/update/name": route("POST", "name", rename),
}
