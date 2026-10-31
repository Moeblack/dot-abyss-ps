from __future__ import annotations

import copy
import re

from .gameplay_catalog import GameplayError, available, identifiers, number
from .gameplay_receipts import feature_data
from .gameplay_records import find, grant, put, rows
from .gameplay_routes import route
from .gameplay_story_state import is_quest_complete

DICTIONARIES = {2: ("non_player_characters", "non_player_character"), 3: ("enemies", "enemy"), 4: ("worlds", "world")}
NOVELS = {1: "mains", 2: "events", 3: "characters", 4: "homes", 5: "character_skins", 6: "prologues", 7: "others"}
NOVEL_RECORDS = {
    2: ("t_novel_events", "m_novel_event_id"),
    3: ("t_novel_characters", "m_novel_character_id"),
    5: ("t_novel_character_skins", "m_novel_character_skin_id"),
    7: ("t_novel_others", "m_novel_other_id"),
}


def read_receipts(game):
    reads = feature_data(game, "library").setdefault("read_novels", {})
    if not isinstance(reads, dict) or any(not isinstance(key, str) or not re.fullmatch(r"[1-7]:[1-9][0-9]{0,18}", key) or value is not True for key, value in reads.items()):
        raise GameplayError("Stored story receipts are invalid")
    return reads


def event_rewards(game, definition):
    event = game.catalog.get("m_events", definition["m_event_id"])
    if event["type"] == 3:
        novels = [row for row in game.catalog.rows("m_event_story_novels") if row["m_novel_event_id"] == definition["id"]]
        if len(novels) != 1:
            raise GameplayError("Event story stage binding is invalid", 503, 5036)
        rewards = [row for row in game.catalog.rows("m_event_story_rewards") if row["m_event_story_stage_id"] == novels[0]["m_event_story_stage_id"] and row["is_first_reward"] == 1]
    elif event["type"] in (5, 8):
        rewards = [row for row in game.catalog.rows("m_novel_event_rewards") if row["m_novel_event_id"] == definition["id"]]
    else:
        rewards = []
    return [{key: row[key] for key in ("content_type", "content_id", "amount")} for row in sorted(rewards, key=lambda row: row["id"])]


def event_read(game, definition, reads):
    row = find(game.state, "t_novel_events", m_novel_event_id=definition["id"])
    claims = feature_data(game, "library").setdefault("event_reward_claims", {})
    if not isinstance(claims, dict):
        raise GameplayError("Stored event story reward receipts are invalid")
    for key, claim in claims.items():
        if not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", key) or not isinstance(claim, dict) or set(claim) != {"source", "rewards"} or claim["source"] not in ("local_read", "existing_read") or not isinstance(claim["rewards"], list):
            raise GameplayError("Stored event story reward receipt is invalid")
        for reward in claim["rewards"]:
            if not isinstance(reward, dict) or set(reward) != {"content_type", "content_id", "amount"}:
                raise GameplayError("Stored event story reward is invalid")
            number(reward["content_type"], 1, 2 ** 31 - 1)
            number(reward["content_id"], 0)
            number(reward["amount"], 1, 2 ** 31 - 1)
    key, read_key = str(definition["id"]), f"2:{definition['id']}"
    rewards = []
    if key not in claims:
        source = "existing_read" if read_key in reads or row["is_unread"] == 0 else "local_read"
        if source == "local_read":
            rewards = event_rewards(game, definition)
            grant(game.state, rewards, game.catalog, game.user_id, game.now, game.progress)
        claims[key] = {"source": source, "rewards": copy.deepcopy(rewards)}
    reads[read_key] = True
    return rewards


def intimacy_level(game, character):
    return max((r["level"] for r in game.catalog.rows("m_character_intimacy_levels") if r["total_point"] <= character["intimacy_point"]), default=0)


def complete_event_story(game, novel_id):
    definition = game.catalog.get("m_novel_events", novel_id)
    row = find(game.state, "t_novel_events", m_novel_event_id=novel_id)
    if row is None:
        row = put(game.state, "t_novel_events", {"m_novel_event_id": novel_id}, {"is_unread": 1}, game.user_id)
    rewards = event_read(game, definition, read_receipts(game))
    row["is_unread"] = 0
    return [{**reward, "bonus_type": 0, "is_rare_drop": 0, "additional_info": None} for reward in rewards]


def chapter_complete(game, identifier):
    return is_quest_complete(game.state, identifier)


def eligible_novel(game, kind, definition):
    if kind == 2:
        unlocked = find(game.state, "t_novel_events", m_novel_event_id=definition["id"]) is not None
        configured = game.rules.get("event_recollection_unlock_all", 0) or definition["m_event_id"] in game.rules.get("event_recollection_event_ids", [])
        return bool(unlocked or configured) and available(definition, game.now, "open_at")
    if kind == 3:
        character = find(game.state, "t_characters", m_character_id=definition["m_character_id"])
        return character is not None and definition["condition_type"] == 1 and intimacy_level(game, character) >= definition["condition_value"]
    if kind == 5:
        return find(game.state, "t_character_skins", m_character_skin_id=definition["m_character_skin_id"]) is not None
    if kind == 4:
        return find(game.state, "t_novel_homes", m_novel_home_id=definition["id"]) is not None
    if kind == 1:
        for sequence in game.catalog.rows("m_chapter_quest_sequences"):
            if sequence["m_novel_main_id"] != definition["id"]:
                continue
            quest = find(game.state, "t_chapter_quests", m_chapter_quest_id=sequence["m_chapter_quest_id"])
            if quest and quest["sequence"] >= sequence["sequence"]:
                return True
        return False
    return kind in (6, 7) and rows(game.state, "t_prologue_tutorials")[0]["status"] == 99


def sync_novels(game):
    for kind, (table, field) in NOVEL_RECORDS.items():
        master = "m_novel_" + NOVELS[kind]
        if master not in game.catalog.tables:
            continue
        for definition in game.catalog.rows(master):
            if eligible_novel(game, kind, definition) and find(game.state, table, **{field: definition["id"]}) is None:
                unread = int(f"{kind}:{definition['id']}" not in read_receipts(game))
                values = {"is_unread": unread, **({"status": 1} if kind == 3 else {})}
                put(game.state, table, {field: definition["id"]}, values, None if kind == 7 else game.user_id)


def character_list(game, body):
    sync_novels(game)
    return {"t_novel_characters": copy.deepcopy(game.state["records"]["t_novel_characters"])}


def character_read(game, body):
    sync_novels(game)
    selected = identifiers(body["t_novel_character_ids"], 1000)
    records = {r["id"]: r for r in rows(game.state, "t_novel_characters")}
    if set(selected) - records.keys():
        raise GameplayError("Character story is not unlocked")
    for identifier in selected:
        records[identifier]["is_unread"] = 0
    return {}


def novel_read(game, body):
    kind, identifier = number(body["novel_type"], 1, 7), number(body["m_novel_id"])
    definition = game.catalog.get("m_novel_" + NOVELS[kind], identifier)
    if not eligible_novel(game, kind, definition):
        raise GameplayError("Story requirements are not met")
    sync_novels(game)
    reads = read_receipts(game)
    key = f"{kind}:{identifier}"
    reward = []
    if kind == 2:
        reward = event_read(game, definition, reads)
    elif key not in reads:
        if kind in (3, 5) and game.rules["novel_read_free_currency"]:
            reward = [{"content_type": 10, "content_id": 0, "amount": game.rules["novel_read_free_currency"]}]
            grant(game.state, reward, game.catalog, game.user_id, game.now, game.progress)
        reads[key] = True
    elif reads[key] is not True:
        raise GameplayError("Stored story receipt is invalid")
    if kind in NOVEL_RECORDS:
        table, field = NOVEL_RECORDS[kind]
        find(game.state, table, **{field: identifier})["is_unread"] = 0
    return {"read_rewards": [{**r, "bonus_type": 0, "is_rare_drop": 0, "additional_info": None} for r in reward]}


def dictionary_list(game, body):
    for plural, singular in DICTIONARIES.values():
        for definition in game.catalog.rows("m_dictionary_" + plural):
            if not available(definition, game.now, "release_at", "end_at"):
                continue
            if definition["quest_type"] != 0 and not (definition["quest_type"] == 2 and chapter_complete(game, definition["quest_id"])):
                continue
            table, field = "t_dictionary_" + plural, "m_dictionary_" + singular + "_id"
            if find(game.state, table, **{field: definition["id"]}) is None:
                put(game.state, table, {field: definition["id"]}, {"is_unread": 1}, game.user_id)
    return {}


def dictionary_read(game, body):
    kind = number(body["dictionary_type"], 2, 4)
    values = body["t_dictionary_ids"]
    if not isinstance(values, list) or len(values) > 1000 or any(not isinstance(v, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", v) for v in values):
        raise GameplayError("Invalid dictionary record identifiers", 400, 4007)
    selected = identifiers([int(v) for v in values])
    dictionary_list(game, {})
    plural, _ = DICTIONARIES[kind]
    records = {r["id"]: r for r in rows(game.state, "t_dictionary_" + plural)}
    if set(selected) - records.keys():
        raise GameplayError("Dictionary entry is not unlocked")
    for identifier in selected:
        records[identifier]["is_unread"] = 0
    return {}


LIBRARY_ROUTES = {
    "api/library/novel/character": route("GET", "", character_list),
    "api/library/novel/character/read": route("POST", "t_novel_character_ids", character_read),
    "api/novel/read": route("POST", "novel_type m_novel_id", novel_read),
    "api/library/dictionary": route("GET", "", dictionary_list),
    "api/library/dictionary/read": route("POST", "dictionary_type t_dictionary_ids", dictionary_read),
}
