import copy
import json
from datetime import datetime

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_records import find, rows
from dotabyss_ps.gameplay_rules import load_rules
from dotabyss_ps.gameplay_story_progress import available_quests, completed, sequences
from dotabyss_ps.gameplay_catalog import JST
from dotabyss_ps.newgame import build_initial_state
from dotabyss_ps.storage import Store
from test_battle_rewards import reward_master


def test_one_save_progresses_through_every_main_story_quest(reward_master):
    tables, schema, catalog = reward_master
    state = build_initial_state(tables, schema, 7, "Campaign test", "2026-10-31 10:00:00", start_tutorial=False)
    progress = {}
    now = datetime(2026, 10, 31, 19, tzinfo=JST)
    rules = load_rules()

    def game():
        return Gameplay(state, progress, catalog, 7, now, rules)

    def execute(path, body):
        return game().execute(path, "POST", body)

    execute("api/party/exploration/edit", {
        "party_no": 1,
        "characters": [{"position_no": index, "t_character_id": row["id"], "t_weapon_id": 0,
                        "t_armor_id": 0, "t_accessory_id": 0}
                       for index, row in enumerate(rows(state, "t_characters")[:3], 1)],
        "mana_gems": [],
    })
    remaining = {quest["id"] for quest in catalog.rows("m_chapter_quests")}
    finished, battle_count, releases, peak_save_bytes = [], 0, set(), 0
    while remaining:
        reachable = available_quests(game()) & remaining
        assert reachable, f"Unreachable main-story quests: {sorted(remaining)}"
        quest_id = min(reachable)
        steps = sequences(game(), quest_id)
        while not completed(game(), quest_id):
            row = find(state, "t_chapter_quests", m_chapter_quest_id=quest_id)
            current = row["sequence"] if row is not None else 0
            if current == 0:
                execute("api/main-story/update-sequence", {"m_chapter_quest_id": quest_id, "sequence": 1})
                continue
            step = steps[current - 1]
            if step["m_chapter_quest_battle_id"]:
                find(state, "t_timed_parameters", type=5)["amount"] = 999
                started = execute("api/main-story/start-battle", {"m_chapter_quest_id": quest_id, "party_no": 1})
                session = progress["features"]["battle"]["data"]["active"]
                assert json.loads(started["stage_detail"]) == session["stage_detail"]
                stage = session["stage_detail"]
                enemies = stage["enemies"] + ([stage["boss_enemy"]] if stage.get("boss_enemy") else [])
                result = {"play_time": 78, "destroyed_enemies": [enemy["sid"] for enemy in enemies],
                          "drop_items": [drop["sid"] for drop in stage["drops"]], "mana_gem_count": 0, "top_fc_damage": 0}
                if session["stage_type"] == 1:
                    result.update(total_damage=0, passed_floor_parts=[floor["sid"] for floor in stage["floor_parts"]], clear_rank=0)
                body = {"m_chapter_quest_id": quest_id, "stage_results": result}
                before_available = available_quests(game())
                cleared = execute("api/main-story/clear-battle", body)
                assert progress["features"]["battle"]["data"]["active"] is None
                stable = copy.deepcopy((state, progress))
                repeated = execute("api/main-story/clear-battle", body)
                assert repeated["battle_result"] == cleared["battle_result"]
                assert (state, progress) == stable
                if completed(game(), quest_id):
                    unlocked = available_quests(game()) - before_available
                    assert set(cleared["release_data"]["release_m_chapter_quest_ids"]) == unlocked
                    releases.update(unlocked)
                battle_count += 1
            elif current < len(steps):
                execute("api/main-story/update-sequence", {"m_chapter_quest_id": quest_id, "sequence": current + 1})
            else:
                before_available = available_quests(game())
                if len(finished) % 2:
                    execute("api/main-story/update-sequence", {"m_chapter_quest_id": quest_id, "sequence": current + 1})
                cleared = execute("api/main-story/clear", {"m_chapter_quest_id": quest_id})
                unlocked = available_quests(game()) - before_available
                assert set(cleared["release_data"]["release_m_chapter_quest_ids"]) == unlocked
                releases.update(unlocked)
        stable = copy.deepcopy((state, progress))
        execute("api/main-story/clear", {"m_chapter_quest_id": quest_id})
        assert (state, progress) == stable
        remaining.remove(quest_id)
        finished.append(quest_id)
        serialized_state = Store._state_json(state)
        serialized_progress = Store._state_json(progress)
        peak_save_bytes = max(peak_save_bytes, len(serialized_state.encode()), len(serialized_progress.encode()))
        state, progress = json.loads(serialized_state), json.loads(serialized_progress)
    assert len(finished) == 207
    assert battle_count == 178
    claims = progress["features"]["battle_rewards"]["data"]
    assert len(claims["first_claims"]) == battle_count
    assert len(claims["quest_claims"]) == len(finished)
    assert releases == set(finished) - {finished[0]}
    assert sum(len(rows(state, table)) for table in ("t_weapons", "t_armors", "t_accessories")) <= 100
    assert peak_save_bytes < 2 * 1024 * 1024
