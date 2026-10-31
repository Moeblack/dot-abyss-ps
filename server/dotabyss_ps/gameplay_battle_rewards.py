from __future__ import annotations

import copy
import random
from collections import Counter

from .gameplay_catalog import GameplayError, identifiers, number
from .gameplay_battle_modes import battle_mode
from .gameplay_content import EQUIPMENT
from .gameplay_delivery import deliver_for_display as grant_display_rewards
from .gameplay_effects import extra_drop_bonus
from .gameplay_drop_bonuses import party_bonus, save_bonus
from .gameplay_receipts import feature_data, operation_digest
from .gameplay_records import rows
from .gameplay_statistics import record

RESULT_FIELDS = {"play_time", "destroyed_enemies", "drop_items", "mana_gem_count", "top_fc_damage"}
DUNGEON_RESULT_FIELDS = {"total_damage", "passed_floor_parts", "clear_rank"}


def stage_entities(stage):
    enemies = list(stage.get("enemies", []))
    if stage.get("boss_enemy") is not None:
        enemies.append(stage["boss_enemy"])
    resources = list(stage.get("resources", []))
    enemy_index, resource_index, floors = {}, {}, {}
    for source, index in ((enemies, enemy_index), (resources, resource_index), (stage.get("floor_parts", []), floors)):
        for entity in source:
            sid = number(entity["sid"])
            if sid in index:
                raise GameplayError("Stored battle contains duplicate entity identifiers")
            index[sid] = entity
    associations = {}
    for sid, floor in floors.items():
        enemy_ids = [value for group in floor["enemy_groups"] for value in group["enemies"]]
        for entity_id in enemy_ids:
            if entity_id not in enemy_index or ("enemy", entity_id) in associations:
                raise GameplayError("Stored battle contains invalid enemy references")
            associations["enemy", entity_id] = sid
        for entity_id in floor["resources"]:
            if entity_id not in resource_index or ("resource", entity_id) in associations:
                raise GameplayError("Stored battle contains invalid resource references")
            associations["resource", entity_id] = sid
    return enemy_index, resource_index, floors, associations


def attach_drops(game, battle_id, stage, seed, *, kind="main", party_no=0, ability_bonus=None):
    enemies, resources, floors, associations = stage_entities(stage)
    if stage.get("drops") or any(entity.get("drops") for entity in [*enemies.values(), *resources.values()]):
        raise GameplayError("Battle drops have already been attached")
    stage["drops"] = []
    mode = battle_mode(kind)
    if not mode.drop_table:
        return {}
    reference = game.catalog.get(mode.master, battle_id)[mode.reference_field]
    choices = [row for row in game.catalog.rows(mode.drop_table) if row[mode.drop_key] == reference]
    unique = {(r["content_type"], r["content_id"], r["is_rare_drop"]): r for r in choices}
    choices = [unique[key] for key in sorted(unique)]
    common = [row for row in choices if not row["is_rare_drop"]]
    rare = [row for row in choices if row["is_rare_drop"]]
    rng = random.Random("battle-drops:" + str(seed))
    probability = number(game.rules["battle_drop_probability_per_mille"], 0, 1000)
    rare_probability = number(game.rules["battle_rare_drop_probability_per_mille"], 0, 1000)
    extra_probability = extra_drop_probability(game, battle_id, kind=kind, party_no=party_no, ability_bonus=ability_bonus)
    next_sid = max([0, *enemies, *resources, *floors,
                    *(row["sid"] for row in stage.get("defensive_units", []))]) + 1
    reward_index = {}
    for source_type, index in (("enemy", enemies), ("resource", resources)):
        for sid, source in index.items():
            source["drops"] = []
            if not choices or rng.randrange(1000) >= probability:
                continue
            pool = rare if rare and rng.randrange(1000) < rare_probability else common or rare
            selected = rng.choice(pool)
            content_type, content_id = selected["content_type"], selected["content_id"]
            rarity = int(bool(selected["is_rare_drop"])) + 1
            if content_type in EQUIPMENT:
                rarity = game.catalog.get("m_" + EQUIPMENT[content_type][1], content_id)["rarity"]
            copies = 1 + int(extra_probability > 0 and rng.randrange(1000) < extra_probability)
            for bonus_type in range(copies):
                drop = {"sid": number(next_sid), "content_type": content_type, "content_id": content_id,
                        "amount": 1, "rarity_level": rarity, "is_rare_drop": int(bool(selected["is_rare_drop"])), "m_drop_id": 0}
                stage["drops"].append(drop)
                source["drops"].append(next_sid)
                reward_index[str(next_sid)] = {**drop, "source_type": source_type, "source_sid": sid,
                                               "floor_part_id": associations.get((source_type, sid), 0),
                                               "bonus_type": bonus_type, "extra_probability": extra_probability}
                next_sid += 1
    for floor_id, floor in floors.items():
        treasure = floor.get("role_option", {}).get("treasure_battle")
        if treasure is None:
            continue
        for rank in treasure["ranks"]:
            rank["drops"] = []
            key = {1: "b", 2: "a", 3: "s"}.get(rank["rank"])
            if key is None:
                raise GameplayError("Unknown treasure reward rank", 503, 5036)
            count = number(game.rules[f"battle_treasure_{key}_drops"], 0, 100)
            for _ in range(count if choices else 0):
                pool = rare if rare and rng.randrange(1000) < rare_probability else common or rare
                selected = rng.choice(pool)
                kind, content_id = selected["content_type"], selected["content_id"]
                rarity = int(bool(selected["is_rare_drop"])) + 1
                if kind in EQUIPMENT:
                    rarity = game.catalog.get("m_" + EQUIPMENT[kind][1], content_id)["rarity"]
                drop = {"sid": number(next_sid), "content_type": kind, "content_id": content_id,
                        "amount": 1, "rarity_level": rarity, "is_rare_drop": int(bool(selected["is_rare_drop"])), "m_drop_id": 0}
                stage["drops"].append(drop)
                rank["drops"].append(next_sid)
                reward_index[str(next_sid)] = {**drop, "source_type": "treasure", "source_sid": floor_id,
                                               "floor_part_id": floor_id, "treasure_rank": rank["rank"],
                                               "bonus_type": 0, "extra_probability": 0}
                next_sid += 1
    return reward_index


def extra_drop_probability(game, battle_id, *, kind="main", party_no=0, ability_bonus=None):
    return min(1000, stored_drop_probability(game, battle_id, kind=kind, party_no=party_no,
                                           ability_bonus=ability_bonus) + extra_drop_bonus(game))


def stored_drop_probability(game, battle_id, *, kind="main", party_no=0, ability_bonus=None):
    definition = game.catalog.get(battle_mode(kind).master, battle_id)
    base = number(definition.get("addition_extra_drop_probability", 0), 0, 1000)
    ability_bonus = party_bonus(game, party_no) if ability_bonus is None else number(ability_bonus, 0, 1000)
    return min(1000, base + ability_bonus)


def validate_stage_results(stage, result, stage_type, *, time_limit=86400, result_schema=None):
    if result_schema not in (None, "exploration", "disaster"):
        raise GameplayError("Unsupported battle result schema", 400, 4007)
    exploration_fields = result_schema == "exploration" or result_schema is None and stage_type in (1, 3)
    expected = RESULT_FIELDS | (DUNGEON_RESULT_FIELDS if exploration_fields else set())
    if stage_type not in (1, 2, 3) or not isinstance(result, dict) or set(result) != expected:
        raise GameplayError("Battle result fields do not match the stage type", 400, 4007)
    normalized = {field: number(result[field], 0, 2 ** 31 - 1) for field in expected - {
        "destroyed_enemies", "drop_items", "passed_floor_parts"}}
    time_limit = number(time_limit, 0, 86400)
    if normalized["play_time"] > (time_limit + 10 if time_limit else 86400):
        raise GameplayError("Battle result exceeds the stage time limit", 400, 4007)
    normalized["destroyed_enemies"] = identifiers(result["destroyed_enemies"], 10000)
    normalized["drop_items"] = identifiers(result["drop_items"], 10000)
    enemies, resources, floors, associations = stage_entities(stage)
    killed = set(normalized["destroyed_enemies"])
    if killed - enemies.keys():
        raise GameplayError("Battle result references enemies outside this session", 400, 4007)
    drops = {row["sid"]: row for row in stage.get("drops", [])}
    if set(normalized["drop_items"]) - drops.keys():
        raise GameplayError("Battle result references drops outside this session", 400, 4007)
    passed = set()
    if exploration_fields:
        floor_parts = result["passed_floor_parts"]
        if floor_parts is None and stage_type == 2:
            floor_parts = []
        normalized["passed_floor_parts"] = identifiers(floor_parts, 10000)
        if normalized["clear_rank"] > 10:
            raise GameplayError("Battle clear rank is invalid", 400, 4007)
    if stage_type in (1, 3):
        if not exploration_fields:
            raise GameplayError("Dungeon result requires visited floor parts", 400, 4007)
        passed = set(normalized["passed_floor_parts"])
        if not passed or passed - floors.keys():
            raise GameplayError("Battle result references invalid floor parts", 400, 4007)
        for sid, floor in floors.items():
            targets = [enemy for group in floor["enemy_groups"] for enemy in group["enemies"]]
            if targets and not floor["fork_group_no"] and sid not in passed:
                raise GameplayError("Required battle floor has not been completed")
            if sid not in passed or stage_type == 3 and floor["role"] == 305:
                continue
            required = {enemy for enemy in targets if enemies[enemy]["is_target"]}
            if required - killed:
                raise GameplayError("Required battle enemies have not been defeated")
            count = floor["destroy_enemy_max"]
            if count and len(killed.intersection(targets)) < min(count, len(targets)):
                raise GameplayError("Required enemy defeat count has not been reached")
            if targets and not required and not count and set(targets) - killed:
                raise GameplayError("Battle floor still contains undefeated enemies")
        if any(associations.get(("enemy", sid), 0) not in passed for sid in killed):
            raise GameplayError("Defeated enemy belongs to an unvisited floor", 400, 4007)
    else:
        if exploration_fields and normalized["passed_floor_parts"]:
            raise GameplayError("Defence battle has no dungeon floor parts", 400, 4007)
        boss = stage.get("boss_enemy")
        if boss is None or boss["sid"] not in killed:
            raise GameplayError("Battle boss has not been defeated")
    allowed_drops = {drop for sid in killed for drop in enemies[sid]["drops"]}
    allowed_drops.update(drop for sid, source in resources.items()
                         if not floors or associations.get(("resource", sid), 0) in passed for drop in source["drops"])
    treasure_floors = {sid: floor["role_option"]["treasure_battle"] for sid, floor in floors.items()
                       if floor.get("role_option", {}).get("treasure_battle") is not None}
    if treasure_floors:
        chosen = normalized.get("clear_rank", 0)
        visited_treasures = passed.intersection(treasure_floors)
        if visited_treasures and chosen not in (1, 2, 3) or not visited_treasures and chosen != 0:
            raise GameplayError("Treasure battle requires one completed reward rank", 400, 4007)
        for sid, treasure in treasure_floors.items():
            if sid not in passed:
                continue
            ranks = [rank for rank in treasure["ranks"] if rank["rank"] == chosen]
            if len(ranks) != 1 or set(ranks[0]["drops"]) - drops.keys():
                raise GameplayError("Stored treasure reward references are invalid")
            allowed_drops.update(ranks[0]["drops"])
    if set(normalized["drop_items"]) - allowed_drops:
        raise GameplayError("Battle drop source has not been completed", 400, 4007)
    if stage_type == 3:
        from .gameplay_mining_stage import validate_mining_path

        validate_mining_path(stage, normalized)
    return normalized


def reward_state(game):
    data = feature_data(game, "battle_rewards")
    if not data:
        previous = feature_data(game, "main_story")
        data.update(first_claims={str(key): {"legacy": True} for key in previous.get("legacy_cleared_battles", [])},
                    quest_claims={str(key): {"legacy": True} for key in previous.get("legacy_completed_quests", [])})
    if set(data) != {"first_claims", "quest_claims"} or any(not isinstance(value, dict) for value in data.values()):
        raise GameplayError("Stored main-story reward receipts are invalid")
    return data


def first_rewards(game, session):
    if session["kind"] != "main":
        return []
    data = reward_state(game)
    key = f"{session['m_chapter_quest_id']}:{session['sequence']}"
    if key in data["first_claims"]:
        return []
    quest = game.catalog.get("m_chapter_quests", session["m_chapter_quest_id"])
    area = game.catalog.get("m_chapter_areas", quest["m_chapter_area_id"])
    mode = number(game.rules["battle_first_reward_group_mode"], 0, 2)
    group = area["m_chapter_quest_battle_reward_group_no"] + (quest["progress_order"] - 1 if mode == 2 else 0)
    rewards = [row for row in game.catalog.rows("m_chapter_quest_battle_rewards") if row["group_no"] == group] if mode else []
    if mode and not rewards:
        raise GameplayError("Main-story first-clear reward group is missing", 503, 5036)
    displayed = grant_display_rewards(game, rewards)
    data["first_claims"][key] = {"at": game.now.isoformat(), "mode": mode, "group_no": group if mode else 0, "rewards": displayed}
    return displayed


def settle_battle(game, session, stage_results):
    digest = operation_digest("battle-result", "POST", stage_results)
    previous = session.get("settlement")
    if previous is not None:
        if previous["digest"] != digest:
            raise GameplayError("Battle session was already settled with a different result")
        return copy.deepcopy(previous["response"])
    stage = session["stage_detail"]
    mode = battle_mode(session["kind"])
    definition = game.catalog.get(mode.master, session["battle_id"]) if "time_limit" not in session else {}
    result = validate_stage_results(stage, stage_results, session["stage_type"],
                                    time_limit=session.get("time_limit", definition.get("time_limit", 86400)),
                                    result_schema=session.get("result_schema", mode.result_schema))
    rewards = []
    for sid in result["drop_items"]:
        reward = session["reward_index"].get(str(sid))
        if reward is None:
            raise GameplayError("Stored battle reward is missing")
        rewards.append(reward)
    save_bonus(game, session)
    enemies, _, _, _ = stage_entities(stage)
    experience = sum(number(enemies[sid]["drop_exp"], 0, 2 ** 31 - 1) for sid in result["destroyed_enemies"]) if mode.grant_experience else 0
    number(experience, 0, 2 ** 31 - 1)
    status = rows(game.state, "t_statuses")[0]
    before_exp = status["character_exp"]
    before_gold = status["gold"]
    drops = grant_display_rewards(game, rewards)
    if experience:
        grant_display_rewards(game, [{"content_type": 110, "content_id": 0, "amount": experience}])
    player_exp = number(session["stamina_spent"] * game.rules["battle_player_exp_per_stamina"], 0, 2 ** 31 - 1) if mode.grant_experience else 0
    if player_exp:
        grant_display_rewards(game, [{"content_type": 111, "content_id": 0, "amount": player_exp}])
    first = first_rewards(game, session)
    if session["kind"] not in ("tutorial", "coaching", "recollection", "tryout", "nether"):
        record(game.progress, game.now, 140000, len(result["destroyed_enemies"]))
        jobs = Counter(game.catalog.get("m_enemies", enemies[sid]["m_enemy_id"])["m_job_id"]
                       for sid in result["destroyed_enemies"])
        for job_id, count in jobs.items():
            record(game.progress, game.now, 140001, count, job_id)
    if session["kind"] == "repeat":
        record(game.progress, game.now, 20000)
    from .gameplay_event_battle_link import battle_event_rewards

    event_drops = battle_event_rewards(game, session)
    response = {"last_party_no": session["party_no"], "battle_result": {
        "drops": drops, "event_drops": event_drops, "first_rewards": first, "clear_rewards": None,
        "grant_character_exp": status["character_exp"] - before_exp, "before_character_exp": before_exp,
        "after_character_exp": status["character_exp"], "grant_gold": status["gold"] - before_gold},
        "receive_content_list": None, "release_data": None}
    session["settlement"] = {"digest": digest, "response": copy.deepcopy(response)}
    return response


def complete_quest(game, quest):
    data = reward_state(game)
    key = str(quest["id"])
    previous = data["quest_claims"].get(key)
    if previous is not None:
        return copy.deepcopy(previous.get("receive_content_list", []))
    rewards = [row for row in game.catalog.rows("m_chapter_quest_rewards") if row["m_chapter_quest_id"] == quest["id"]]
    rewarded_characters = {row["content_id"] for row in rewards if row["content_type"] == 40}
    rewards += [{"content_type": 40, "content_id": row["m_character_id"], "amount": 1}
                for row in game.catalog.rows("m_chapter_characters")
                if row["m_chapter_quest_id"] == quest["id"] and row["m_character_id"] not in rewarded_characters]
    received = grant_display_rewards(game, rewards)
    data["quest_claims"][key] = {"at": game.now.isoformat(), "receive_content_list": copy.deepcopy(received)}
    return received
