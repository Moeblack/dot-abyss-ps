from __future__ import annotations


EVENT_ROW_FIELDS = {
    't_events': {'long': 'm_event_id', 'string': 'join_at updated_at created_at', 'int': 'is_event_stage_effect_enabled'},
    't_event_stage_effect_levels': {'long': 'id m_event_id m_event_stage_effect_group_no', 'int': 'level'},
    't_event_timed_parameters': {'long': 'id m_event_id', 'int': 'type amount capacity recovery duration_type recovery_duration', 'string': 'last_recovery_at'},
    't_event_boss_tickets': {'long': 'id m_event_boss_ticket_id', 'int': 'amount'},
    't_event_battle_stats': {'long': 'id user_id m_event_id target_id value', 'int': 'type'},
    't_event_story_stages': {'long': 'id m_event_story_stage_id', 'int': 'status skip_extra_drop_probability skip_additional_drop_rate', 'long_ids': 'skip_party_characters'},
    't_event_training_stages': {'long': 'id m_event_training_stage_id', 'int': 'status skip_extra_drop_probability skip_additional_drop_rate', 'long_ids': 'skip_party_characters'},
    't_event_miner_quests': {'long': 'id m_event_miner_quest_id', 'int': 'status clear_time clear_rank skip_extra_drop_probability', 'long_ids': 'skip_party_characters', 'string': 'last_cleared_at created_at updated_at'},
    't_event_disaster_quests': {'long': 'm_event_disaster_quest_id', 'int': 'is_cleared status challenge_count phase_no remain_hp skip_extra_drop_probability skip_additional_drop_rate', 'long_ids': 'used_character_list skip_party_characters'},
    't_event_commission_quests': {'long': 'id user_id m_event_commission_quest_id', 'int': 'clear_time status skip_extra_drop_probability skip_additional_drop_rate', 'long_list': 'skip_party_characters', 'string': 'last_cleared_at'},
    't_event_hunt_quests': {'long': 'id m_event_hunt_quest_id', 'int': 'status skip_extra_drop_probability skip_additional_drop_rate', 'string': 'open_at', 'long_ids': 'skip_party_characters'},
    't_event_speedrun_quests': {'long': 'id user_id m_event_speedrun_quest_id', 'int': 'status clear_time', 'string': 'created_at updated_at'},
    't_event_box_gachas': {'long': 'id user_id m_event_id', 'int': 'current_box_no'},
    't_event_bonus_quests': {'long': 'id user_id m_event_id', 'int': 'probability can_skip', 'optional_long': 'm_event_bonus_quest_stage_id'},
    't_event_bonus_quest_stages': {'long': 'id user_id m_event_id stage_id', 'int': 'can_skip skip_extra_drop_probability skip_additional_drop_rate', 'long_ids': 'skip_party_characters'},
}
