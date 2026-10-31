from __future__ import annotations

import re

from .gameplay_catalog import GameplayError, number
from .gameplay_receipts import feature_data
from .gameplay_records import find, put, rows
from .gameplay_story_state import COMPLETED_SEQUENCE, is_quest_complete


def sequences(game, quest_id):
    if not hasattr(game, "_story_sequences"):
        grouped = {}
        for row in game.catalog.rows("m_chapter_quest_sequences"):
            grouped.setdefault(row["m_chapter_quest_id"], []).append(row)
        game._story_sequences = {key: sorted(value, key=lambda r: r["sequence"]) for key, value in grouped.items()}
    result = game._story_sequences.get(quest_id, [])
    if not result or [r["sequence"] for r in result] != list(range(1, len(result) + 1)):
        raise GameplayError("Main-story sequence master is incomplete", 503, 5036)
    return result


def completed(game, quest_id):
    return is_quest_complete(game.state, quest_id)


def prepare_story_state(game):
    data = feature_data(game, "main_story")
    if not data:
        complete, battles = [], []
        for row in rows(game.state, "t_chapter_quests"):
            steps = sequences(game, row["m_chapter_quest_id"])
            if row["sequence"] > steps[-1]["sequence"]:
                complete.append(row["m_chapter_quest_id"])
            battles.extend(f"{row['m_chapter_quest_id']}:{step['sequence']}" for step in steps
                           if step["m_chapter_quest_battle_id"] and step["sequence"] < row["sequence"])
        data.update(legacy_completed_quests=complete, legacy_cleared_battles=battles, last_party_no=1, pending_releases={})
    data.setdefault("pending_releases", {})
    fields = {"legacy_completed_quests", "legacy_cleared_battles", "last_party_no", "pending_releases"}
    if set(data) - {"completion_version"} != fields:
        raise GameplayError("Stored main-story progress is invalid")
    number(data.get("completion_version", 0), 0, 1)
    if not isinstance(data["pending_releases"], dict) or len(data["pending_releases"]) > 10000:
        raise GameplayError("Stored pending story releases are invalid")
    for key, values in data["pending_releases"].items():
        if not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", key) or not isinstance(values, list) or len(values) > 10000:
            raise GameplayError("Stored pending story release is invalid")
        for value in values:
            number(value)
    for name in ("legacy_completed_quests", "legacy_cleared_battles"):
        values = data[name]
        if not isinstance(values, list) or len(values) > 10000:
            raise GameplayError("Stored main-story history is invalid")
        for value in values:
            if name == "legacy_completed_quests":
                number(value)
            elif not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{0,18}:[1-9][0-9]{0,3}", value):
                raise GameplayError("Stored main-story battle history is invalid")
        if len(set(values)) != len(values):
            raise GameplayError("Stored main-story history contains duplicates")
    number(data["last_party_no"], 1, 10)
    if not data.get("completion_version"):
        claims = game.progress["features"].get("battle_rewards", {}).get("data", {}).get("quest_claims", {})
        if not isinstance(claims, dict) or any(not re.fullmatch(r"[1-9][0-9]{0,18}", str(key)) for key in claims):
            raise GameplayError("Stored main-story reward receipts are invalid")
        confirmed = set(data["legacy_completed_quests"]) | {int(key) for key in claims}
        for row in rows(game.state, "t_chapter_quests"):
            if row["m_chapter_quest_id"] in confirmed and row["sequence"] > sequences(game, row["m_chapter_quest_id"])[-1]["sequence"]:
                row["sequence"] = COMPLETED_SEQUENCE
        data["completion_version"] = 1
    return data


def prerequisites_met(game, quest):
    area = game.catalog.get("m_chapter_areas", quest["m_chapter_area_id"])
    try:
        required_areas = {int(value) for value in str(area["release_condition_area_ids"]).split(",") if value.strip()}
    except ValueError as error:
        raise GameplayError("Main-story release conditions are invalid", 503, 5036) from error
    previous = [candidate for candidate in game.catalog.rows("m_chapter_quests")
                if candidate["m_chapter_area_id"] in required_areas or
                candidate["m_chapter_area_id"] == area["id"] and candidate["progress_order"] < quest["progress_order"]]
    return all(completed(game, candidate["id"]) for candidate in previous)


def quest_row(game, quest_id):
    quest = game.catalog.get("m_chapter_quests", number(quest_id))
    row = find(game.state, "t_chapter_quests", m_chapter_quest_id=quest_id)
    if row is not None:
        return row
    if not prerequisites_met(game, quest):
        raise GameplayError("Previous story progress is incomplete")
    area = game.catalog.get("m_chapter_areas", quest["m_chapter_area_id"])
    return put(game.state, "t_chapter_quests", {"m_chapter_quest_id": quest_id},
               {"m_chapter_id": area["m_chapter_id"], "m_chapter_area_id": area["id"], "sequence": 0})


def available_quests(game):
    return {quest["id"] for quest in game.catalog.rows("m_chapter_quests") if prerequisites_met(game, quest)}


def release_data(game, before):
    after = available_quests(game)
    newly_available = after - before
    old_areas = {game.catalog.get("m_chapter_quests", value)["m_chapter_area_id"] for value in before}
    new_areas = {game.catalog.get("m_chapter_quests", value)["m_chapter_area_id"] for value in newly_available} - old_areas
    old_chapters = {game.catalog.get("m_chapter_areas", value)["m_chapter_id"] for value in old_areas}
    new_chapters = {game.catalog.get("m_chapter_areas", value)["m_chapter_id"] for value in new_areas} - old_chapters
    for identifier in sorted(newly_available):
        quest_row(game, identifier)
    return {"release_m_chapter_ids": sorted(new_chapters), "release_m_chapter_area_ids": sorted(new_areas),
            "release_m_chapter_quest_ids": sorted(newly_available)}


def update_sequence(game, body):
    from .gameplay_effects import settle_before_effect_change
    from .gameplay_idle import reconcile

    quest_id = number(body["m_chapter_quest_id"])
    target = number(body["sequence"], 1, 1000)
    steps = {step["sequence"]: step for step in sequences(game, quest_id)}
    row = quest_row(game, quest_id)
    if target == row["sequence"]:
        return {"battle_result": None}
    if target != row["sequence"] + 1 or target > max(steps) + 1:
        raise GameplayError("Story sequence cannot be skipped or rewound")
    previous = steps.get(row["sequence"])
    if previous and previous["m_chapter_quest_battle_id"]:
        raise GameplayError("Battle completion is required before advancing this sequence")
    if target == max(steps) + 1:
        settle_before_effect_change(game)
        prepare_story_state(game)["pending_releases"][str(quest_id)] = sorted(available_quests(game))
    row["sequence"] = target
    if target == max(steps) + 1:
        reconcile(game, settle_pending=False)
    return {"battle_result": None}


def clear_quest(game, body, previous_available=None):
    from .gameplay_battle_rewards import complete_quest
    from .gameplay_effects import settle_before_effect_change
    from .gameplay_idle import reconcile

    quest_id = number(body["m_chapter_quest_id"])
    quest = game.catalog.get("m_chapter_quests", quest_id)
    row = quest_row(game, quest_id)
    last = sequences(game, quest_id)[-1]
    if row["sequence"] < last["sequence"] or row["sequence"] == last["sequence"] and last["m_chapter_quest_battle_id"]:
        raise GameplayError("Main-story quest is not complete")
    progress = prepare_story_state(game)
    pending = progress["pending_releases"].get(str(quest_id))
    before = (set(pending) if pending is not None else available_quests(game)) if previous_available is None else previous_available
    settle_before_effect_change(game)
    row["sequence"] = COMPLETED_SEQUENCE
    rewards = complete_quest(game, quest)
    progress["pending_releases"].pop(str(quest_id), None)
    reconcile(game, settle_pending=False)
    return {"last_party_no": progress["last_party_no"], "receive_content_list": rewards,
            "release_data": release_data(game, before)}
