from __future__ import annotations

import copy
from datetime import date, timedelta

from .gameplay_catalog import GameplayError, identifiers
from .gameplay_delivery import deliver
from .gameplay_missions import Missions
from .gameplay_nether_state import POINT_FIELDS, ensure, state
from .gameplay_records import rows
from .gameplay_statistics import record as statistic, value as statistic_value
from .gameplay_access import require_feature


class NetherMissions(Missions):
    def __init__(self, game):
        super().__init__(game.state, game.progress, game.catalog, game.now, game.user_id, game.rules)
        self.game = game

    def value(self, mission):
        if mission["situation_type"] == 120000 and not mission.get("target_id"):
            return ensure(self.game)["max_cleared_floor_level"]
        if mission["situation_type"] in (120001, 120002, 120005, 120015):
            start = None
            if mission.get("m_mission_term_id"):
                start = date.fromisoformat(self.catalog.get("m_mission_terms", mission["m_mission_term_id"])["start_at"][:10])
            if mission["situation_type"] == 120001:
                if mission["mission_type"] in (2, 8):
                    start = self.now.date()
                elif mission["mission_type"] in (3, 6):
                    start = max(start or date.min, self.now.date() - timedelta(days=self.now.weekday()))
                values = self.statistics["totals"] if start is None else {
                    key: value for day, entries in self.statistics["days"].items()
                    if start <= date.fromisoformat(day) <= self.now.date() for key, value in entries.items()}
                prefix = f"120001:{mission['target_id']}:"
                return sum(value > 0 and key.rsplit(":", 1)[1] != "0" for key, value in values.items() if key.startswith(prefix))
            mode = "add" if mission["situation_type"] == 120002 else "max"
            value = statistic_value(self.progress, self.now, mission, mode=mode, start=start, data=self.statistics) or 0
            if mission["situation_type"] == 120002:
                return max((row["score_level"] for row in self.catalog.rows("m_nether_result_reward_levels")
                            if row["m_nether_id"] == ensure(self.game)["m_nether_id"] and row["total_score"] <= value), default=0)
            return value
        if mission["situation_type"] == 130000:
            spent = self.statistics["totals"].get("130001:0:0", 0)
            return max(0, spent - state(self.game)["ticket_claims"].get(str(mission["id"]), 0) * mission["require_value"])
        return super().value(mission)


def record_return(game, expedition):
    floor = max((int(key.split(":", 1)[0]) for key in expedition["resolved"]), default=0)
    if not floor:
        return
    maximum = game.catalog.get("m_nether_maps", expedition["map_id"])["max_floor_num"]
    characters = {row["m_character_id"] for row in expedition["party"]["characters"]}
    unions = {game.catalog.get("m_characters", identifier)["union_type"] for identifier in characters}
    count = sum(row["amount"] > 0 for row in rows(game.state, "t_nether_codes"))
    statistic(game.progress, game.now, 120005, count, mode="max")
    if floor >= maximum:
        statistic(game.progress, game.now, 120007)
        for identifier in characters:
            statistic(game.progress, game.now, 120004, 1, identifier)
        if len(unions) == 1:
            statistic(game.progress, game.now, 120003, 1, next(iter(unions)))
    conditions = {(row["situation_type"], row["target_id"], row["sub_target_id"])
                  for row in definitions(game) if row["situation_type"] in (120013, 120014, 120015, 120017)}
    for situation, target, sub in conditions:
        if floor < target or situation == 120013 and unions != {sub} or situation == 120014 and sub not in characters:
            continue
        statistic(game.progress, game.now, situation, count if situation == 120015 else 1,
                  target, sub, mode="max" if situation == 120015 else "add")


def definitions(game):
    return [row for row in game.catalog.rows("m_missions") if 120000 <= row["situation_type"] <= 130000]


def mission_list(game, body):
    require_feature(game, 5)
    evaluator = NetherMissions(game)
    result = {"achievement": [], "daily": [], "weekly": [], "limited": []}
    for mission in sorted(definitions(game), key=lambda row: (row["display_order"], row["id"])):
        if not evaluator.active(mission) or not evaluator.prerequisites(mission):
            continue
        category = "daily" if mission["mission_type"] in (2, 8) else "weekly" if mission["mission_type"] in (3, 6) else "achievement" if mission["mission_type"] in (1, 4) else "limited"
        result[category].append(evaluator.view(mission))
    return result


def mission_receive(game, body):
    require_feature(game, 5)
    selected = identifiers(body["m_mission_ids"], 100)
    if not selected:
        raise GameplayError("No Nether missions were selected", 400, 4007)
    allowed = {row["id"]: row for row in definitions(game)}
    if set(selected) - allowed.keys():
        raise GameplayError("Mission does not belong to Nether", 400, 4007)
    evaluator = NetherMissions(game)
    pending = [allowed[identifier] for identifier in selected]
    while pending:
        advanced = False
        for mission in list(pending):
            key = evaluator.key(mission)
            loop = mission["situation_type"] == 130000 and mission["mission_type"] == 9
            if not loop and key in game.progress["mission_claims"]:
                pending.remove(mission)
                advanced = True
                continue
            if not evaluator.active(mission) or not evaluator.completed(mission) or not evaluator.prerequisites(mission):
                continue
            rewards = [{field: row[field] for field in ("content_type", "content_id", "amount")}
                       for row in game.catalog.rows("m_mission_rewards") if row["group_id"] == mission["m_mission_reward_group_id"]]
            if not rewards:
                raise GameplayError("Nether mission reward group is missing", 503, 5036)
            ordinary = []
            for reward in rewards:
                kind = reward["content_type"]
                if 161 <= kind <= 164:
                    state(game)["points"][POINT_FIELDS[kind - 161]] += reward["amount"]
                else:
                    ordinary.append(reward)
            deliver(game, ordinary)
            if loop:
                claims = state(game)["ticket_claims"]
                claims[str(mission["id"])] = claims.get(str(mission["id"]), 0) + 1
            else:
                game.progress["mission_claims"][key] = copy.deepcopy(rewards)
            pending.remove(mission)
            advanced = True
        if not advanced:
            raise GameplayError("Nether mission requirements have not been met")
    return {}
