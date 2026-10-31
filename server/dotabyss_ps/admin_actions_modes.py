from __future__ import annotations

from .admin_actions_common import changed, field, no_battle, spec
from .gameplay_catalog import GameplayError, number
from .gameplay_records import find, put, rows


def union_progress(game, args):
    from .gameplay_union import prepare_union, progress

    no_battle(game)
    target = game.catalog.get("m_union_request_stages", args["stage_id"])
    prepare_union(game)
    current = find(game.state, "t_union_requests", m_union_type_id=target["m_union_type_id"])
    reached = target["stage"] + int(args["completed"])
    current["stage"] = reached
    claims = progress(game)["first_claims"]
    for stage in game.catalog.rows("m_union_request_stages"):
        if stage["m_union_type_id"] == target["m_union_type_id"] and stage["stage"] < reached and stage["id"] not in claims:
            claims.append(stage["id"])
    return changed("已修改联合战进度，跳过的首通奖励不补发", timing="page")


def disaster_status(game, args):
    from .gameplay_disaster_state import level_definition, status_record

    no_battle(game)
    current = status_record(game)
    if "level" in args:
        level_definition(game, args["level"])
        current["disaster_level"] = args["level"]
        current["disaster_level_record_high"] = max(current["disaster_level_record_high"], args["level"])
    if "points" in args:
        current["disaster_point"] = args["points"]
    if "skippable_level" in args:
        if args["skippable_level"]:
            level_definition(game, args["skippable_level"])
        current["skippable_disaster_level"] = args["skippable_level"]
    return changed("已修改厄灾等级和积分", timing="page", warnings=["当前生成的首领槽位保留原有等级；新设置用于后续生成。"])


def defensive_unit(game, args):
    game.catalog.get("m_defensive_units", args["unit_id"])
    if not any(row["m_defensive_unit_id"] == args["unit_id"] and row["level"] == args["level"] for row in game.catalog.rows("m_defensive_unit_levels")):
        raise GameplayError("防御设施没有对应等级")
    put(game.state, "t_defensive_units", {"m_defensive_unit_id": args["unit_id"]}, {"level": args["level"]}, game.user_id)
    return changed("已修改防御设施等级", timing="next_battle", warnings=["研究已提供的更高等级会保持生效。"])


def nether_resources(game, args):
    from .gameplay_nether_state import ensure, run

    no_battle(game)
    run(game)
    current = ensure(game)
    for name in ("nether_gold", "treasure_key", "erosion_point", "code_reload"):
        if name in args:
            current[name] = args[name]
    return changed("已修改当前深渊局内资源", timing="page")


def nether_code(game, args):
    from .gameplay_nether_events import add_code, code_rows
    from .gameplay_nether_state import run

    no_battle(game)
    run(game)
    definition = game.catalog.get("m_nether_codes", args["code_id"])
    current = next((row for row in code_rows(game) if row["m_nether_code_id"] == definition["id"]), None)
    if args["owned"]:
        if current is None or not current["amount"]:
            add_code(game, definition["id"])
    elif current:
        current["amount"] = 0
    return changed("已修改当前远征代码", timing="page")


def miner_complete(game, args):
    from .gameplay_event_miner import miner_data, prepare_miner
    from .gameplay_events import require_event

    no_battle(game)
    event = require_event(game, args["event_id"], 1)
    prepare_miner(game, event)
    data = miner_data(game, event["id"])
    count = 0
    for definition in game.catalog.rows("m_event_miner_quests"):
        if definition["m_event_id"] != event["id"]:
            continue
        row = find(game.state, "t_event_miner_quests", m_event_miner_quest_id=definition["id"])
        row.update(status=99, clear_rank=args["rank"], clear_time=args["clear_time"], last_cleared_at=game.now.isoformat(), updated_at=game.now.isoformat())
        key = str(definition["id"])
        data["clears"][key] = max(1, data["clears"].get(key, 0))
        count += 1
    return changed("已完成采矿关卡，未发放首通奖励", count, timing="page")


def mutant_progress(game, args):
    from .gameplay_event_mutant import phases, prepare_mutant, quest_data
    from .gameplay_events import require_event

    no_battle(game)
    definition = game.catalog.get("m_event_disaster_quests", args["quest_id"])
    event = require_event(game, definition["m_event_id"], 2)
    prepare_mutant(game, event)
    hp, infinite = phases(game, definition)
    phase = number(args["phase"], 1, 1000000 if infinite else len(hp))
    remaining = number(args["hp"], 0, hp[(phase - 1) % len(hp)])
    if remaining == 0 and (infinite or phase != len(hp)):
        raise GameplayError("只可将有限首领的最后阶段 HP 设为 0；其他情况请推进阶段后设置剩余 HP")
    row = find(game.state, "t_event_disaster_quests", m_event_disaster_quest_id=definition["id"])
    data = quest_data(game, definition)
    data["version"] += 1
    row.update(phase_no=phase, remain_hp=remaining)
    if remaining == 0:
        row.update(is_cleared=1, status=99)
        data["clears"] = max(data["clears"], 1)
    elif row["status"] == 99:
        row["status"] = 2
    if args["reset_used_characters"]:
        row.update(used_character_list=[], challenge_count=0)
    prepare_mutant(game, event)
    return changed("已修改凶化厄灾阶段，不发放战斗奖励", timing="page")


def event_effect(game, args):
    from .gameplay_event_effects import effect_groups
    from .gameplay_events import require_event

    no_battle(game)
    event = require_event(game, args["event_id"])
    if args["group"] not in effect_groups(game, event):
        raise GameplayError("效果组不属于所选活动")
    levels = {row["level"] for row in game.catalog.rows("m_event_stage_effects") if row["group_no"] == args["group"]}
    if args["level"] not in levels | {0}:
        raise GameplayError("活动效果等级不存在")
    put(game.state, "t_event_stage_effect_levels", {"m_event_id": event["id"], "m_event_stage_effect_group_no": args["group"]}, {"level": args["level"]})
    return changed("已修改活动效果等级", timing="next_battle")


def box_reset(game, args):
    from .gameplay_event_box import box

    no_battle(game)
    if not args["allow_duplicate_rewards"]:
        raise GameplayError("重建箱池可能重复发奖，需要明确确认", 400, 4007)
    event, _, _, _, data = box(game, args["event_id"])
    data.update(current_box_no=args["number"], draws={})
    box(game, event["id"])
    return changed("已重建指定箱池", timing="page", warnings=["原有抽取奖励保留，新箱池可以重新抽取。"])


def board_select(game, args):
    from .gameplay_event_board import board, current_sheet

    no_battle(game)
    event, _, data = board(game, args["event_id"])
    if args["reset_sheet"] and not args["allow_duplicate_rewards"]:
        raise GameplayError("清空棋盘领取状态可能重复发奖，需要明确确认", 400, 4007)
    data["current_sheet"] = args["number"]
    if args["reset_sheet"]:
        data["sheets"].pop(str(args["number"]), None)
    current_sheet(game, event, data)
    return changed("已切换当前棋盘", timing="page", warnings=["已有棋盘保留开格记录；仅明确选择重置时允许重新领取。"])


OPERATIONS = [
    (spec("union.progress", "battle", "设置联合战进度", [field("stage_id", "目标关卡", min=1, source="m_union_request_stages"), field("completed", "目标关卡也标为完成", "boolean", default=False)], timing="page"), union_progress),
    (spec("disaster.status", "battle", "修改厄灾等级与积分", [field("level", "当前等级", min=1, required=False), field("points", "积分", required=False), field("skippable_level", "可扫荡最高等级", required=False)], timing="page"), disaster_status),
    (spec("disaster.unit", "battle", "设置防御设施", [field("unit_id", "防御设施", min=1, source="m_defensive_units"), field("level", "等级", min=1)], timing="next_battle"), defensive_unit),
    (spec("nether.resources", "battle", "修改深渊局内资源", [field(name, label, required=False) for name, label in (("nether_gold", "局内金币"), ("treasure_key", "宝箱钥匙"), ("erosion_point", "侵蚀值"), ("code_reload", "已重抽代码次数"))], timing="page"), nether_resources),
    (spec("nether.code", "battle", "获取或移除深渊代码", [field("code_id", "代码", min=1, source="m_nether_codes"), field("owned", "持有此代码", "boolean", default=True)], timing="page"), nether_code),
    (spec("event.miner_complete", "event", "完成采矿活动关卡", [field("event_id", "采矿活动", min=1, source="m_events"), field("rank", "评级（1 至 4）", min=1, max=4, default=4), field("clear_time", "通关毫秒数", min=1, max=86400000, default=60000)], timing="page"), miner_complete),
    (spec("event.mutant_progress", "event", "修改凶化厄灾阶段和 HP", [field("quest_id", "凶化厄灾关卡", min=1, source="m_event_disaster_quests"), field("phase", "阶段", min=1), field("hp", "剩余 HP"), field("reset_used_characters", "重置已出战角色和挑战次数", "boolean", default=False)], timing="page"), mutant_progress),
    (spec("event.effect", "event", "设置活动效果等级", [field("event_id", "活动", min=1, source="m_events"), field("group", "效果组", min=1), field("level", "等级")], timing="next_battle"), event_effect),
    (spec("event.box_reset", "event", "重建指定活动箱池", [field("event_id", "箱池活动", min=1, source="m_events"), field("number", "箱编号", min=1, max=1000000), field("allow_duplicate_rewards", "允许重新抽取奖励", "boolean", default=False)], timing="page"), box_reset),
    (spec("event.board_select", "event", "切换或重置寻宝棋盘", [field("event_id", "寻宝活动", min=1, source="m_events"), field("number", "棋盘编号", min=1, max=1000000), field("reset_sheet", "清空此棋盘领取记录", "boolean", default=False), field("allow_duplicate_rewards", "允许重新领取奖励", "boolean", default=False)], timing="page"), board_select),
]
