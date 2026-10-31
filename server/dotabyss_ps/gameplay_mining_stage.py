from __future__ import annotations

from .gameplay_catalog import GameplayError


def next_group(group, outcome):
    return int("".join(sorted((str(group) if group else "") + str(outcome))))


def build_mining(builder, mine_stage_id):
    mine = builder.catalog.get("m_mine_stages", mine_stage_id)
    builder.catalog.get("m_dungeons", mine["m_dungeon_id"])
    parts = sorted(builder.group("m_dungeon_floor_parts", "m_dungeon_id", mine["m_dungeon_id"]), key=lambda row: row["id"])
    groups = {row["fork_group_no"] for row in parts}
    floors = []
    for part in parts:
        role = part["role"]
        floor = builder.floor_part({**part, "role": 100 if role in (101, 305, 900) else role}, 0)
        floor["role"] = role
        if role == 305:
            success, failure = next_group(part["fork_group_no"], 1), next_group(part["fork_group_no"], 2)
            if success not in groups or failure not in groups:
                raise GameplayError("Mining branch targets are missing", 503, 5036)
            floor["role_option"]["mining_battle"] = {
                "time_limit": builder.game.rules.get("event_miner_battle_seconds", 60), "success_to": success, "failure_to": failure}
        floors.append(floor)
    return {"floor_parts": floors, "enemies": builder.enemies, "resources": builder.resources, "drops": [],
            "mining": {"trolley": {"hp": mine["trolley_hp"], "defense": mine["trolley_defense"],
                                   "loading_max": mine["trolley_loading_max"], "asset_id": mine["trolley_asset_id"]},
                       "clear_ranks": [{"rank": rank, "require_success": mine[f"rank_{name}_success_count"]}
                                       for rank, name in ((4, "s"), (3, "a"), (2, "b"), (1, "c"))]}}


def validate_mining_path(stage, result):
    floors = {row["sid"]: row for row in stage["floor_parts"]}
    passed = set(result["passed_floor_parts"])
    if not passed <= floors.keys():
        raise GameplayError("Mining result contains a foreign floor", 400, 4007)
    groups = {floors[sid]["fork_group_no"] for sid in passed}
    current, visited, wins = 0, set(), 0
    while current not in visited:
        visited.add(current)
        selected = [row for row in floors.values() if row["fork_group_no"] == current]
        battle = next((row for row in selected if row["role"] == 305), None)
        if battle is None:
            if not any(row["role"] == 102 and row["sid"] in passed for row in selected):
                raise GameplayError("Mining result has not reached its exit", 400, 4007)
            break
        if battle["sid"] not in passed:
            raise GameplayError("Mining result skipped a required battle area", 400, 4007)
        option = battle["role_option"]["mining_battle"]
        reached = [value for value in (option["success_to"], option["failure_to"]) if value in groups]
        if len(reached) != 1:
            raise GameplayError("Mining result must choose one outcome per battle", 400, 4007)
        targets = {identifier for group in battle["enemy_groups"] for identifier in group["enemies"]}
        required = {row["sid"] for row in stage["enemies"] if row["sid"] in targets and row["is_target"]}
        if reached[0] == option["success_to"] and required - set(result["destroyed_enemies"]):
            raise GameplayError("Mining success requires defeating its target enemy", 400, 4007)
        wins += reached[0] == option["success_to"]
        current = reached[0]
    if groups != visited:
        raise GameplayError("Mining result includes a mutually exclusive branch", 400, 4007)
    rank = max((row["rank"] for row in stage["mining"]["clear_ranks"] if wins >= row["require_success"]), default=0)
    if result["clear_rank"] != rank:
        raise GameplayError("Mining clear rank does not match the chosen route", 400, 4007)


def skipped_mining_result(stage, rank):
    terminal = {row["fork_group_no"] for row in stage["floor_parts"] if row["role"] == 102}
    target = next((value for value in sorted(terminal) if max(entry["rank"] for entry in stage["mining"]["clear_ranks"]
                   if str(value).count("1") >= entry["require_success"]) == rank), None)
    if target is None:
        raise GameplayError("Stored mining rank has no matching route")
    group, groups = 0, {0}
    for digit in str(target):
        group = next_group(group, int(digit))
        groups.add(group)
    floors = [row for row in stage["floor_parts"] if row["fork_group_no"] in groups]
    enemies = {sid for row in floors for entry in row["enemy_groups"] for sid in entry["enemies"]}
    for floor in floors:
        option = floor["role_option"].get("mining_battle")
        if option and option["failure_to"] in groups:
            identifiers = {sid for entry in floor["enemy_groups"] for sid in entry["enemies"]}
            enemies -= {row["sid"] for row in stage["enemies"] if row["sid"] in identifiers and row["is_target"]}
    resources = {sid for row in floors for sid in row["resources"]}
    drops = {sid for row in stage["enemies"] + stage["resources"] if row["sid"] in enemies | resources for sid in row["drops"]}
    return {"play_time": 0, "destroyed_enemies": sorted(enemies), "drop_items": sorted(drops), "mana_gem_count": 0,
            "top_fc_damage": 0, "total_damage": 0, "passed_floor_parts": [row["sid"] for row in floors], "clear_rank": rank}
