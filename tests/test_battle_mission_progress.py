import copy
import json
from collections import Counter

from dotabyss_ps.gameplay_battle_rewards import settle_battle
from dotabyss_ps.gameplay_catalog import GameplayCatalog
from dotabyss_ps.gameplay_missions import Missions
from dotabyss_ps.gameplay_records import put
from test_battle_rewards import clear_result, reward_game, reward_master, session
from test_economy_features import update
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_main_repeat import prepare_repeat
from test_main_story_battles import active_session, first_battle, victory


def mission_value(context, identifier=70112, *, target=None):
    state, progress = map(json.loads, snapshot(context))
    catalog = GameplayCatalog(context.tables, context.schema)
    mission = copy.deepcopy(catalog.get("m_missions", identifier))
    if target is not None:
        mission["target_id"] = target
    return Missions(state, progress, catalog, NOW, context.identity["user_id"]).value(mission)


def totals(context):
    return json.loads(snapshot(context)[1])["features"]["statistics"]["data"]["totals"]


def test_any_main_story_mission_waits_for_quest_completion_and_does_not_double_count(setup_gameplay):
    c = setup_gameplay
    _, battle = first_battle(c)
    call(c, "api/main-story/clear-battle", victory(battle))
    assert mission_value(c) == 0
    assert mission_value(c, target=1010101) == 0
    call(c, "api/main-story/clear", {"m_chapter_quest_id": 1010101})
    assert mission_value(c) == 1
    assert mission_value(c, target=1010101) == 1
    assert mission_value(c, 100101) == 0
    call(c, "api/main-story/clear", {"m_chapter_quest_id": 1010101})
    assert mission_value(c) == 1
    unrelated = {110000, 110001, 110005, 120000, 120018, 130000, 150100, 160100, 170001}
    assert not any(int(key.split(":")[0]) in unrelated for key in totals(c))


def test_existing_completed_quests_contribute_without_a_new_counter_or_reward(setup_gameplay):
    c = setup_gameplay
    call(c, "api/user")
    catalog = GameplayCatalog(c.tables, c.schema)
    quests = sorted(catalog.rows("m_chapter_quests"), key=lambda row: row["id"])[:4]

    def imported_progress(state, progress):
        for index, quest in enumerate(quests):
            area = catalog.get("m_chapter_areas", quest["m_chapter_area_id"])
            last = max(row["sequence"] for row in catalog.rows("m_chapter_quest_sequences") if row["m_chapter_quest_id"] == quest["id"])
            put(state, "t_chapter_quests", {"m_chapter_quest_id": quest["id"]}, {
                "m_chapter_id": area["m_chapter_id"], "m_chapter_area_id": area["id"],
                "sequence": last + 1 if index < 3 else last,
            })

    update(c, imported_progress)
    before = snapshot(c)
    assert mission_value(c) == 3
    assert mission_value(c, target=quests[0]["id"]) == 1
    assert mission_value(c, target=quests[-1]["id"]) == 0
    assert snapshot(c) == before


def test_enemy_type_mission_uses_job_identity_and_settlement_is_idempotent(reward_game):
    game = reward_game
    matching = next(row for row in game.catalog.rows("m_enemies") if row["m_job_id"] == 2029000)
    battle = session(game)
    for enemy in battle["stage_detail"]["enemies"]:
        enemy["m_enemy_id"] = matching["id"]
    body = clear_result(battle)
    settle_battle(game, battle, body)
    evaluator = Missions(game.state, game.progress, game.catalog, game.now, game.user_id)
    assert evaluator.value(game.catalog.get("m_missions", 60007)) == 2
    counts = game.progress["features"]["statistics"]["data"]["totals"]
    assert counts["140000:0:0"] == counts["140001:2029000:0"] == 2
    assert f"140001:{matching['id']}:0" not in counts
    before = copy.deepcopy((game.state, game.progress))
    settle_battle(game, battle, body)
    assert (game.state, game.progress) == before


def test_main_battle_counts_only_validated_enemy_jobs(setup_gameplay):
    c = setup_gameplay
    _, battle = first_battle(c)
    catalog = GameplayCatalog(c.tables, c.schema)
    expected = Counter(catalog.get("m_enemies", enemy["m_enemy_id"])["m_job_id"] for enemy in battle["stage_detail"]["enemies"])
    body = victory(battle)
    rejected = copy.deepcopy(body)
    rejected["stage_results"]["destroyed_enemies"].append(999999999999)
    before = snapshot(c)
    call(c, "api/main-story/clear-battle", rejected, expected=400)
    assert snapshot(c) == before
    call(c, "api/main-story/clear-battle", body)
    actual = totals(c)
    assert actual["140000:0:0"] == sum(expected.values())
    assert {int(key.split(":")[1]): value for key, value in actual.items() if key.startswith("140001:")} == dict(expected)
    before = snapshot(c)
    call(c, "api/main-story/clear-battle", body)
    assert snapshot(c) == before


def test_repeat_and_skip_share_main_story_and_enemy_counters(setup_gameplay):
    c = setup_gameplay
    repeat = prepare_repeat(c)
    initial_quests = mission_value(c)
    call(c, "api/main-story/repeat/start-battle", {"m_chapter_area_id": repeat["m_chapter_area_id"], "party_no": 1})
    body = {"stage_results": victory(active_session(c))["stage_results"]}
    call(c, "api/main-story/repeat/clear-battle", body)
    assert mission_value(c) == initial_quests + 1
    before = snapshot(c)
    call(c, "api/main-story/repeat/clear-battle", body)
    assert snapshot(c) == before
    headers = {**c.headers, "x-request-id": "mission-skip-batch"}
    body = {"m_chapter_area_id": repeat["m_chapter_area_id"], "skip_count": 2}
    call(c, "api/main-story/repeat/skip-battle", body, headers=headers)
    assert mission_value(c) == initial_quests + 3
    actual = totals(c)
    assert actual["20000:0:0"] == 3
    assert actual["140000:0:0"] == sum(value for key, value in actual.items() if key.startswith("140001:"))
    before = snapshot(c)
    call(c, "api/main-story/repeat/skip-battle", body, headers=headers)
    assert snapshot(c) == before
