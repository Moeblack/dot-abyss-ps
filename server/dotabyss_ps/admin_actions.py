from __future__ import annotations

import copy

from .admin_actions_common import arguments, changed, field, no_battle, spec
from .admin_actions_economy import OPERATIONS as ECONOMY
from .admin_actions_growth import OPERATIONS as GROWTH
from .admin_actions_modes import OPERATIONS as MODES
from .admin_actions_progress import OPERATIONS as PROGRESS
from .gameplay_catalog import GameplayError

ROUTE_ACTIONS = [
    ("profile.name", "profile", "修改玩家名字", "api/profile/edit/name"),
    ("profile.comment", "profile", "修改个人签名", "api/profile/edit/comment"),
    ("profile.degree", "profile", "设置展示称号", "api/profile/edit/degree"),
    ("profile.character", "profile", "设置主页角色", "api/profile/edit/favorite-character"),
    ("appearance.character", "appearance", "更换角色服装", "api/character/skin/set"),
    ("appearance.weapon", "appearance", "更换武器外观", "api/character/weapon_skin/set"),
    ("character.gift", "character", "按赠礼规则增加好感", "api/character/gift/give"),
    ("character.love_gift", "character", "按赠礼规则增加爱心", "api/character/gift"),
    ("character.bond_reset", "character", "按规则重置羁绊分配", "api/character/bond/reset"),
    ("character.resistance_reset", "character", "按规则重置抗性分配", "api/character/condition_resistance/reset"),
    ("equipment.appraisal_roll", "equipment", "消耗材料重新鉴定", "api/equipment/appraisal/lottery"),
    ("equipment.appraisal_apply", "equipment", "采用本次鉴定结果", "api/equipment/appraisal/execute"),
    ("equipment.disassemble", "equipment", "按规则分解装备", "api/equipment/disassembly/execute"),
    ("party.profile", "party", "修改编队名称和图标", "api/party/exploration/edit/profile"),
    ("party.copy", "party", "复制编队", "api/party/exploration/copy"),
    ("party.layout", "party", "编辑编队成员与魔晶石", "api/party/exploration/edit"),
    ("party.preset_save", "party", "保存编队预设", "api/party/push/preset"),
    ("party.preset_load", "party", "应用编队预设", "api/party/pop/preset"),
    ("party.preset_name", "party", "修改预设名称", "api/party/edit/name"),
    ("party.delete", "party", "删除编队或预设", "api/party/delete"),
    ("inventory.use", "inventory", "使用背包自选道具", "api/item/use/selectable"),
    ("gacha.execute", "gacha", "按当前卡池抽卡", "api/gacha/exec"),
    ("gacha.wishlist", "gacha", "设置卡池愿望单", "api/gacha/wishlist/update"),
    ("exchange.execute", "shop", "按规则兑换商品", "api/exchange/execute"),
    ("shop.purchase", "shop", "本地模拟购买", "api/payment/grant/free"),
    ("shop.pass_rewards", "shop", "领取通行证等级奖励", "api/mission-pass/level/reward/receive"),
    ("shop.pass_loop", "shop", "领取通行证循环奖励", "api/mission-pass/loop-reward/receive"),
    ("mail.receive", "mission", "领取邮件奖励", "api/present/receive"),
    ("mission.receive", "mission", "领取普通任务奖励", "api/mission/reward/receive"),
    ("mission.plan_receive", "mission", "领取开发计划任务奖励", "api/plan-mission/reward/receive"),
    ("mission.special_receive", "mission", "领取新手任务奖励", "api/special-mission/mission-reward/receive"),
    ("mission.special_complete", "mission", "领取新手任务总奖励", "api/special-mission/complete-reward/receive"),
    ("mission.nether_receive", "mission", "领取深渊任务奖励", "api/nether-mission/reward/receive"),
    ("mission.event_receive", "mission", "领取活动任务奖励", "api/event-mission/reward/receive"),
    ("mission.pass_receive", "mission", "领取通行证任务奖励", "api/mission-pass/mission/reward/receive"),
    ("tavern.name", "tavern", "修改酒馆名字", "api/tavern/update/name"),
    ("tavern.staff", "tavern", "安排酒馆员工", "api/tavern/set/character"),
    ("tavern.start", "tavern", "开始酒馆轮次", "api/tavern/exec/work"),
    ("tavern.card", "tavern", "选择酒馆卡片", "api/tavern/select/card"),
    ("tavern.character_card", "tavern", "选择员工卡片", "api/tavern/select/character-card"),
    ("tavern.skip", "tavern", "按规则跳过酒馆轮次", "api/tavern/skip/work"),
    ("idle.party", "idle", "编辑探索队伍", "api/party/idle-exploration/edit"),
    ("idle.profile", "idle", "修改探索队名称和图标", "api/party/idle-exploration/edit/profile"),
    ("idle.assign", "idle", "分配探索区域", "api/party/idle-exploration/assign"),
    ("idle.receive", "idle", "结算并领取探索收益", "api/idle-exploration/receive"),
    ("idle.boost", "idle", "按规则加速探索", "api/idle-exploration/boost"),
    ("idle.campaign", "event", "选择遭遇活动", "api/idle-exploration/campaign/join"),
    ("battle.disaster_refresh", "battle", "刷新厄灾状态", "api/disaster/refresh"),
    ("battle.disaster_summon", "battle", "按规则召唤厄灾", "api/disaster/summon"),
    ("battle.disaster_patrol", "battle", "设置厄灾巡逻等级", "api/disaster/patrol"),
    ("battle.disaster_skip", "battle", "按规则扫荡厄灾", "api/disaster/skip-quest"),
    ("battle.nether_upgrade", "battle", "按规则升级深渊球盘", "api/nether/upgrade-sphere"),
    ("battle.nether_code", "battle", "选择当前深渊代码", "api/nether/fix-code"),
    ("battle.nether_cancel", "battle", "结束当前深渊远征", "api/nether/cancel"),
    ("battle.nether_result", "battle", "结算深渊远征奖励", "api/nether/result"),
    ("story.read", "story", "阅读已解锁剧情并领取奖励", "api/novel/read"),
    ("story.side_read", "story", "完成当前外传剧情节点", "api/side-story/clear-story"),
    ("event.board_open", "event", "打开寻宝棋盘格", "api/event/treasure-board/open"),
    ("event.board_next", "event", "推进寻宝棋盘", "api/event/treasure-board/clear"),
    ("event.box_draw", "event", "抽取活动箱池", "api/event/box-gacha/exec"),
    ("event.box_next", "event", "推进活动箱池", "api/event/box-gacha/renew-box"),
    ("event.effect_toggle", "event", "开关活动效果", "api/event/stage-effect/toggle"),
    ("event.effect_upgrade", "event", "按规则升级活动效果", "api/event/stage-effect/upgrade"),
    ("event.miner_recover", "event", "按规则恢复采矿次数", "api/event/miner/recovery"),
    ("event.mutant_reset", "event", "重开凶化厄灾关卡", "api/event/disaster/reset-quest"),
    ("event.mutant_recover", "event", "按规则恢复凶化厄灾次数", "api/event/disaster/recovery"),
    ("event.bonus_giveup", "event", "放弃当前奖励关", "api/event/bonus-quest/giveup"),
]

LABELS = {
    "name": "名称", "comment": "个人签名", "is_sync_dmm": "同步标记（本地）", "t_character_id": "拥有的角色",
    "m_character_id": "角色", "m_character_skin_id": "角色服装", "m_weapon_skin_id": "武器外观", "m_degree_id": "称号",
    "t_equipment_id": "拥有的装备 ID", "content_type": "内容类型", "t_weapon_ids": "武器 ID 列表", "t_armor_ids": "防具 ID 列表", "t_accessory_ids": "饰品 ID 列表",
    "m_gacha_id": "卡池", "m_gacha_group_id": "卡池分组", "m_mission_ids": "任务列表", "m_payment_id": "本地商品", "m_mission_pass_id": "通行证",
    "party_no": "编队编号", "original_party_no": "来源编队编号", "replica_party_no": "目标编队编号", "preset_no": "预设编号",
    "t_recent_exploration_party_id": "编队", "t_preset_exploration_party_id": "预设", "t_exploration_party_id": "编队或预设 ID", "party_type": "编队类型（1 普通、2 预设）",
    "m_event_id": "活动", "m_encounter_campaign_id": "遭遇活动", "m_plan_id": "开发计划", "m_special_mission_id": "新手任务组",
    "m_tavern_work_id": "酒馆岗位", "position": "员工位置", "t_tavern_daily_card_id": "酒馆今日卡组", "m_tavern_card_id": "酒馆卡片",
    "exec_count": "执行次数", "skip_count": "扫荡次数", "exchange_count": "兑换数量", "recovery_count": "恢复次数", "use_ticket": "是否使用票券（0/1）",
    "use_gacha_ticket": "是否使用抽卡券（0/1）", "m_exchange_lineup_id": "兑换商品", "ids": "邮件 ID 列表", "levels": "领取等级设置",
    "use_items": "使用道具", "wishlist": "愿望单角色列表", "characters": "成员设置", "mana_gems": "魔晶石设置", "assign_to": "探索区域分配",
    "t_preset_exploration_party": "预设设置", "boosts": "加速设置", "space_numbers": "棋盘格编号（从 0 开始）", "value": "设置值",
    "m_event_stage_effect_group_no": "活动效果组", "category_group": "活动任务分类组", "m_disaster_boss_id": "厄灾首领", "disaster_level": "厄灾等级", "slot": "厄灾槽位",
    "m_nether_id": "深渊赛季", "m_nether_map_id": "深渊地图", "m_nether_sphere_board_id": "球盘", "m_nether_sphere_id": "球盘节点", "current_level": "当前等级",
    "m_nether_code_id": "深渊代码", "remove_m_nether_code_id": "替换代码 ID（0 为不替换）", "use_item_flag": "使用保留物品标记（0/1）",
    "novel_type": "剧情类型", "m_novel_id": "剧情 ID", "m_side_story_stage_id": "外传节点", "m_event_disaster_quest_id": "凶化厄灾关卡",
}
JSON_FIELDS = {"levels", "use_items", "characters", "mana_gems", "t_preset_exploration_party", "boosts", "space_numbers", "selects"}
DEFAULTS = {"is_sync_dmm": 0, "icon_1": 0, "icon_2": 0, "icon_3": 0, "exec_count": 1, "skip_count": 1,
            "exchange_count": 1, "recovery_count": 1, "use_ticket": 0, "use_gacha_ticket": 0, "remove_m_nether_code_id": 0,
            "use_item_flag": 0, "party_no": 1, "position": 1, "mana_gems": []}
TEMPLATES = {
    "characters": [{"position_no": 1, "t_character_id": 1, "t_weapon_id": 0, "t_armor_id": 0, "t_accessory_id": 0}],
    "use_items": [{"m_item_id": 140001, "amount": 1}],
    "levels": [{"level": 1, "receive_normal": 1, "receive_premium": 0}],
    "space_numbers": [0],
    "t_preset_exploration_party": {"preset_no": 1, "party_no": 1, "name": "预设", "icon": 0},
}


def route_field(name, catalog=None):
    kind = "json" if name in JSON_FIELDS else "ids" if name.endswith("_ids") or name in ("ids", "wishlist") else "string" if name in ("name", "comment") else "integer"
    options = {}
    if name in DEFAULTS:
        options["default"] = DEFAULTS[name]
    elif name in TEMPLATES:
        options["default"] = copy.deepcopy(TEMPLATES[name])
    if kind == "string":
        options.update(max_length=300, allow_empty=name == "comment", multiline=name == "comment")
    if name.endswith("_id") and name.startswith(("m_", "t_")):
        table = name[:-3] + "s"
        if catalog is None or table.startswith("t_") or table in catalog.tables:
            options["source"] = table
    return field(name, LABELS.get(name, name), kind, **options)


def route_specs(catalog=None):
    from .gameplay import EXTRA_ROUTES

    extra_fields = {"api/party/exploration/edit": {"party_no", "characters", "mana_gems"},
                    "api/mission/reward/receive": {"m_mission_ids"},
                    "api/special-mission/mission-reward/receive": {"m_special_mission_id", "m_mission_ids"}}
    result = []
    for identifier, group, label, route in ROUTE_ACTIONS:
        entry = EXTRA_ROUTES.get(route)
        fields = entry.fields if entry else extra_fields.get(route)
        if fields is None:
            continue
        definition = spec(identifier, group, label, [route_field(name, catalog) for name in sorted(fields)],
                          description="按现有游戏规则执行；消耗、前置条件和奖励记录正常生效。", timing="page")
        definition["route"] = route
        if identifier == "idle.party":
            next(item for item in definition["fields"] if item["name"] == "characters")["default"] = [{"position_no": 1, "t_character_id": 1, "t_weapon_id": 0}]
        if identifier == "character.love_gift":
            next(item for item in definition["fields"] if item["name"] == "use_items")["default"] = [{"t_item_id": 1, "amount": 1}]
        if identifier == "battle.disaster_summon":
            next(item for item in definition["fields"] if item["name"] == "use_items")["default"] = []
        result.append(definition)
    return result


def action_specs(catalog=None):
    values = copy.deepcopy([definition for definition, _ in (*GROWTH, *ECONOMY, *PROGRESS, *MODES)] + route_specs(catalog))
    sources = {
        "skin.unlock": {"ids": {"character": "m_character_skins", "weapon": "m_weapon_skins"}},
        "novel.unlock": {"ids": {str(i): "m_novel_" + name for i, name in ((1, "mains"), (2, "events"), (3, "characters"), (4, "homes"), (5, "character_skins"), (6, "prologues"), (7, "others"))}},
        "training.complete": {"ids": {name: f"m_{name}_quests" for name in ("coaching", "recollection", "tryout")}},
        "inventory.balance": {"master_id": {"30": "m_items", "54": "m_event_currencies", "56": "m_event_boss_tickets", "170": "m_gacha_tokens"}},
        "shop.reset_counter": {"master_id": {"shop": "m_payments", "exchange": "m_exchange_lineups"}},
    }
    for identifier in ("equipment.create", "equipment.edit", "equipment.delete", "equipment.enchantment"):
        names = {"70": ("weapon", "weapons"), "80": ("armor", "armors"), "90": ("accessory", "accessories")}
        key = "master_id" if identifier == "equipment.create" else "enchantment_id" if identifier == "equipment.enchantment" else "equipment_id"
        sources[identifier] = {key: {kind: f"m_{plural}" if key == "master_id" else f"t_{singular}_enchantments" if key == "enchantment_id" else f"t_{plural}" for kind, (singular, plural) in names.items()}}
    for definition in values:
        for item in definition["fields"]:
            mapping = sources.get(definition["id"], {}).get(item["name"])
            if mapping:
                item["source_by"] = {"field": "kind", "values": mapping}
    return values


def apply_action(game, action, args):
    from .gameplay import prepare_progress
    from .gameplay_characters import ensure_abilities
    from .gameplay_equipment import ensure_equipment_appraisal
    from .gameplay_library import sync_novels
    from .gameplay_plans import sync_plans
    from .gameplay_training import prepare_training
    from .newgame import validate_saved_state
    from .protocol import validate_data

    definitions = {definition["id"]: (definition, handler) for definition, handler in (*GROWTH, *ECONOMY, *PROGRESS, *MODES)}
    wrapped = {definition["id"]: definition for definition in route_specs(game.catalog)}
    if action in definitions:
        definition, handler = definitions[action]
    elif action in wrapped:
        definition, handler = wrapped[action], None
    else:
        raise GameplayError("不支持的管理操作", 400, 4007)
    values = arguments(definition, args)
    no_battle(game)
    from .gameplay_effects import settle_before_effect_change
    from .gameplay_idle import reconcile
    from .gameplay_skin_bonds import reconcile_skin_bonds
    configured_rules = copy.deepcopy(game.rules)
    tutorial_mode = game.progress.get("features", {}).get("tutorial", {}).get("data", {}).get("mode")
    game.prepare()
    settle_before_effect_change(game)
    if handler:
        result = handler(game, values)
    else:
        response = game.execute(definition["route"], "POST", values)
        result = changed(definition["label"] + "完成", timing=definition["timing"])
        result["response"] = response
    if tutorial_mode == "guided" and game.progress["features"]["tutorial"]["data"]["mode"] == "legacy":
        game.rules = configured_rules
        game.prepare()
    reconcile(game, settle_pending=False)
    reconcile_skin_bonds(game)
    ensure_abilities(game)
    ensure_equipment_appraisal(game)
    if "m_plan_restricts" in game.catalog.tables:
        sync_plans(game)
    sync_novels(game)
    prepare_training(game)
    validate_saved_state(game.state)
    prepare_progress(game.progress, game.now)
    validate_data(game.progress)
    return result
