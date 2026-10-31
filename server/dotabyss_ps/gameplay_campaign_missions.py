from __future__ import annotations

from collections import Counter

from .gameplay_catalog import available, number
from .gameplay_event_missions import add_statistic, validate_statistics
from .gameplay_missions import Missions
from .gameplay_statistics import key as statistic_key


def definitions(game, campaign):
    return [row for row in game.catalog.rows("m_missions")
            if row["m_mission_term_id"] == campaign["m_mission_term_id"]
            and row["mission_type"] in (4, 8) and not row["m_mission_pass_id"]]


def mission_campaign_id(game, encounter):
    from .gameplay_encounter_campaign import campaign_data, selected_campaign

    origin = campaign_data(game)["origins"].get(str(encounter["id"]))
    if origin is not None:
        return origin["campaign_id"]
    selected = selected_campaign(game)
    return selected["id"] if selected else 0


def statistics(game, campaign_id):
    from .gameplay_encounter_campaign import campaign_entry

    entry = campaign_entry(game, campaign_id)
    if "mission_stats" not in entry:
        counts = entry["clear_counts"]
        totals = {statistic_key(150100): sum(counts.values())}
        totals.update({statistic_key(150100, int(identifier)): amount for identifier, amount in counts.items()})
        entry["mission_stats"] = {"totals": totals, "days": {}}
    return validate_statistics(entry["mission_stats"])


def record_clear(game, campaign_id, quest_id, *, session=None, stage_results=None):
    data = statistics(game, campaign_id)
    add_statistic(game, data, 150100, 1)
    add_statistic(game, data, 150100, 1, quest_id)
    if session is None or stage_results is None:
        return
    from .gameplay_battle_rewards import stage_entities

    enemies, _, _, _ = stage_entities(session["stage_detail"])
    destroyed = stage_results["destroyed_enemies"]
    add_statistic(game, data, 140000, len(destroyed))
    jobs = Counter(game.catalog.get("m_enemies", enemies[sid]["m_enemy_id"])["m_job_id"] for sid in destroyed)
    for job, count in jobs.items():
        add_statistic(game, data, 140001, count, job)


class CampaignMissions(Missions):
    def __init__(self, game, campaign):
        super().__init__(game.state, game.progress, game.catalog, game.now, game.user_id, game.rules)
        self.game, self.campaign = game, campaign
        self.definitions = definitions(game, campaign)
        self.allowed_ids = {mission["id"] for mission in self.definitions}

    def joined(self):
        from .gameplay_encounter_campaign import campaign_data

        return str(self.campaign["id"]) in campaign_data(self.game)["entries"]

    def period_active(self, mission):
        from .gameplay_encounter_campaign import available as campaign_available

        if mission["id"] not in self.allowed_ids or not campaign_available(self.game, self.campaign, allow_closed=True):
            return False
        term = self.catalog.get("m_mission_terms", mission["m_mission_term_id"])
        return bool(self.rules["event_open_all"]) or available(term, self.now)

    def active(self, mission):
        return self.joined() and self.period_active(mission)

    def value(self, mission):
        if mission["situation_type"] not in (150100, 140000, 140001):
            return super().value(mission)
        if not self.joined():
            return 0
        data = statistics(self.game, self.campaign["id"])
        values = data["days"].get(self.now.date().isoformat(), {}) if mission["mission_type"] == 8 else data["totals"]
        key = statistic_key(mission["situation_type"], mission["target_id"], mission["sub_target_id"])
        return number(values.get(key, 0), 0, 2 ** 63 - 1)

    def listing(self, *, preview=False):
        result = {"limited": [], "limited_daily": []}
        for mission in sorted(self.definitions, key=lambda row: (row["display_order"], row["id"])):
            active = self.period_active(mission) if preview else self.active(mission)
            if active and self.prerequisites(mission):
                result["limited_daily" if mission["mission_type"] == 8 else "limited"].append(self.view(mission))
        return result
