from __future__ import annotations

from .gameplay_catalog import number
from .gameplay_event_effects import boost_event_rewards
from .gameplay_events import event_available
from .gameplay_records import find, rows


def battle_characters(game, party_no):
    party = find(game.state, "t_exploration_parties", party_no=party_no)
    identifiers = {row["t_character_id"] for row in rows(game.state, "t_exploration_party_characters")
                   if party and row["t_exploration_party_id"] == party["id"] and row["t_character_id"]}
    return [row["m_character_id"] for row in rows(game.state, "t_characters") if row["id"] in identifiers]


def battle_event_rewards(game, session):
    if session["kind"] not in ("main", "repeat", "side_story") or not session["stamina_spent"]:
        return []
    if "m_event_stamina_drops" not in game.catalog.tables:
        return []
    from .gameplay_battle_rewards import grant_display_rewards
    from .gameplay_event_bonus import trigger_bonus

    context = session.get("context", {})
    characters = session.get("party_characters", context.get("party_characters", context.get("characters")))
    if characters is None:
        characters = battle_characters(game, session["party_no"])
    rewards = []
    for source in game.catalog.rows("m_event_stamina_drops"):
        event_id = source["m_event_id"]
        event = game.catalog.get("m_events", event_id)
        if find(game.state, "t_events", m_event_id=event_id) is None or not event_available(game, event_id, event):
            continue
        rates = [row for row in game.catalog.rows("m_event_stamina_drop_boost_rates")
                 if row["m_event_id"] == event_id and row["stamina_upper"] <= session["stamina_spent"]]
        rate = number(max(rates, key=lambda row: row["stamina_upper"])["boost_rate"] if rates else 0, 0, 1000000)
        denominator = number(source["stamina_base"]) * 1000
        amount = (session["stamina_spent"] * rate + denominator - 1) // denominator
        if amount:
            reward = {"content_type": 54, "content_id": source["m_event_currency_id"], "amount": amount}
            rewards.extend(grant_display_rewards(game, boost_event_rewards(game, event_id, [reward], characters)))
    if session["kind"] == "side_story":
        trigger_bonus(game, side_story_stage_id=context.get("stage_id", 0), party_characters=characters)
    else:
        area_id = session.get("m_chapter_area_id", 0)
        if area_id:
            area = game.catalog.get("m_chapter_areas", area_id)
            trigger_bonus(game, chapter_id=area["m_chapter_id"], difficulty=area["difficulty"], party_characters=characters)
    return rewards
