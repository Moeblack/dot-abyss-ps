from __future__ import annotations

from .gameplay_catalog import GameplayError, available, identifiers, number
from .gameplay_receipts import feature_data


def progress(game):
    data = feature_data(game, "side_story")
    if not data:
        data.update(seen_stories=[], stages={})
    if set(data) != {"seen_stories", "stages"} or not isinstance(data["stages"], dict) or len(data["stages"]) > 10000:
        raise GameplayError("Stored side-story progress is invalid")
    identifiers(data["seen_stories"], 10000)
    for key, entry in data["stages"].items():
        if not isinstance(key, str) or not key.isdecimal() or str(int(key)) != key:
            raise GameplayError("Stored side-story stage identifier is invalid")
        number(int(key))
        if not isinstance(entry, dict) or set(entry) != {"seen", "clears", "reward_received", "party_no", "party_characters"}:
            raise GameplayError("Stored side-story stage progress is invalid")
        if type(entry["seen"]) is not bool or type(entry["reward_received"]) is not bool:
            raise GameplayError("Stored side-story stage flags are invalid")
        number(entry["clears"], 0, 2 ** 31 - 1)
        number(entry["party_no"], 1, 10)
        identifiers(entry["party_characters"], 10)
        if bool(entry["clears"]) != entry["reward_received"]:
            raise GameplayError("Stored side-story rewards and progress disagree")
    return data


def prepare_side_story(game):
    if "m_side_stories" in game.catalog.tables:
        progress(game)


def stages(game, story_id):
    return sorted((row for row in game.catalog.rows("m_side_story_stages") if row["m_side_story_id"] == story_id),
                  key=lambda row: (row["no"], row["id"]))


def stage_progress(data, stage_id):
    return data["stages"].get(str(stage_id), {"seen": False, "clears": 0, "reward_received": False,
                                            "party_no": 1, "party_characters": []})


def writable_stage(data, stage_id):
    return data["stages"].setdefault(str(stage_id), stage_progress(data, stage_id))


def story_open(game, story):
    if not available(story, game.now, "open_at", "close_at"):
        return False
    group = story["restrict_group_id"]
    if group:
        raise GameplayError("Side-story release conditions require a verified master binding", 503, 5036)
    return True


def require_story(game, story_id):
    story = game.catalog.get("m_side_stories", number(story_id))
    if not story_open(game, story):
        raise GameplayError("Side story is not open")
    return story


def status(data, stage, previous_complete):
    entry = stage_progress(data, stage["id"])
    if entry["clears"]:
        return 99
    return (2 if entry["seen"] else 1) if previous_complete else 0


def stage_statuses(game, story_id, data):
    previous_complete = True
    result = {}
    for stage in stages(game, story_id):
        if stage["type"] not in (1, 2, 3):
            raise GameplayError("Unsupported side-story stage type", 503, 5036)
        result[stage["id"]] = status(data, stage, previous_complete)
        if stage["type"] != 3:
            previous_complete = previous_complete and bool(stage_progress(data, stage["id"])["clears"])
    return result


def require_stage(game, identifier, kinds):
    stage = game.catalog.get("m_side_story_stages", number(identifier))
    require_story(game, stage["m_side_story_id"])
    data = progress(game)
    if stage["type"] not in kinds:
        raise GameplayError("This endpoint does not support the selected side-story stage")
    if not stage_statuses(game, stage["m_side_story_id"], data)[stage["id"]]:
        raise GameplayError("Complete the preceding side-story stages first")
    return stage, data


def story_completed(game, story_id, data):
    required = [row for row in stages(game, story_id) if row["type"] != 3]
    return bool(required) and all(stage_progress(data, row["id"])["clears"] for row in required)
