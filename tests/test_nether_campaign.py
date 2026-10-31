import copy
import json
from collections import Counter

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_battle_session import battle_state
from dotabyss_ps.gameplay_nether_events import code_rows
from dotabyss_ps.gameplay_nether_missions import NetherMissions
from dotabyss_ps.gameplay_nether_state import ensure, floor_key, point_view, run
from dotabyss_ps.gameplay_records import put
from test_battle_rewards import reward_game, reward_master
from test_gameplay import party_body


def execute(game, path, body=None):
    return game.execute("api/" + path, "GET" if body is None else "POST", body or {})


def coordinates(floor):
    return {key: floor[key] for key in ("m_nether_id", "m_nether_map_id", "floor_level", "floor_index")}


def choose_codes(game):
    count = 0
    while ensure(game)["m_nether_code_ids"]:
        owned = [row for row in code_rows(game) if row["amount"]]
        execute(game, "nether/fix-code", {"m_nether_id": 1, "m_nether_map_id": 1,
                "m_nether_code_id": ensure(game)["m_nether_code_ids"][0],
                "remove_m_nether_code_id": owned[0]["m_nether_code_id"] if len(owned) >= point_view(game)["max_nether_code"] else 0})
        count += 1
    return count


def win_battle(game, floor):
    execute(game, "nether/start-battle", coordinates(floor))
    stage = battle_state(game)["active"]["stage_detail"]
    body = {**coordinates(floor),
            "character_hps": [{"m_character_id": int(key), "current_hp_ratio": 1000} for key in run(game)["hp"]],
            "stage_results": {"play_time": 1, "destroyed_enemies": [row["sid"] for row in stage["enemies"]],
                              "drop_items": [row["sid"] for row in stage["drops"]], "mana_gem_count": 0,
                              "top_fc_damage": 100, "total_damage": 1000,
                              "passed_floor_parts": [row["sid"] for row in stage["floor_parts"]], "clear_rank": 0}}
    execute(game, "nether/clear-battle", body)


def resolve_event(game, floor):
    definition = game.catalog.get("m_nether_floor_events", floor["extend_id"])
    owned = [row for row in code_rows(game) if row["amount"]]
    for choice in range(1, 5):
        identifier = definition[f"m_nether_floor_event_part_id_{choice}"]
        if not identifier:
            continue
        part = game.catalog.get("m_nether_floor_event_parts", identifier)
        transform = any(part[f"target_type_{index}"] == 7 for index in (1, 2, 3))
        full = len(owned) >= point_view(game)["max_nether_code"]
        if transform and not owned:
            continue
        code = owned[0]["m_nether_code_id"] if transform or (part["content_type"] == 160 and full) else 0
        execute(game, "nether/update-event", {**coordinates(floor), "select_number": choice, "m_nether_code_id": code})
        return
    raise AssertionError("No feasible event option")


def test_single_save_completes_all_150_floors_and_enters_again(reward_game):
    source = reward_game
    game = Gameplay(source.state, {}, source.catalog, source.user_id, source.now, source.rules)
    put(game.state, "t_feature_locks", {"m_feature_lock_id": 5}, {"is_unlocked": True})
    execute(game, "party/exploration/edit", party_body())
    put(game.state, "t_items", {"m_item_id": 200002}, {"amount": 100})
    execute(game, "nether/start", {"m_nether_id": 1, "m_nether_map_id": 1, "party_no": 1,
                                  "use_ticket": 1, "start_floor_level": 1})
    counts = Counter()
    for level in range(1, 151):
        current, expedition = ensure(game), run(game)
        current.update(nether_gold=100000, treasure_key=1000)
        expedition["hp"] = {key: 1000 for key in expedition["hp"]}
        counts["codes"] += choose_codes(game)
        if current["status"] == 3:
            execute(game, "nether/continue", {"m_nether_id": 1, "m_nether_map_id": 1,
                    "floor_level": current["floor_level"], "use_ticket": 1, "lock_items": []})
            counts["continue"] += 1
        choices = [row for row in expedition["floors"] if row["floor_level"] == level]
        floor = next(row for row in choices if all(
            restriction["restrict_param_1"] <= current["erosion_point"] <= restriction["restrict_param_2"]
            for restriction in game.catalog.rows("m_nether_floor_restricts")
            if restriction["m_nether_map_floor_id"] == row["m_nether_map_floor_id"]))
        execute(game, "nether/update", coordinates(floor))
        kind = game.catalog.get("m_nether_map_floors", floor["m_nether_map_floor_id"])["type"]
        counts[kind] += 1
        if kind in (4, 5, 7):
            resolve_event(game, floor)
        elif kind == 6:
            item = next(row for row in game.catalog.rows("m_nether_floor_shop_contents")
                        if row["m_nether_floor_shop_id"] == floor["extend_id"] and row["content_type"] != 160)
            execute(game, "nether/update-shop", {"shop_item_amount": [{"m_nether_floor_shop_content_id": item["id"], "amount": 1}]})
        if floor_key(floor) not in expedition["resolved"]:
            win_battle(game, floor)
        assert expedition["cleared_count"] == level
        assert len(set(expedition["resolved"])) == level
        saved = json.dumps([game.state, game.progress])
        assert len(saved) < 2 * 1024 * 1024
        restored_state, restored_progress = json.loads(saved)
        game = Gameplay(restored_state, restored_progress, source.catalog, source.user_id, source.now, source.rules)
    assert ensure(game)["status"] == 8
    assert ensure(game)["m_nether_code_ids"]
    counts["codes"] += choose_codes(game)
    assert counts["codes"] >= 15
    assert counts["continue"] == 14
    assert counts[2] == 15 and counts[1] > 0 and counts[4] > 0 and counts[6] > 0
    request = {"m_nether_id": 1, "m_nether_map_id": 1, "use_item_flag": 0}
    result = execute(game, "nether/result", request)
    assert result["result_score"] == 15000
    assert result["result_score_bonus"] == 1
    statistics = game.progress["features"]["statistics"]["data"]["totals"]
    assert statistics["120018:0:0"] == 15
    assert statistics["120007:0:0"] == 1
    assert statistics["120017:130:0"] == 1
    assert statistics["120004:1200002:0"] == 1
    assert statistics["120015:130:0"] == 8
    mission = game.catalog.get("m_missions", 92000001)
    assert NetherMissions(game).value(mission) == max(
        row["score_level"] for row in game.catalog.rows("m_nether_result_reward_levels") if row["total_score"] <= 15000)
    prior = copy.deepcopy([game.state, game.progress])
    assert execute(game, "nether/result", request)["result_rewards"] == result["result_rewards"]
    assert [game.state, game.progress] == prior
    execute(game, "nether/start", {"m_nether_id": 1, "m_nether_map_id": 1, "party_no": 1,
                                  "use_ticket": 1, "start_floor_level": 1})
    assert run(game)["id"] == 2 and not run(game)["resolved"]
    assert ensure(game)["max_cleared_floor_level"] == 150
