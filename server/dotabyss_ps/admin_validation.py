from __future__ import annotations

import copy
import re
from datetime import date

from .gameplay import prepare_progress
from .gameplay_catalog import GameplayError, identifiers, number, parse_timestamp
from .gameplay_records import rows
from .newgame import validate_saved_state
from .protocol import validate_data


def shape(value, required=(), optional=()):
    if not isinstance(value, dict) or set(required) - value.keys() or value.keys() - set(required) - set(optional):
        raise GameplayError("Imported state contains missing or unknown fields")
    return value


def mapping(value):
    if not isinstance(value, dict) or len(value) > 10000:
        raise GameplayError("Imported state contains an invalid mapping")
    return value


def sequence(value, maximum=10000):
    if not isinstance(value, list) or len(value) > maximum:
        raise GameplayError("Imported state contains an invalid list")
    return value


def key_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", value):
        raise GameplayError("Imported state contains an invalid identifier")
    return number(int(value))


def flag(value):
    if type(value) is not bool:
        raise GameplayError("Imported state contains an invalid flag")


def day(value):
    if not isinstance(value, str):
        raise GameplayError("Imported day must be a string")
    date.fromisoformat(value.removeprefix("reset05:"))


def dates(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key.endswith("_at") and child not in (None, ""):
                if not isinstance(child, str):
                    raise GameplayError("Imported timestamp must be a string")
                parse_timestamp(child)
            dates(child)
    elif isinstance(value, list):
        for child in value:
            dates(child)


def counts(value):
    for key, amount in mapping(value).items():
        key_id(key)
        number(amount, 0, 2 ** 31 - 1)


def rewards(value):
    for row in sequence(value):
        if not isinstance(row, dict) or not {"content_type", "content_id", "amount"} <= row.keys():
            raise GameplayError("Imported reward is invalid")
        number(row["content_type"], 1, 2 ** 31 - 1)
        number(row["content_id"], 0)
        number(row["amount"], 0, 2 ** 31 - 1)


def record_references(game):
    from .admin_master import reference_table

    schema = {"tables": game.catalog.tables}
    for collection in game.state["records"].values():
        for row in collection["up"]:
            for field, value in row.items():
                target = reference_table(field, schema)
                if target and value:
                    game.catalog.get(target, number(value))


def simple_states(game, features):
    from .gameplay_battle_rewards import reward_state
    from .gameplay_battle_session import battle_state
    from .gameplay_characters import ability_release_policy
    from .gameplay_disaster_state import disaster_data
    from .gameplay_encounter_campaign import campaign_data
    from .gameplay_home import home_data
    from .gameplay_library import read_receipts
    from .gameplay_side_story_state import progress as side_story
    from .gameplay_statistics import bucket
    from .gameplay_story_progress import prepare_story_state
    from .gameplay_training import progress as training
    from .gameplay_tutorial import tutorial_data
    from .gameplay_union import progress as union

    validators = {"battle": battle_state, "battle_rewards": reward_state, "character": ability_release_policy,
                  "disaster": disaster_data, "encounter": campaign_data, "home": home_data,
                  "library": read_receipts, "main_story": prepare_story_state, "side_story": side_story,
                  "training": training, "tutorial": tutorial_data, "union": union}
    for name, validator in validators.items():
        if name in features:
            validator(game)
    if "statistics" in features:
        bucket(game.progress)
    if "battle" in features and features["battle"]["data"]["active"] is not None:
        raise GameplayError("An active battle cannot be imported")
    for name in ("friends", "missions"):
        if name in features:
            shape(features[name]["data"])
    if "character" in features:
        data = features["character"]["data"]
        shape(data, optional=("ability_releases", "ability_response_version", "skin_bond_rewards"))
        from .gameplay_skin_bonds import validate_skin_bond_rewards
        validate_skin_bond_rewards(game)
        if "ability_response_version" in data:
            number(data["ability_response_version"], 1, 1)
    if "union" in features:
        claims = features["union"]["data"]["first_claims"]
        if len(claims) != len(set(claims)):
            raise GameplayError("Duplicate imported union rewards")
        for value in claims:
            game.catalog.get("m_union_request_stages", number(value))
    if "training" in features:
        for key, value in features["training"]["data"]["reward_claims"].items():
            game.catalog.get("m_coaching_quests", key_id(key))
            if not isinstance(value, dict):
                raise GameplayError("Invalid imported training reward")


def auxiliary_states(game, features):
    from .gameplay_pass_state import pass_state
    from .gameplay_login_state import bonus_entry, login_data
    from .gameplay_research import ensure_tutorial_supplies
    from .gameplay_access import reward_claims

    if "feature_unlock" in features:
        reward_claims(game)

    for name in ("equipment", "party"):
        if name not in features:
            continue
        data = features[name]["data"]
        allowed = ("next_ids", "appraisal_state_version") if name == "equipment" else ("next_ids", "next_party_id", "drop_snapshots")
        shape(data, optional=allowed)
        for table, value in mapping(data.get("next_ids", {})).items():
            if table not in game.state["records"]:
                raise GameplayError("Imported record cursor has an unknown table")
            number(value)
        if "next_party_id" in data:
            number(data["next_party_id"], 1, 2 ** 31 - 2)
        if "drop_snapshots" in data:
            from .gameplay_drop_bonuses import snapshots
            snapshots(game)
        if "appraisal_state_version" in data:
            number(data["appraisal_state_version"], 1, 1)
    if "research" in features:
        shape(features["research"]["data"], optional=("tutorial_supplies",))
        if "tutorial_supplies" in features["research"]["data"]:
            ensure_tutorial_supplies(game)
    if "mission_pass" in features:
        for key in list(mapping(features["mission_pass"]["data"])):
            identifier = key_id(key)
            game.catalog.get("m_mission_passes", identifier)
            pass_state(game.progress, identifier)
    if "login" in features:
        data = login_data(game.progress)
        for key in list(data["bonuses"]):
            identifier = key_id(key)
            game.catalog.get("m_login_bonuses", identifier)
            entry = bonus_entry(game.progress, identifier)
            if entry["last_day"]:
                day(entry["last_day"])
        for value in data["subscription_days"].values():
            values = sequence(value)
            if len(values) != len(set(values)):
                raise GameplayError("Duplicate imported subscription reward days")
            for item in values:
                day(item)
        if data["friend_day"]:
            day(data["friend_day"])
        for entry in sequence(data["history"]):
            if not isinstance(entry, dict):
                raise GameplayError("Invalid imported login history")
    if "home" in features:
        data = features["home"]["data"]
        if data["visitor_day"]:
            date.fromisoformat(data["visitor_day"])
        for value in data["visitor_claims"].values():
            rewards(value)
    if "library" in features:
        data = features["library"]["data"]
        shape(data, optional=("read_novels", "event_reward_claims"))
        for key, claim in mapping(data.get("event_reward_claims", {})).items():
            game.catalog.get("m_novel_events", key_id(key))
            shape(claim, ("source", "rewards"))
            if claim["source"] not in ("local_read", "existing_read"):
                raise GameplayError("Invalid imported story reward origin")
            rewards(claim["rewards"])


def economy_states(game, features):
    from .gameplay_gacha import gacha_state
    from .gameplay_idle import progress as idle
    from .gameplay_presents import presents
    from .gameplay_shop import shop_data

    if "presents" in features:
        presents(game)
    if "exchange" in features:
        data = features["exchange"]["data"]
        if data:
            shape(data, ("counts",))
            for key, counter in mapping(data["counts"]).items():
                game.catalog.get("m_exchange_lineups", key_id(key))
                shape(counter, ("period", "count"))
                if not isinstance(counter["period"], str):
                    raise GameplayError("Invalid imported exchange period")
                number(counter["count"], 0, 2 ** 31 - 1)
    if "gacha" in features:
        data = gacha_state(game)
        counts(data["pity"])
        for key, value in data["periods"].items():
            game.catalog.get("m_gachas", key_id(key))
            date.fromisoformat(value)
        for key, values in data["wishes"].items():
            game.catalog.get("m_gacha_groups", key_id(key))
            for identifier in identifiers(values, 1000):
                game.catalog.get("m_characters", identifier)
        if "tutorial" in data:
            entry = shape(data["tutorial"], ("confirmed", "pending", "result"))
            flag(entry["confirmed"])
            rewards(entry["pending"])
            if entry["result"] is not None:
                mapping(entry["result"])
        for key, entry in mapping(data.get("expiry_receipts", {})).items():
            if not isinstance(key, str) or ":" not in key:
                raise GameplayError("Invalid imported token expiry key")
            shape(entry, ("token_num", "gold", "converted_at"))
            number(entry["token_num"], 0)
            number(entry["gold"], 0)
    if "shop" in features:
        data = shop_data(game)
        counts(data["steps"])
        for value in (*data["tokens"].values(), *data["released"].values()):
            parse_timestamp(value)
        for entry in data["receipts"].values():
            shape(entry, ("payment_id", "result"), ("response",))
            game.catalog.get("m_payments", number(entry["payment_id"]))
            mapping(entry["result"])
            if "response" in entry:
                mapping(entry["response"])
        for entry in data["counts"].values():
            shape(entry, ("period", "count"))
            number(entry["count"], 0, 2 ** 31 - 1)
            if not isinstance(entry["period"], str):
                raise GameplayError("Invalid imported purchase period")
    if "idle" in features:
        data = idle(game)
        rewards(data["pending"])
        for entry in sequence(data["logs"], 100):
            shape(entry, ("sequence", "type", "bind"))
            key_id(entry["sequence"])
            number(entry["type"], 0)
            mapping(entry["bind"])
        for key, entry in data["carry"].items():
            number(key_id(key), 1, 10)
            shape(entry, ("seconds", "interval", "area", "guaranty"))
            for field in ("seconds", "guaranty"):
                number(entry[field], 0)
            number(entry["interval"])
            game.catalog.get("m_idle_exploration_map_areas", number(entry["area"]))


def tavern_state(game, features):
    if "tavern" not in features:
        return
    from .gameplay_tavern import state

    data = state(game)
    owned = {row["m_character_id"] for row in rows(game.state, "t_characters")}
    seen = set()
    for key, members in data["assignments"].items():
        game.catalog.get("m_tavern_works", key_id(key))
        for identifier in members:
            if identifier and (identifier not in owned or identifier in seen):
                raise GameplayError("Imported tavern staff are missing or duplicated")
            if identifier:
                seen.add(identifier)
    active = data["active"]
    if active is None:
        return
    shape(active, ("seed", "turn", "max_turn", "card_level", "work_level", "reward_level", "score_rules", "workers", "hand", "rerolls", "character_used"), ("reload",))
    for field in ("seed", "max_turn", "card_level", "work_level", "reward_level"):
        number(active[field], 1, 2 ** 31 - 1)
    number(active["turn"], 1, active["max_turn"])
    number(active["rerolls"], 0, 2 ** 31 - 1)
    flag(active["character_used"])
    shape(active["score_rules"], ("tavern_base_score", "tavern_combo_permille"), ("tavern_use_client_score_parameters",))
    for value in active["score_rules"].values():
        number(value, 0, 2 ** 31 - 1)
    workers = sequence(active["workers"], 30)
    if not workers:
        raise GameplayError("Imported active tavern has no staff")
    for worker in workers:
        shape(worker, ("character", "work", "job_level", "intimacy_level", "rate", "flat", "categories", "score"))
        if worker["character"] not in owned:
            raise GameplayError("Imported tavern worker is not owned")
        game.catalog.get("m_tavern_works", number(worker["work"]))
        for field in ("job_level", "intimacy_level", "score"):
            number(worker[field], 0, 2 ** 31 - 1)
        for field in ("rate", "flat"):
            number(worker[field], -(2 ** 31), 2 ** 31 - 1)
        identifiers(worker["categories"], 1000)
    for identifier in sequence(active["hand"], 100):
        game.catalog.get("m_tavern_cards", number(identifier))
    if "reload" in active:
        shape(active["reload"], ("kind", "param"))
        number(active["reload"]["kind"], 100, 103)
        number(active["reload"]["param"], 0)


def nether_state(game, features):
    if "nether" not in features:
        return
    from .gameplay_nether_state import state
    from .gameplay_nether_configuration import frozen_game, validate_configuration

    data = state(game)
    for day, amount in data["free_entries"].items():
        date.fromisoformat(day)
        number(amount, 0)
    for sphere in data["spheres"]:
        if not isinstance(sphere, dict) or not {"m_nether_sphere_board_id", "type", "level"} <= sphere.keys():
            raise GameplayError("Invalid imported Nether sphere")
        for key in ("m_nether_sphere_board_id", "type", "level"):
            number(sphere[key])
    run = data["run"]
    if run is None:
        return
    shape(run, ("id", "map_id", "party_no", "party", "hp", "floors", "visited", "resolved", "rewards", "code_draws", "code_roll", "shop_purchases", "cleared_count", "event_battle", "last_battle", "last_continue", "settled"), ("base_score", "bonus_score", "score_multiplier", "configuration"))
    if "configuration" in run:
        validate_configuration(run["configuration"], game.catalog)
        game = frozen_game(game, include_settled=True)
    number(run["id"])
    game.catalog.get("m_nether_maps", number(run["map_id"]))
    number(run["party_no"], 1, 10)
    flag(run["settled"])
    for key in ("code_draws", "code_roll", "cleared_count", "event_battle", "base_score", "bonus_score"):
        if key in run:
            number(run[key], 0)
    if "score_multiplier" in run:
        number(run["score_multiplier"], 1)
    for value in mapping(run["shop_purchases"]).values():
        number(value, 0)
    party = shape(run["party"], ("mana_gem_rank", "mana_gems", "characters", "built_in_effects"))
    characters = sequence(party["characters"], 10)
    if not characters:
        raise GameplayError("Imported Nether party is empty")
    expected = set()
    for character in characters:
        if not isinstance(character, dict) or not {"m_character_id", "level", "hp", "attack", "defence", "party_index"} <= character.keys():
            raise GameplayError("Imported Nether character is incomplete")
        identifier = number(character["m_character_id"])
        game.catalog.get("m_characters", identifier)
        if str(identifier) in expected:
            raise GameplayError("Duplicate imported Nether character")
        expected.add(str(identifier))
        for key in ("level", "hp", "attack", "defence"):
            number(character[key], 0, 2 ** 31 - 1)
        number(character["party_index"], 0, 9)
        for key in ("character_abilities", "equipment_abilities", "general_abilities", "t_character_bond_parameters"):
            sequence(character.get(key), 1000)
    if set(mapping(run["hp"])) != expected:
        raise GameplayError("Imported Nether HP does not match its party")
    for value in run["hp"].values():
        number(value, 0, 1000)
    coordinates = set()
    for floor in sequence(run["floors"], 3000):
        shape(floor, ("m_nether_id", "m_nether_map_id", "m_nether_map_floor_id", "floor_level", "floor_index", "m_nether_map_floor_prev_ids", "is_hidden", "extend_id"))
        level, index = number(floor["floor_level"], 1, 1000), number(floor["floor_index"], 1, 3)
        key = f"{level}:{index}"
        if key in coordinates or floor["m_nether_map_id"] != run["map_id"]:
            raise GameplayError("Imported Nether floor coordinates are inconsistent")
        coordinates.add(key)
        game.catalog.get("m_nether_map_floors", number(floor["m_nether_map_floor_id"]))
        identifiers(floor["m_nether_map_floor_prev_ids"], 100)
        number(floor["is_hidden"], 0, 1)
        number(floor["extend_id"])
    for field in ("visited", "resolved"):
        values = sequence(run[field], 3000)
        if len(values) != len(set(values)) or set(values) - coordinates:
            raise GameplayError("Imported Nether route history is inconsistent")
    rewards(run["rewards"])


def event_states(game, features):
    if "events" not in features:
        return
    from .gameplay_event_board import current_sheet
    from .gameplay_event_box import box
    from .gameplay_event_missions import statistics
    from .gameplay_event_quest_state import QUEST_KINDS, saved_node

    data = features["events"]["data"]
    if not data:
        return
    shape(data, ("instances",))
    game.rules = {**game.rules, "event_open_all": 1}
    joined = {row["m_event_id"] for row in rows(game.state, "t_events")}
    for key, entry in mapping(data["instances"]).items():
        identifier = key_id(key)
        event = game.catalog.get("m_events", identifier)
        shape(entry, ("epoch",), ("quests", "board", "box", "miner", "mutant", "bonus", "battle_recovery", "mission_stats"))
        number(entry["epoch"])
        if identifier not in joined:
            raise GameplayError("Imported event progress has no participation record")
        if "quests" in entry:
            for node_key in mapping(entry["quests"]):
                parts = node_key.split(":")
                if len(parts) != 2 or parts[0] not in QUEST_KINDS:
                    raise GameplayError("Invalid imported event quest identifier")
                kind = QUEST_KINDS[parts[0]]
                node = game.catalog.get(kind.nodes, key_id(parts[1]))
                if node["m_event_id"] != identifier or kind.event_type != event["type"]:
                    raise GameplayError("Imported quest belongs to another event")
                saved_node(game, kind, node)
        if "board" in entry:
            if event["type"] != 4:
                raise GameplayError("Imported board belongs to a different event type")
            board = shape(entry["board"], ("current_sheet", "sheets"))
            number(board["current_sheet"])
            for sheet_key in list(mapping(board["sheets"])):
                current_sheet(game, event, {"current_sheet": key_id(sheet_key), "sheets": board["sheets"]})
        if "box" in entry:
            box(game, identifier)
        if "miner" in entry:
            if event["type"] != 1:
                raise GameplayError("Imported mining state belongs to a different event type")
            miner = shape(entry["miner"], ("clears", "last_party_no"))
            counts(miner["clears"])
            number(miner["last_party_no"], 1, 10)
        if "mutant" in entry:
            if event["type"] != 2:
                raise GameplayError("Imported mutant state belongs to a different event type")
            mutant = shape(entry["mutant"], ("quests", "best_score", "best_detail", "ranking_claimed"))
            number(mutant["best_score"], 0)
            sequence(mutant["best_detail"])
            flag(mutant["ranking_claimed"])
            for quest_key, quest in mapping(mutant["quests"]).items():
                definition = game.catalog.get("m_event_disaster_quests", key_id(quest_key))
                if definition["m_event_id"] != identifier:
                    raise GameplayError("Imported mutant quest belongs to another event")
                shape(quest, ("version", "damage", "details", "clears", "last_party_no"))
                number(quest["version"])
                number(quest["damage"], 0)
                number(quest["clears"], 0)
                number(quest["last_party_no"], 1, 10)
                for attempt in sequence(quest["details"], 3):
                    sequence(attempt, 10)
        if "bonus" in entry:
            number(shape(entry["bonus"], ("rolls",))["rolls"], 0)
        if "battle_recovery" in entry:
            recovery = shape(entry["battle_recovery"], ("day", "used"))
            date.fromisoformat(recovery["day"])
            number(recovery["used"], 0)
        if "mission_stats" in entry:
            stats = statistics(game, identifier)
            for bucket in [stats["totals"], *stats["days"].values()]:
                for stat_key, value in bucket.items():
                    if not isinstance(stat_key, str) or not re.fullmatch(r"[1-9][0-9]{0,8}:[0-9]{1,19}:[0-9]{1,19}", stat_key):
                        raise GameplayError("Invalid imported event statistic key")
                    number(value, 0, 2 ** 31 - 1)


def validate_import(game):
    candidate = copy.copy(game)
    candidate.state = copy.deepcopy(game.state)
    candidate.progress = copy.deepcopy(game.progress)
    candidate.rules = dict(game.rules)
    candidate.community = None
    try:
        validate_data(candidate.state)
        validate_data(candidate.progress)
        validate_saved_state(candidate.state)
        owners = {row["user_id"] for collection in candidate.state["records"].values() for row in collection["up"] if "user_id" in row}
        if owners != {candidate.user_id}:
            raise GameplayError("Imported save belongs to another profile")
        record_references(candidate)
        dates(candidate.state)
        dates(candidate.progress)
        prepare_progress(candidate.progress, candidate.now)
        features = candidate.progress["features"]
        simple_states(candidate, features)
        auxiliary_states(candidate, features)
        economy_states(candidate, features)
        tavern_state(candidate, features)
        nether_state(candidate, features)
        event_states(candidate, features)
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
        if isinstance(error, GameplayError):
            raise
        raise GameplayError("Imported save has inconsistent business state: " + type(error).__name__) from error
