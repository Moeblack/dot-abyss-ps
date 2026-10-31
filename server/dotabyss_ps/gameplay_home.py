from __future__ import annotations

import copy
import secrets
from datetime import date, timedelta

from .gameplay_catalog import GameplayError, JST, available, identifiers, number, parse_timestamp
from .gameplay_content import weighted_choice
from .gameplay_delivery import deliver
from .gameplay_records import find, put, rows
from .gameplay_receipts import feature_data
from .gameplay_routes import route


def home_data(game):
    data = feature_data(game, "home")
    if not data:
        data.update(visitor_day="", visitor_ids=[], visitor_claims={})
    required = {"visitor_day", "visitor_ids", "visitor_claims"}
    if (not required <= data.keys() or data.keys() - required - {"greeting_cycle"}
            or not isinstance(data["visitor_ids"], list) or not isinstance(data["visitor_claims"], dict)):
        raise GameplayError("Stored home visitor state is invalid")
    identifiers(data["visitor_ids"], 100)
    if "greeting_cycle" in data:
        validate_cycle(game, data["greeting_cycle"])
    return data


def period(game):
    hour = game.rules["home_greeting_reset_hour"]
    shifted = game.now.astimezone(JST) - timedelta(hours=hour)
    start = shifted.replace(hour=hour, minute=0, second=0, microsecond=0)
    return shifted.date().isoformat(), start + timedelta(days=1)


def validate_cycle(game, cycle):
    if not isinstance(cycle, dict) or set(cycle) != {"day", "ends_at", "entries", "claims"}:
        raise GameplayError("Stored greeting cycle is invalid")
    try:
        started = date.fromisoformat(cycle["day"])
        end = parse_timestamp(cycle["ends_at"])
        if started.isoformat() != cycle["day"] or end.astimezone(JST).date() != started + timedelta(days=1):
            raise ValueError("Inconsistent cycle dates")
    except (ValueError, TypeError, AttributeError, OverflowError) as error:
        raise GameplayError("Stored greeting cycle dates are invalid") from error
    if not isinstance(cycle["entries"], list) or len(cycle["entries"]) > 12 or not isinstance(cycle["claims"], dict):
        raise GameplayError("Stored greeting selection is invalid")
    selected, characters, positions = set(), set(), set()
    for entry in cycle["entries"]:
        if not isinstance(entry, dict) or set(entry) != {"id", "pos_x", "pos_z"}:
            raise GameplayError("Stored greeting entry is invalid")
        definition = game.catalog.get("m_home_daily_greetings", number(entry["id"]))
        point = entry["pos_x"], entry["pos_z"]
        if any(type(value) is not int or abs(value) > 100000 for value in point):
            raise GameplayError("Stored greeting position is invalid")
        if entry["id"] in selected or definition["m_character_id"] in characters or point in positions:
            raise GameplayError("Stored greeting selection is duplicated")
        selected.add(entry["id"])
        characters.add(definition["m_character_id"])
        positions.add(point)
    for key, rewards in cycle["claims"].items():
        if not isinstance(key, str) or not key.isascii() or not key.isdecimal() or str(int(key)) != key or int(key) not in selected:
            raise GameplayError("Stored greeting claim does not belong to its cycle")
        if not isinstance(rewards, list) or not rewards:
            raise GameplayError("Stored greeting reward is invalid")
        for reward in rewards:
            if not isinstance(reward, dict) or set(reward) != {"content_type", "content_id", "amount"}:
                raise GameplayError("Stored greeting reward fields are invalid")
            number(reward["content_type"], 1, 2 ** 31 - 1)
            number(reward["content_id"], 0)
            number(reward["amount"], 1, 2 ** 31 - 1)


def story_received(game, greeting):
    novel = greeting["m_novel_home_id"]
    return bool(novel and find(game.state, "t_novel_homes", m_novel_home_id=novel) is not None)


def candidates(game, excluded=()):
    selected = {}

    def priority(row):
        return not bool(row["m_novel_home_id"]), row["intimacy_level"], row["id"]

    for entry in game.catalog.rows("m_home_daily_greetings"):
        if entry["m_character_id"] in excluded or not eligible(game, entry) or story_received(game, entry):
            continue
        old = selected.get(entry["m_character_id"])
        if old is None or priority(entry) < priority(old):
            selected[entry["m_character_id"]] = entry
    return sorted(selected.values(), key=lambda row: row["id"])


def append_entry(game, cycle, definition):
    occupied = {(entry["pos_x"], entry["pos_z"]) for entry in cycle["entries"]}
    positions = [row for row in game.catalog.rows("m_home_greeting_positions") if (row["pos_x"], row["pos_z"]) not in occupied]
    if not positions:
        raise GameplayError("No distinct greeting positions remain", 503, 5036)
    position = next((row for row in positions if row["id"] == definition["m_home_greeting_position_id"]), None)
    if position is None:
        position = positions[secrets.randbelow(len(positions))]
    cycle["entries"].append({"id": definition["id"], "pos_x": position["pos_x"], "pos_z": position["pos_z"]})


def greeting_cycle(game):
    data = home_data(game)
    today, end = period(game)
    cycle = data.get("greeting_cycle")
    if cycle is not None and (game.now < parse_timestamp(cycle["ends_at"]) or today <= cycle["day"]):
        if today == cycle["day"] and game.now >= parse_timestamp(cycle["ends_at"]):
            cycle["ends_at"] = end.isoformat()
        if not cycle["entries"]:
            populate_cycle(game, cycle)
        return cycle
    legacy = cycle is None
    cycle = {"day": today, "ends_at": end.isoformat(), "entries": [], "claims": {}}
    limit = game.rules["home_greeting_limit"]
    if legacy:
        days = {today, game.now.astimezone(JST).date().isoformat()}
        claims = {}
        for key, reward in game.progress["greeting_claims"].items():
            day, identifier = key.rsplit(":", 1)
            if day in days:
                claims[int(identifier)] = reward
        for identifier in sorted(claims):
            definition = game.catalog.get("m_home_daily_greetings", identifier)
            if len(cycle["entries"]) >= limit:
                break
            if definition["m_character_id"] in {game.catalog.get("m_home_daily_greetings", entry["id"])["m_character_id"] for entry in cycle["entries"]}:
                continue
            append_entry(game, cycle, definition)
            cycle["claims"][str(identifier)] = copy.deepcopy(claims[identifier])
        if claims:
            previous = {}
            for entry in game.catalog.rows("m_home_daily_greetings"):
                old = previous.get(entry["m_character_id"])
                if eligible(game, entry) and (old is None or (entry["intimacy_level"], -entry["id"]) > (old["intimacy_level"], -old["id"])):
                    previous[entry["m_character_id"]] = entry
            for entry in sorted(previous.values(), key=lambda row: row["id"])[:limit]:
                used = {game.catalog.get("m_home_daily_greetings", value["id"])["m_character_id"] for value in cycle["entries"]}
                if len(cycle["entries"]) < limit and entry["m_character_id"] not in used:
                    append_entry(game, cycle, entry)
        if data["visitor_day"] in days:
            data["visitor_day"] = today
        for key, reward in list(data["visitor_claims"].items()):
            parts = key.split(":")
            if len(parts) == 2 and parts[0] in days:
                data["visitor_claims"][f"reset:{today}:{parts[1]}"] = copy.deepcopy(reward)
    populate_cycle(game, cycle)
    data["greeting_cycle"] = cycle
    return cycle


def populate_cycle(game, cycle):
    used = {game.catalog.get("m_home_daily_greetings", entry["id"])["m_character_id"] for entry in cycle["entries"]}
    pool = candidates(game, used)
    while pool and len(cycle["entries"]) < game.rules["home_greeting_limit"]:
        stories = [entry for entry in pool if entry["m_novel_home_id"]]
        choices = stories or pool
        entry = choices[secrets.randbelow(len(choices))]
        pool.remove(entry)
        append_entry(game, cycle, entry)


def visitors(game):
    data = home_data(game)
    records = rows(game.state, "t_visitors")
    if not game.rules["home_enable_visitors"]:
        records[:] = [row for row in records if not row["is_temporary"]]
        return []
    today = greeting_cycle(game)["day"]
    choices = [row for row in game.catalog.rows("m_home_visitor_lotteries") if row["weight"] > 0
               and available(row, game.now, "begin_at", "end_at")
               and available(game.catalog.get("m_visitors", row["m_visitor_id"]), game.now)]
    if data["visitor_day"] != today:
        data.update(visitor_day=today, visitor_ids=[])
        if choices:
            data["visitor_ids"].append(weighted_choice(choices)["m_visitor_id"])
    active = {row["m_visitor_id"] for row in choices}
    visible = [identifier for identifier in data["visitor_ids"] if identifier in active]
    records[:] = [row for row in records if not row["is_temporary"] or row["m_visitor_id"] in visible]
    for identifier in visible:
        if find(game.state, "t_visitors", m_visitor_id=identifier) is None:
            put(game.state, "t_visitors", {"m_visitor_id": identifier}, {"is_temporary": 1}, game.user_id)
    return [{"m_visitor_id": identifier, "pos_x": -150 + i * 100, "pos_z": 100} for i, identifier in enumerate(visible)]


def eligible(game, greeting):
    character = find(game.state, "t_characters", m_character_id=greeting["m_character_id"])
    threshold = next((r["total_point"] for r in game.catalog.rows("m_character_intimacy_levels") if r["level"] == greeting["intimacy_level"]), None)
    return character is not None and threshold is not None and character["intimacy_point"] >= threshold


def greetings(game):
    cycle = greeting_cycle(game)
    result = []
    for entry in cycle["entries"][:game.rules["home_greeting_limit"]]:
        definition = game.catalog.get("m_home_daily_greetings", entry["id"])
        if not eligible(game, definition):
            continue
        received = str(entry["id"]) in cycle["claims"] or story_received(game, definition)
        result.append({"m_home_daily_greeting_id": entry["id"], "m_home_visitor_daily_greeting_id": 0, "is_received": int(received),
                       "is_visitor": 0, "pos_x": entry["pos_x"], "pos_z": entry["pos_z"]})
    current = {r["m_visitor_id"] for r in visitors(game)}
    for entry in game.catalog.rows("m_home_visitor_daily_greetings"):
        if entry["m_visitor_id"] not in current:
            continue
        position = game.catalog.get("m_home_greeting_positions", entry["m_home_greeting_position_id"]) if entry["m_home_greeting_position_id"] else {"pos_x": -150, "pos_z": 100}
        key = f"reset:{cycle['day']}:{entry['id']}"
        result.append({"m_home_daily_greeting_id": 0, "m_home_visitor_daily_greeting_id": entry["id"], "is_received": int(key in home_data(game)["visitor_claims"]),
                       "is_visitor": 1, "pos_x": position["pos_x"], "pos_z": position["pos_z"]})
    return result


def receive(game, body):
    identifier = number(body["m_home_daily_greeting_id"], 0)
    visitor_id = number(body["m_home_visitor_daily_greeting_id"], 0)
    if bool(identifier) == bool(visitor_id):
        raise GameplayError("Select exactly one greeting", 400, 4007)
    definition = game.catalog.get("m_home_daily_greetings" if identifier else "m_home_visitor_daily_greetings", identifier or visitor_id)
    if identifier and story_received(game, definition):
        return {"rewards": []}
    options = greetings(game)
    if not any(r["m_home_daily_greeting_id"] == identifier and r["m_home_visitor_daily_greeting_id"] == visitor_id for r in options):
        raise GameplayError("Greeting is not currently available")
    cycle = greeting_cycle(game)
    receipts = cycle["claims"] if identifier else home_data(game)["visitor_claims"]
    key = str(identifier) if identifier else f"reset:{cycle['day']}:{visitor_id}"
    if key in receipts:
        return {"rewards": []}
    rewards = [{k: r[k] for k in ("content_type", "content_id", "amount")} for r in game.catalog.rows("m_home_greeting_rewards")
               if r["group_id"] == definition["m_home_greeting_reward_group_id"]]
    if not rewards:
        raise GameplayError("Home greeting rewards are missing", 503, 5036)
    deliver(game, rewards)
    if identifier and definition["m_novel_home_id"]:
        game.catalog.get("m_novel_homes", definition["m_novel_home_id"])
        put(game.state, "t_novel_homes", {"m_novel_home_id": definition["m_novel_home_id"]}, {}, game.user_id)
    receipts[key] = copy.deepcopy(rewards)
    return {"rewards": rewards}


def prologue(game, body):
    from .gameplay_tutorial import update_prologue

    return update_prologue(game, body)


HOME_ROUTES = {
    "api/home/daily-greeting/receive-reward": route("POST", "m_home_daily_greeting_id m_home_visitor_daily_greeting_id", receive),
    "api/tutorial/prologue-update": route("POST", "status", prologue),
}
