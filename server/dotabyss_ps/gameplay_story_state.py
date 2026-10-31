from __future__ import annotations

from .gameplay_records import find


COMPLETED_SEQUENCE = 99


def is_quest_complete(state, quest_id):
    quest = find(state, "t_chapter_quests", m_chapter_quest_id=quest_id)
    return quest is not None and quest["sequence"] == COMPLETED_SEQUENCE
