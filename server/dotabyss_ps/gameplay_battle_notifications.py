from __future__ import annotations


NOTIFICATION_FIELDS = """
chapter_clear chapter_quest_open chapter_story_open chapter_total_evaluate payment_recommend_release
content_limit_over disaster_boss_unlock disaster_level_up mission_achievement mission_pass_achievement
special_mission_achievement novel_character_release player_exp_up event_division_unlock building_skill_unlock
bonus_encounter_quest_appear event_bonus_quest_occurred vip_rank_up payment_subscription_grace_period
payment_subscription_expired payment_subscription_will_period store_review_request coaching_quest_open
recollection_quest_open tryout_quest_open
""".split()


def battle_notifications(before, after, catalog=None):
    messages = {field: [] for field in NOTIFICATION_FIELDS}
    if catalog is not None and "m_vip_ranks" in catalog.tables:
        ranks = []
        for records in (before, after):
            points = max((row["total_point"] for row in records["t_vip_ranks"]["up"]), default=0)
            ranks.append(max((row["rank"] for row in catalog.rows("m_vip_ranks") if row["total_point"] <= points), default=0))
        if ranks[1] > ranks[0]:
            messages["vip_rank_up"].append({"before_vip_rank": ranks[0], "after_vip_rank": ranks[1]})
    old_bonus = {row["m_event_id"]: row["m_event_bonus_quest_stage_id"] for row in before["t_event_bonus_quests"]["up"]}
    messages["event_bonus_quest_occurred"] = [{"m_event_bonus_quest_stage_id": row["m_event_bonus_quest_stage_id"]}
                                              for row in after["t_event_bonus_quests"]["up"]
                                              if row["m_event_bonus_quest_stage_id"] and old_bonus.get(row["m_event_id"]) != row["m_event_bonus_quest_stage_id"]]
    old_bosses = {row["m_disaster_boss_id"] for row in before["t_disaster_bosses"]["up"]}
    messages["disaster_boss_unlock"] = [{"m_disaster_boss_id": row["m_disaster_boss_id"]}
                                       for row in after["t_disaster_bosses"]["up"] if row["m_disaster_boss_id"] not in old_bosses]
    old_status = before["t_disaster_statuses"]["up"]
    new_status = after["t_disaster_statuses"]["up"]
    if old_status and new_status and new_status[0]["disaster_level"] > old_status[0]["disaster_level"]:
        messages["disaster_level_up"].append({"before_disaster_level": old_status[0]["disaster_level"],
                                            "after_disaster_level": new_status[0]["disaster_level"]})
    for kind in ("coaching", "recollection", "tryout"):
        field = f"m_{kind}_quest_id"
        old = {row[field]: row["status"] for row in before[f"t_{kind}_quests"]["up"]}
        messages[f"{kind}_quest_open"] = [{field: row[field]} for row in after[f"t_{kind}_quests"]["up"]
                                         if row["status"] > 0 and old.get(row[field], 0) == 0]
    return messages if any(messages.values()) else None
