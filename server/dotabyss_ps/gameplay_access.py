from __future__ import annotations

import re

from .gameplay_catalog import GameplayError, number, parse_timestamp
from .gameplay_delivery import deliver
from .gameplay_library import intimacy_level
from .gameplay_receipts import feature_data
from .gameplay_records import find, rows


STAFF_REGISTRATION_LEVEL = 1


def staff_registered(game, character):
    return intimacy_level(game, character) >= STAFF_REGISTRATION_LEVEL


def feature_available(game, identifier):
    from .gameplay_tutorial import can_unlock_feature

    current = find(game.state, "t_feature_locks", m_feature_lock_id=identifier)
    return bool(current and current["is_unlocked"]) or can_unlock_feature(game, identifier)


def require_feature(game, identifier):
    if not feature_available(game, identifier):
        raise GameplayError("Feature unlock requirements have not been met")


def reward_claims(game):
    data = feature_data(game, "feature_unlock")
    data.setdefault("reward_claims", {})
    if set(data) != {"reward_claims"} or not isinstance(data["reward_claims"], dict) or len(data["reward_claims"]) > 10000:
        raise GameplayError("Stored feature unlock rewards are invalid")
    fields = {"m_feature_lock_id", "content_type", "content_id", "amount", "covered_amount", "granted_amount", "at", "source"}
    for key, claim in data["reward_claims"].items():
        if not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]{0,18}", key) or not isinstance(claim, dict) or set(claim) != fields:
            raise GameplayError("Stored feature unlock reward receipt is invalid")
        for field in ("m_feature_lock_id", "content_type", "amount"):
            number(claim[field])
        for field in ("content_id", "covered_amount", "granted_amount"):
            number(claim[field], 0)
        if claim["covered_amount"] + claim["granted_amount"] != claim["amount"] or claim["source"] not in ("unlock", "legacy-backfill"):
            raise GameplayError("Stored feature unlock reward accounting is invalid")
        if not isinstance(claim["at"], str):
            raise GameplayError("Stored feature unlock reward date is invalid")
        try:
            parse_timestamp(claim["at"])
        except (ValueError, TypeError) as error:
            raise GameplayError("Stored feature unlock reward date is invalid") from error
    return data["reward_claims"]


def reconcile_feature_rewards(game, *, just_unlocked=None):
    from .gameplay_research import tutorial_supply_receipt

    if "m_feature_lock_rewards" not in game.catalog.tables:
        return
    unlocked = {row["m_feature_lock_id"] for row in rows(game.state, "t_feature_locks") if row["is_unlocked"]}
    definitions = [row for row in game.catalog.rows("m_feature_lock_rewards") if row["m_feature_lock_id"] in unlocked]
    if not definitions and "feature_unlock" not in game.progress["features"]:
        return
    claims = reward_claims(game)
    supplied = tutorial_supply_receipt(game)
    covered_bones = supplied["amount"] - supplied.get("feature_reward_bone_credit", 0) if supplied else 0
    covered_bones -= sum(claim["covered_amount"] for claim in claims.values()
                         if claim["m_feature_lock_id"] == 1 and claim["content_type"] == 30 and claim["content_id"] == 50004)
    for definition in sorted(definitions, key=lambda row: row["id"]):
        key = str(number(definition["id"]))
        if key in claims:
            continue
        content = {field: number(definition[field], 0 if field == "content_id" else 1)
                   for field in ("content_type", "content_id", "amount")}
        covered = 0
        if definition["m_feature_lock_id"] == 1 and content["content_type"] == 30 and content["content_id"] == 50004:
            covered = min(content["amount"], max(0, covered_bones))
            covered_bones -= covered
        granted = content["amount"] - covered
        if granted:
            deliver(game, [{**content, "amount": granted}])
        claims[key] = {"m_feature_lock_id": definition["m_feature_lock_id"], **content,
                       "covered_amount": covered, "granted_amount": granted, "at": game.now.isoformat(),
                       "source": "unlock" if definition["m_feature_lock_id"] == just_unlocked else "legacy-backfill"}
