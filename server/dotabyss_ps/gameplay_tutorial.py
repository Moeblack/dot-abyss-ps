from __future__ import annotations

from .gameplay_catalog import GameplayError, number
from .gameplay_delivery import deliver
from .gameplay_library import chapter_complete
from .gameplay_presents import enqueue
from .gameplay_receipts import feature_data
from .gameplay_records import find, put, rows
from .newgame import PROLOGUE_COMPLETED, PROLOGUE_STATES
from .gameplay_access import reconcile_feature_rewards, staff_registered

GUIDED_RULES = {
    "unlock_common_features": 0,
    "party_slots": 1,
    "idle_party_slots": 1,
    "idle_auto_start": 0,
    "idle_unlock_all_areas": 0,
}


def tutorial_data(game):
    data = feature_data(game, "tutorial")
    if not data:
        data.update(mode="legacy", reward_claims=[])
    if set(data) != {"mode", "reward_claims"} or data["mode"] not in ("guided", "legacy"):
        raise GameplayError("Stored tutorial policy is invalid")
    claims = data["reward_claims"]
    if not isinstance(claims, list) or len(claims) > 10000:
        raise GameplayError("Stored tutorial reward receipts are invalid")
    for identifier in claims:
        number(identifier)
    if len(claims) != len(set(claims)):
        raise GameplayError("Stored tutorial reward receipts are duplicated")
    return data


def initialize_tutorial_profile(game):
    if "tutorial" in game.progress["features"]:
        raise GameplayError("New tutorial profile already has a policy")
    tutorial_data(game)["mode"] = "guided"


def apply_tutorial_policy(game):
    if "tutorial" in game.progress["features"] and tutorial_data(game)["mode"] == "guided":
        game.rules = {**game.rules, **GUIDED_RULES}


def tutorial_rewards(game, category, tutorial_type, status):
    if "m_tutorial_rewards" not in game.catalog.tables:
        return
    claims = tutorial_data(game)["reward_claims"]
    definitions = sorted((row for row in game.catalog.rows("m_tutorial_rewards")
                          if row["tutorial_category"] == category and row["tutorial_type"] == tutorial_type
                          and row["status"] == status), key=lambda row: row["id"])
    for definition in definitions:
        if definition["id"] in claims:
            continue
        reward = {key: definition[key] for key in ("content_type", "content_id", "amount")}
        if definition["is_direct"] == 1:
            deliver(game, [reward])
        elif definition["is_direct"] == 0:
            enqueue(game, [reward], title="Tutorial rewards")
        else:
            raise GameplayError("Unsupported tutorial reward delivery", 503, 5036)
        claims.append(definition["id"])


def complete_feature_tutorial(game, body):
    kind = number(body["tutorial_type"], 1, 19)
    current = find(game.state, "t_feature_tutorials")
    if current is None:
        current = put(game.state, "t_feature_tutorials", {}, {"completed_flags": 0}, game.user_id)
    mask = 1 << (kind - 1)
    if current["completed_flags"] & mask:
        return {}
    tutorial_rewards(game, 2, kind, 0)
    current["completed_flags"] |= mask
    return {}


def validate_tutorial_battle(game, tutorial_id):
    identifier = number(tutorial_id, 1, 3)
    state = rows(game.state, "t_prologue_tutorials")[0]["status"]
    required = {1: 2, 2: 10, 3: PROLOGUE_COMPLETED}[identifier]
    if state != required:
        raise GameplayError("Tutorial battle is not available at this prologue state")
    return game.catalog.get("m_tutorial_quests", identifier)


def update_prologue(game, body):
    target = number(body["status"], 0, PROLOGUE_COMPLETED)
    if target not in PROLOGUE_STATES:
        raise GameplayError("Unsupported prologue state", 400, 4007)
    current = rows(game.state, "t_prologue_tutorials")[0]
    previous = current["status"]
    if previous == PROLOGUE_COMPLETED or target == previous:
        return {}
    expected = previous + 1 if previous < 13 else PROLOGUE_COMPLETED
    if target != expected:
        raise GameplayError("Prologue state cannot be skipped or rewound")
    if previous in (2, 10):
        from .gameplay_battle_session import finish_tutorial_battle

        finish_tutorial_battle(game, {2: 1, 10: 2}[previous])
    if previous == 7:
        from .gameplay_tutorial_gacha import tutorial

        if not tutorial(game)[0]["confirmed"]:
            raise GameplayError("Confirm the tutorial gacha before continuing")
    current["status"] = target
    return {}


def can_unlock_feature(game, identifier):
    game.catalog.get("m_feature_locks", identifier)
    conditions = [row for row in game.catalog.rows("m_feature_lock_conditions") if row["m_feature_lock_id"] == identifier]
    if not conditions:
        raise GameplayError("Feature unlock condition is missing", 503, 5036)
    for condition in conditions:
        kind, target = condition["condition_type"], condition["condition_param"]
        if kind == 0:
            continue
        if kind == 1:
            game.catalog.get("m_chapter_quests", target)
            if not chapter_complete(game, target):
                return False
        elif kind == 2:
            sequence = game.catalog.get("m_chapter_quest_sequences", target)
            quest = find(game.state, "t_chapter_quests", m_chapter_quest_id=sequence["m_chapter_quest_id"])
            if quest is None or quest["sequence"] <= sequence["sequence"]:
                return False
        elif kind == 3:
            if sum(staff_registered(game, character) for character in rows(game.state, "t_characters")) < target:
                return False
        else:
            raise GameplayError("Unsupported feature unlock condition", 501, 5010)
    return True


def unlock_feature(game, body):
    identifier = number(body["m_feature_lock_id"])
    game.catalog.get("m_feature_locks", identifier)
    current = find(game.state, "t_feature_locks", m_feature_lock_id=identifier)
    if current and current["is_unlocked"]:
        reconcile_feature_rewards(game)
        return {}
    if not can_unlock_feature(game, identifier):
        raise GameplayError("Feature unlock requirements have not been met")
    put(game.state, "t_feature_locks", {"m_feature_lock_id": identifier}, {"is_unlocked": True})
    reconcile_feature_rewards(game, just_unlocked=identifier)
    return {}
