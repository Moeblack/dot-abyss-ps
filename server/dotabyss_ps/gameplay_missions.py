from __future__ import annotations

from datetime import date, datetime, timedelta
from types import SimpleNamespace

from .gameplay_catalog import GameplayError, JST, available, identifiers, parse_timestamp
from .gameplay_records import find, grant, put, rows
from .gameplay_statistics import bucket, value as statistic_value
from .gameplay_rules import load_rules
from .gameplay_delivery import deliver
from .gameplay_character_levels import effective_character_level

TIME_SITUATIONS = {110200, 110201, 110202, 110500, 110600, 110700}
COUNTER_SITUATIONS = {40300, 40400, 40500, 50000, 60000, 80000, 80002, 80003, 90000, 110000, 110001, 110002, 110003,
                     110004, 110005, 110300, 110400, 120000, 120001, 120002, 120003, 120004, 120005, 120007, 120013,
                     120014, 120015, 120017, 120018, 130000, 130001, 140000, 140001, 150000, 150100, 160100,
                     170001, 170002, 170100, 200000, 210001, 250000, 260100, 270000, 280000}
SUPPORTED_SITUATIONS = COUNTER_SITUATIONS | TIME_SITUATIONS | {10000, 10001, 20000, 40000, 40102, 40103, 40200, 70100,
                                                             70200, 70300, 100000, 160000, 180000, 190000, 200001, 210000, 230000, 230001, 240000}


class Missions:
    def __init__(self, state, progress, catalog, now, user_id, rules=None):
        self.state, self.progress, self.catalog, self.now, self.user_id = state, progress, catalog, now, user_id
        self.rules = load_rules() if rules is None else rules
        self.statistics = bucket(progress)

    def key(self, mission):
        kind = mission["mission_type"]
        period = self.now.date().isoformat() if kind in (2, 8) else self.now.date().strftime("%G-W%V") if kind in (3, 6) else "once"
        return f"{period}:{mission['id']}"

    def value(self, mission):
        situation = mission["situation_type"]
        if situation in (120001, 120002, 120005, 120015) and type(self) is Missions:
            from .gameplay_nether_missions import NetherMissions

            context = SimpleNamespace(state=self.state, progress=self.progress, catalog=self.catalog,
                                      now=self.now, user_id=self.user_id, rules=self.rules)
            return NetherMissions(context).value(mission)
        target, sub = mission.get("target_id", 0), mission.get("sub_target_id", 0)
        if situation == 160000 and not target and mission["mission_type"] not in (2, 3, 6, 8):
            recorded = statistic_value(self.progress, self.now, mission, mode="max", data=self.statistics) or 0
            reached = max((row["disaster_level_record_high"] for row in rows(self.state, "t_disaster_statuses")), default=0)
            return max(recorded, reached)
        if situation == 10000:
            days = self.progress["login_days"]
            if mission["mission_type"] in (2, 8):
                return int(self.now.date().isoformat() in days)
            if mission["mission_type"] in (3, 6):
                monday = self.now.date() - timedelta(days=self.now.weekday())
                return sum(monday <= date.fromisoformat(day) <= self.now.date() for day in days)
            return len(days)
        if situation == 10001:
            return 1
        if situation == 240000:
            return self.rules["local_platform_missions_complete"]
        if situation == 20000:
            if not target:
                last_sequences = {}
                for sequence in self.catalog.rows("m_chapter_quest_sequences"):
                    identifier = sequence["m_chapter_quest_id"]
                    last_sequences[identifier] = max(last_sequences.get(identifier, 0), sequence["sequence"])
                completed = {quest["m_chapter_quest_id"] for quest in rows(self.state, "t_chapter_quests")
                             if quest["m_chapter_quest_id"] in last_sequences
                             and quest["sequence"] > last_sequences[quest["m_chapter_quest_id"]]}
                return len(completed) + (statistic_value(self.progress, self.now, mission, data=self.statistics) or 0)
            quest = find(self.state, "t_chapter_quests", m_chapter_quest_id=target)
            sequences = [r["sequence"] for r in self.catalog.rows("m_chapter_quest_sequences") if r["m_chapter_quest_id"] == target]
            return int(quest is not None and bool(sequences) and quest["sequence"] > max(sequences))
        if situation in (260100, 270000):
            kind = "coaching" if situation == 260100 else "tryout"
            quest = find(self.state, f"t_{kind}_quests", **{f"m_{kind}_quest_id": target})
            if quest is None:
                return 0
            return int(quest["status"] == 99) if situation == 260100 else quest["high_score"]
        if situation in (170002, 170100):
            stages = {row["m_union_type_id"]: row["stage"] for row in rows(self.state, "t_union_requests")}
            completed = [row for row in self.catalog.rows("m_union_request_stages")
                         if row["stage"] < stages.get(row["m_union_type_id"], 1)]
            if situation == 170002:
                return int(any(row["id"] == target for row in completed)) if target else len(completed)
            return max((row["stage"] for row in completed if not target or row["m_union_type_id"] == target), default=0)
        if situation == 40000:
            return len(rows(self.state, "t_characters"))
        if situation == 40102:
            return sum(effective_character_level(self, r) >= target for r in rows(self.state, "t_characters"))
        if situation == 40103:
            levels = self.catalog.rows("m_character_intimacy_levels")
            needed = next((r["total_point"] for r in levels if r["level"] == target), None)
            return None if needed is None else sum(r["intimacy_point"] >= needed for r in rows(self.state, "t_characters"))
        if situation == 40200:
            return sum(r["level"] >= target for r in rows(self.state, "t_character_abilities"))
        if situation in (70100, 70200, 70300):
            kind = {70100: 2, 70200: 3, 70300: 4}[situation]
            return sum(self.key(r) in self.progress["mission_claims"] for r in self.catalog.rows("m_missions")
                       if r["mission_type"] == kind and not r["m_mission_pass_id"] and r["situation_type"] not in (70100, 70200, 70300)
                       and (not target or r["mission_group_id"] == target) and self.active(r))
        if situation == 100000:
            return rows(self.state, "t_statuses")[0]["user_rank"]
        if situation == 180000:
            record = find(self.state, "t_building_skills", m_building_id=target, skill_type=sub)
            return record["skill_level"] if record else 0
        if situation == 190000:
            return max((r["level"] for r in rows(self.state, "t_level_synchros")), default=30)
        if situation == 200001:
            return max((r["max_score"] for r in rows(self.state, "t_tavern_shops")), default=0)
        if situation in (230000, 230001):
            plan = find(self.state, "t_plans", m_plan_id=target)
            return int(plan is not None) if situation == 230000 else plan["step"] if plan else 0
        if situation in COUNTER_SITUATIONS | TIME_SITUATIONS | {160000, 210000}:
            start = None
            term = mission.get("m_mission_term_id")
            if mission["mission_type"] in (4, 5, 6, 8) and term:
                value = self.catalog.get("m_mission_terms", term).get("start_at")
                if value:
                    start = date.fromisoformat(value[:10])
            mode = "min" if situation in TIME_SITUATIONS else "max" if situation in (110300, 160000, 170100, 210000, 270000) else "add"
            value = statistic_value(self.progress, self.now, mission, mode=mode, start=start, data=self.statistics)
            return value if situation in TIME_SITUATIONS else value or 0
        return None

    def completed(self, mission):
        value = self.value(mission)
        return value is not None and (0 < value <= mission["require_value"] if mission["situation_type"] in TIME_SITUATIONS else value >= mission["require_value"])

    def active(self, mission):
        term = mission.get("m_mission_term_id", 0)
        return not term or available(self.catalog.get("m_mission_terms", term), self.now)

    def prerequisites(self, mission):
        previous = mission.get("require_mission_id", 0)
        return not previous or self.key(self.catalog.get("m_missions", previous)) in self.progress["mission_claims"]

    def view(self, mission):
        return {"m_mission_id": mission["id"], "value": self.value(mission) or 0, "calculate_type": 1 if mission["situation_type"] in TIME_SITUATIONS else 2,
                "is_received": int(self.key(mission) in self.progress["mission_claims"])}

    def listing(self):
        result = {"daily": [], "weekly": [], "achievement": []}
        categories = {1: "achievement", 2: "daily", 3: "weekly"}
        for mission in sorted(self.catalog.rows("m_missions"), key=lambda r: (r["display_order"], r["id"])):
            category = categories.get(mission["mission_type"])
            if category and not mission.get("m_mission_pass_id") and self.active(mission) and self.prerequisites(mission):
                result[category].append(self.view(mission))
        return result

    def special(self, identifier):
        definition = self.catalog.get("m_special_missions", identifier)
        if not available(definition, self.now):
            raise GameplayError("Special mission is not active")
        record = find(self.state, "t_special_missions", m_special_mission_id=identifier)
        if record is None:
            end = definition["end_at"]
            if definition["valid_day"] > 0:
                limit = self.now + timedelta(days=definition["valid_day"])
                if end:
                    declared = parse_timestamp(end)
                    limit = min(limit, declared if declared.tzinfo else declared.replace(tzinfo=JST))
                end = limit.isoformat()
            record = put(self.state, "t_special_missions", {"m_special_mission_id": identifier}, {
                "start_at": self.now.isoformat(), "end_at": end, "completed_at": "",
                "created_at": self.now.strftime("%Y-%m-%d %H:%M:%S"), "updated_at": self.now.isoformat()}, self.user_id)
        return record, [r for r in self.catalog.rows("m_special_mission_days") if r["m_special_mission_id"] == identifier]

    def special_top(self, identifier):
        _, days = self.special(identifier)
        values = [self.view(self.catalog.get("m_missions", row["m_mission_id"])) for row in sorted(days, key=lambda r: (r["day"], r["id"]))]
        return {"missions": {"special": values}, "badges": self.badges()}

    def receivable(self, mission):
        return (self.completed(mission) and self.active(mission)
                and self.prerequisites(mission) and self.key(mission) not in self.progress["mission_claims"])

    def badges(self):
        ordinary = any(self.receivable(m) for m in self.catalog.rows("m_missions")
                       if m["mission_type"] in (1, 2, 3) and not m.get("m_mission_pass_id"))
        special = []
        for record in rows(self.state, "t_special_missions"):
            if not available(record, self.now):
                continue
            elapsed = (self.now.date() - date.fromisoformat(record["start_at"][:10])).days + 1
            eligible = [self.catalog.get("m_missions", d["m_mission_id"]) for d in self.catalog.rows("m_special_mission_days")
                        if d["m_special_mission_id"] == record["m_special_mission_id"] and d["day"] <= elapsed]
            special.append({"t_special_mission_id": record["id"], "m_special_mission_id": record["m_special_mission_id"],
                            "is_receivable": int(any(self.receivable(m) for m in eligible))})
        return {"info": 0, "present_count": 0, "mission_reward": int(ordinary), "mission_pass_reward": 0,
                "sp_mission_reward": special, "building": 0, "unarrived_plan_mission": 0}

    def receive(self, requested, special_id=None, *, allowed_ids=None):
        ids = identifiers(requested, 100)
        if not ids:
            raise GameplayError("No missions were selected", 400, 4007)
        allowed_days = None
        if special_id is not None:
            record, days = self.special(special_id)
            if not available(record, self.now):
                raise GameplayError("Special mission reward period has ended")
            started = date.fromisoformat(record["start_at"][:10])
            elapsed = (self.now.date() - started).days + 1
            allowed_days = {r["m_mission_id"] for r in days if r["day"] <= elapsed}
        pending, waiting = [], []
        for identifier in ids:
            mission = self.catalog.get("m_missions", identifier)
            if allowed_ids is not None:
                if identifier not in allowed_ids:
                    raise GameplayError("Mission does not belong to this reward endpoint", 400, 4007)
            elif allowed_days is not None:
                if identifier not in allowed_days or mission["mission_type"] != 7:
                    raise GameplayError("Special mission day is locked")
            elif mission["mission_type"] not in (1, 2, 3) or mission.get("m_mission_pass_id"):
                raise GameplayError("Mission does not belong to this reward endpoint", 400, 4007)
            if not self.active(mission):
                raise GameplayError("Mission is not available")
            key = self.key(mission)
            if key in self.progress["mission_claims"]:
                continue
            waiting.append(mission)
        while waiting:
            count = len(waiting)
            for mission in list(waiting):
                if not self.prerequisites(mission) or not self.completed(mission):
                    continue
                rewards = [{k: r[k] for k in ("content_type", "content_id", "amount")} for r in self.catalog.rows("m_mission_rewards")
                           if r["group_id"] == mission["m_mission_reward_group_id"]]
                if not rewards:
                    raise GameplayError("Mission reward group is missing", 503, 5036)
                key = self.key(mission)
                self.progress["mission_claims"][key] = rewards
                pending.append((key, rewards))
                waiting.remove(mission)
            if len(waiting) == count:
                unknown = next((r for r in waiting if r["situation_type"] not in SUPPORTED_SITUATIONS), None)
                if unknown:
                    raise GameplayError("Mission condition is not implemented: " + str(unknown["situation_type"]), 501, 5010)
                raise GameplayError("Mission or its prerequisites have not been completed")
        for key, rewards in pending:
            deliver(self, rewards)
            self.progress["mission_claims"][key] = rewards
        return {}
