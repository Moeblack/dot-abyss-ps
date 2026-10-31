from __future__ import annotations

from datetime import date, timedelta
import re

from .gameplay_catalog import GameplayError, identifiers, number
from .gameplay_missions import Missions
from .gameplay_records import find, rows
from .gameplay_routes import route
from .gameplay_statistics import key as statistic_key

COUNT_SITUATIONS = {1: 110000, 3: 110001, 5: 110002, 7: 110003, 8: 110004}
EVENT_SITUATIONS = {*COUNT_SITUATIONS.values(), 110005, 110200, 110201, 110202, 110300, 110400, 110500, 110600, 110700}
TIME_SITUATIONS = {110200, 110201, 110500, 110600, 110700}


def statistics(game, event_id):
    from .gameplay_events import event_data

    data = event_data(game, event_id).setdefault("mission_stats", {"totals": {}, "days": {}})
    return validate_statistics(data)


def validate_statistics(data):
    if not isinstance(data, dict) or set(data) != {"totals", "days"} or not all(isinstance(v, dict) for v in data.values()):
        raise GameplayError("Stored event mission statistics are invalid")
    if len(data["totals"]) > 10000 or len(data["days"]) > 400:
        raise GameplayError("Stored event mission statistics exceed their limit")
    for day, values in data["days"].items():
        try:
            valid = isinstance(day, str) and date.fromisoformat(day).isoformat() == day
        except ValueError:
            valid = False
        if not valid or not isinstance(values, dict) or len(values) > 10000:
            raise GameplayError("Stored event mission date bucket is invalid")
    for values in (data["totals"], *data["days"].values()):
        for key, value in values.items():
            if not isinstance(key, str) or not re.fullmatch(r"[1-9][0-9]{0,8}:[0-9]{1,19}:[0-9]{1,19}", key):
                raise GameplayError("Stored event mission statistic key is invalid")
            number(value, 0, 2 ** 63 - 1)
    return data


def add_statistic(game, data, situation, value, target=0, sub=0, *, mode="add"):
    value = number(value, 0, 2 ** 63 - 1)
    key = statistic_key(situation, target, sub)
    today = game.now.date().isoformat()
    cutoff = (game.now.date() - timedelta(days=399)).isoformat()
    data["days"] = {day: values for day, values in data["days"].items() if day >= cutoff}
    for values in (data["totals"], data["days"].setdefault(today, {})):
        previous = values.get(key)
        if previous is not None:
            number(previous, 0, 2 ** 63 - 1)
        current = (previous or 0) + value if mode == "add" else max(previous or 0, value) if mode == "max" else min(previous, value) if previous is not None else value
        values[key] = number(current, 0, 2 ** 63 - 1)


def definitions(game, event):
    group = event["m_mission_term_category_group"]
    if not group:
        return []
    terms = {row["id"] for row in game.catalog.rows("m_mission_terms") if row["category_group"] == group}
    return [row for row in game.catalog.rows("m_missions")
            if row["m_mission_term_id"] in terms and row["mission_type"] in (4, 8) and not row["m_mission_pass_id"]]


def record_clear(game, event, quest_id, stage_results, context, count=1):
    if context.get("is_story"):
        return
    count = number(count, 1, 100000)
    quest_id = number(quest_id)
    data = statistics(game, event["id"])
    situation = 110005 if context.get("kind") in ("event_bonus", "bonus") else COUNT_SITUATIONS.get(event["type"])
    if situation is not None:
        add_statistic(game, data, situation, count)
        add_statistic(game, data, situation, count, quest_id)
    if context.get("is_skip"):
        return
    elapsed = number(stage_results.get("play_time", 0), 0, 86400000)
    if event["type"] == 7:
        add_statistic(game, data, 110300, number(stage_results.get("top_fc_damage", 0), 0, 2 ** 63 - 1), quest_id, mode="max")
    if event["type"] == 5 and context.get("effects_enabled") == 0:
        add_statistic(game, data, 110400, count, quest_id)
    if elapsed <= 0:
        return
    if event["type"] in (1, 9):
        add_statistic(game, data, 110200 if event["type"] == 1 else 110201, elapsed, quest_id, mode="min")
    if event["type"] != 9:
        return
    members = identifiers(context.get("party_characters", []), 10)
    if not members:
        return
    characters = [game.catalog.get("m_characters", identifier) for identifier in members]
    conditions = {(mission["situation_type"], mission["sub_target_id"]) for mission in definitions(game, event)
                  if mission["target_id"] == quest_id and mission["situation_type"] in (110500, 110600, 110700)}
    for situation, sub in conditions:
        matches = all(character["rarity"] <= sub for character in characters) if situation == 110500 else all(
            character["mana_type" if situation == 110600 else "union_type"] == sub for character in characters)
        if matches:
            add_statistic(game, data, situation, elapsed, quest_id, sub, mode="min")


class EventMissions(Missions):
    def __init__(self, game, event):
        super().__init__(game.state, game.progress, game.catalog, game.now, game.user_id, game.rules)
        self.game, self.event = game, event
        self.definitions = definitions(game, event)
        self.allowed_ids = {mission["id"] for mission in self.definitions}

    def active(self, mission):
        from .gameplay_events import event_available

        if mission["id"] not in self.allowed_ids:
            return False
        term = self.catalog.get("m_mission_terms", mission["m_mission_term_id"])
        if not event_available(self.game, self.event["id"], term, start="start_at"):
            return False
        for entry in self.catalog.rows("m_event_commission_missions"):
            if entry["m_event_id"] == self.event["id"] and entry["m_mission_id"] == mission["id"]:
                if not event_available(self.game, self.event["id"], entry, start="receivable_at"):
                    return False
        return True

    def value(self, mission):
        situation = mission["situation_type"]
        if situation not in EVENT_SITUATIONS:
            return super().value(mission)
        data = statistics(self.game, self.event["id"])
        values = data["days"].get(self.now.date().isoformat(), {}) if mission["mission_type"] == 8 else data["totals"]
        target, sub = mission["target_id"], mission["sub_target_id"]
        if situation == 110202:
            quests = [row for row in self.catalog.rows("m_event_speedrun_quests")
                      if row["m_event_id"] == self.event["id"] and row["division"] == target]
            times = [values.get(statistic_key(110201, quest["id"])) for quest in quests]
            if not times or any(value is None or number(value, 0, 86400000) == 0 for value in times):
                return None
            return sum(times)
        value = values.get(statistic_key(situation, target, sub))
        if value is not None:
            return number(value, 0, 2 ** 63 - 1)
        return None if situation in TIME_SITUATIONS else 0

    def listing(self):
        result = {"limited": [], "limited_daily": []}
        for mission in sorted(self.definitions, key=lambda row: (row["display_order"], row["id"])):
            if self.active(mission) and self.prerequisites(mission):
                result["limited_daily" if mission["mission_type"] == 8 else "limited"].append(self.view(mission))
        return result


def evaluator_for_category(game, category_group):
    from .gameplay_campaign_missions import CampaignMissions
    from .gameplay_encounter_campaign import available as campaign_available, campaign_data
    from .gameplay_events import require_event

    group = number(category_group)
    events = [event for event in game.catalog.rows("m_events") if event["m_mission_term_category_group"] == group]
    terms = {term["id"] for term in game.catalog.rows("m_mission_terms") if term["category_group"] == group}
    campaigns = [campaign for campaign in game.catalog.tables.get("m_encounter_campaigns", {}).values()
                 if campaign["m_mission_term_id"] in terms]
    if events and campaigns or len(events) > 1:
        raise GameplayError("Mission category has conflicting event definitions", 503, 5036)
    if events:
        return EventMissions(game, require_event(game, events[0]["id"], allow_closed=True))
    if not campaigns:
        raise GameplayError("Unknown event mission category", 400, 4007)
    data = campaign_data(game)
    joined = [campaign for campaign in campaigns if str(campaign["id"]) in data["entries"]]
    selected = next((campaign for campaign in joined if campaign["id"] == data["current_id"]), None)
    if selected is None:
        if not joined:
            raise GameplayError("Join this encounter campaign before using its missions")
        if len(joined) != 1:
            raise GameplayError("Select the encounter campaign for this mission category")
        selected = joined[0]
    if not campaign_available(game, selected, allow_closed=True):
        raise GameplayError("Encounter campaign reward period has ended")
    return CampaignMissions(game, selected)


def evaluator_for_mission(game, mission_id):
    from .gameplay_campaign_missions import CampaignMissions
    from .gameplay_encounter_campaign import campaign_data

    mission = game.catalog.get("m_missions", number(mission_id))
    if not mission["m_mission_term_id"]:
        return None
    term = game.catalog.get("m_mission_terms", mission["m_mission_term_id"])
    events = [event for event in game.catalog.rows("m_events") if event["m_mission_term_category_group"] == term["category_group"]]
    campaigns = [campaign for campaign in game.catalog.tables.get("m_encounter_campaigns", {}).values()
                 if campaign["m_mission_term_id"] == term["id"]]
    if len(events) + len(campaigns) != 1:
        return None
    if events:
        if find(game.state, "t_events", m_event_id=events[0]["id"]) is None:
            return None
        evaluator = EventMissions(game, events[0])
    else:
        if str(campaigns[0]["id"]) not in campaign_data(game)["entries"]:
            return None
        evaluator = CampaignMissions(game, campaigns[0])
    return evaluator if mission_id in evaluator.allowed_ids else None


def mission_list(game, body):
    return evaluator_for_category(game, body["category_group"]).listing()


def mission_receive(game, body):
    evaluator = evaluator_for_category(game, body["category_group"])
    return evaluator.receive(body["m_mission_ids"], allowed_ids=evaluator.allowed_ids)


def receivable_events(game):
    from .gameplay_campaign_missions import CampaignMissions
    from .gameplay_encounter_campaign import available as campaign_available, campaign_data
    from .gameplay_events import event_available

    joined = {row["m_event_id"] for row in rows(game.state, "t_events")}
    result = []
    for event in game.catalog.rows("m_events"):
        if event["id"] not in joined or not event_available(game, event["id"], event, end="close_at"):
            continue
        evaluator = EventMissions(game, event)
        if any(evaluator.receivable(mission) for mission in evaluator.definitions):
            result.append(event["id"])
    if "m_encounter_campaigns" not in game.catalog.tables:
        return result
    campaigns = campaign_data(game)
    campaign = game.catalog.get("m_encounter_campaigns", campaigns["current_id"]) if campaigns["current_id"] else None
    if campaign is not None and campaign_available(game, campaign, allow_closed=True):
        evaluator = CampaignMissions(game, campaign)
        if any(evaluator.receivable(mission) for mission in evaluator.definitions):
            result.append(campaign["id"])
    return result


def replay_response(game, path, response):
    prefix = "api/event-mission/list/"
    if path.startswith(prefix):
        return {**response, **mission_list(game, {"category_group": number(int(path[len(prefix):]))})}
    return response


EVENT_MISSION_ROUTES = {
    "api/event-mission/list/{0}": route("GET", "category_group", mission_list, "category_group"),
    "api/event-mission/reward/receive": route("POST", "category_group m_mission_ids", mission_receive),
}
