from __future__ import annotations

import copy
import random
from collections import Counter

from .gameplay_battle_rewards import stage_entities, validate_stage_results
from .gameplay_battle_session import battle_state, create_session
from .gameplay_battle_stages import build_stage
from .gameplay_catalog import GameplayError
from .gameplay_nether_configuration import frozen_nether
from .gameplay_receipts import operation_digest
from .gameplay_statistics import record as statistic
from .gameplay_nether_events import grant_local, offer_codes, resolve_floor
from .gameplay_nether_party import update_hp
from .gameplay_nether_state import ensure, floor_key, hp_view, point_view, reveal_routes, run, selected_floor


@frozen_nether
def attach_rewards(game, stage, floor_level, seed):
    rng = random.Random(f"nether-rewards:{seed}")
    enemies, resources, floors, _ = stage_entities(stage)
    sid = max([0, *enemies, *resources, *floors]) + 1
    stage["drops"] = []
    rewards = {}
    additional = min(1000, max(0, point_view(game)["extra_drop_ratio"]))
    for entity in [*enemies.values(), *resources.values()]:
        entity["drops"] = []
        if rng.randrange(1000) >= game.rules["nether_random_drop_probability_per_mille"]:
            continue
        item_id = 200003 + min(3, max(0, (floor_level - 1) // 40))
        game.catalog.get("m_items", item_id)
        reward = {"sid": sid, "content_type": 31, "content_id": item_id, "amount": 1,
                  "rarity_level": 3, "is_rare_drop": 0, "m_drop_id": 0}
        stage["drops"].append(reward)
        entity["drops"].append(sid)
        rewards[str(sid)] = reward
        sid += 1
        if rng.randrange(1000) < additional:
            extra = {**reward, "sid": sid}
            stage["drops"].append(extra)
            entity["drops"].append(sid)
            rewards[str(sid)] = {**extra, "bonus_type": 1}
            sid += 1
    return rewards


@frozen_nether
def start_battle(game, body):
    run(game)
    current, data, floor = selected_floor(game, body)
    if (current["floor_level"], current["floor_index"]) != (floor["floor_level"], floor["floor_index"]):
        raise GameplayError("Enter the Nether floor before starting its battle")
    if floor_key(floor) in data["resolved"]:
        raise GameplayError("Nether floor is already complete")
    master = game.catalog.get("m_nether_map_floors", floor["m_nether_map_floor_id"])
    if master["type"] in (1, 2, 3):
        node = game.catalog.get("m_nether_floor_battles", floor["extend_id"])
        battle = game.catalog.get("m_nether_battle_stages", node["m_nether_battle_stage_id"])
    elif data.get("event_battle"):
        battle = game.catalog.get("m_nether_battle_stages", data["event_battle"])
    else:
        raise GameplayError("Nether floor has no pending battle")
    if not any(data["hp"].values()):
        raise GameplayError("No Nether expedition character can fight")
    seed = battle_state(game)["next_id"]
    stage = build_stage(game, 1, battle["m_dungeon_id"], seed, kind="nether", quest_id=battle["id"])
    rewards = attach_rewards(game, stage, floor["floor_level"], seed)
    definition = {**battle, "stage_type": 1, "stage_id": battle["m_dungeon_id"], "m_fixed_party_id": 0}
    response = create_session(game, definition, kind="nether", quest_id=battle["id"], party_no=data["party_no"],
                              stage_detail=stage, reward_index=rewards, party_detail=copy.deepcopy(data["party"]),
                              context={"m_nether_id": current["m_nether_id"], "m_nether_map_id": current["m_nether_map_id"],
                                       "floor_level": floor["floor_level"], "floor_index": floor["floor_index"]},
                              validate_members=False)
    current["status"] = 5
    response["t_nether_characters"] = hp_view(game)
    return response


@frozen_nether
def clear_battle(game, body):
    current, data, floor = selected_floor(game, body)
    digest = operation_digest("api/nether/clear-battle", "POST", body)
    if data.get("last_battle") and data["last_battle"]["digest"] == digest:
        return copy.deepcopy(data["last_battle"]["response"])
    active = battle_state(game)["active"]
    expected = {key: body[key] for key in ("m_nether_id", "m_nether_map_id", "floor_level", "floor_index")}
    if active is None or active["kind"] != "nether" or active["context"] != expected:
        raise GameplayError("No matching Nether battle is active")
    result = validate_stage_results(active["stage_detail"], body["stage_results"], 1, time_limit=active["time_limit"])
    update_hp(game, data, body["character_hps"])
    rewards = []
    for sid in result["drop_items"]:
        reward = active["reward_index"][str(sid)]
        grant_local(game, reward["content_type"], reward["content_id"], reward["amount"])
        rewards.append({key: reward[key] for key in ("content_type", "content_id", "amount", "is_rare_drop")})
        rewards[-1].update(bonus_type=reward.get("bonus_type", 0), additional_info=None)
    enemies, _, _, _ = stage_entities(active["stage_detail"])
    statistic(game.progress, game.now, 140000, len(result["destroyed_enemies"]))
    jobs = Counter(game.catalog.get("m_enemies", enemies[sid]["m_enemy_id"])["m_job_id"] for sid in result["destroyed_enemies"])
    for identifier, count in jobs.items():
        statistic(game.progress, game.now, 140001, count, identifier)
    kind = game.catalog.get("m_nether_map_floors", floor["m_nether_map_floor_id"])["type"]
    if kind in (2, 3):
        current["strong_enemy_count"] += 1
    rate = 1000 if kind == 2 else game.catalog.get("m_nether_floor_battles", floor["extend_id"])["code_drop_ratio"] if kind in (1, 3) else 300
    if random.Random(f"nether-code-drop:{data['id']}:{floor_key(floor)}").randrange(1000) < rate:
        offer_codes(game)
    data["event_battle"] = 0
    resolve_floor(game, floor)
    battle_state(game)["active"] = None
    opened = reveal_routes(game)
    response = {"t_nether_characters": hp_view(game), "drops": rewards, "first_rewards": [], "clear_rewards": [],
                "t_nether_map_floor": opened, "t_nether_map_floors": copy.deepcopy(data["floors"])}
    data["last_battle"] = {"digest": digest, "response": copy.deepcopy(response)}
    return response


@frozen_nether
def close_battle(game, body):
    current, data, floor = selected_floor(game, body)
    active = battle_state(game)["active"]
    expected = {key: body[key] for key in ("m_nether_id", "m_nether_map_id", "floor_level", "floor_index")}
    if active is not None and (active["kind"] != "nether" or active["context"] != expected):
        raise GameplayError("A different battle is active")
    battle_state(game)["active"] = None
    current["status"] = 7
    return {"t_nether_map_floors": copy.deepcopy(data["floors"]), "first_rewards": [], "result_rewards": []}


@frozen_nether
def on_battle_suspended(game, session):
    current = ensure(game)
    if session["context"]["m_nether_id"] != current["m_nether_id"]:
        raise GameplayError("Nether suspension does not match the expedition")
    current["status"] = 2
