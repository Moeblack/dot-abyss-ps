from __future__ import annotations

from .admin_actions_common import MAX_VALUE, changed, choice, field, no_expedition, owned, remove_rows, selected, selection_fields, spec
from .gameplay_catalog import GameplayError, number
from .gameplay_characters import ability_release_policy, ensure_abilities
from .gameplay_content import EQUIPMENT, create_character, create_equipment
from .gameplay_effects import settle_before_effect_change
from .gameplay_records import find, put, rows


def unlock_characters(game, args):
    values = selected(game, args, "m_characters")
    added = 0
    for identifier in values:
        if find(game.state, "t_characters", m_character_id=identifier) is None:
            create_character(game.state, identifier, game.catalog, game.user_id, game.now, game.progress)
            added += 1
    return changed("已获得角色", added, timing="page")


def delete_character(game, args):
    from .gameplay_idle import remove_relation, status
    from .gameplay_tavern import state

    no_expedition(game)
    data = state(game)
    if data["active"] is not None:
        raise GameplayError("请先结束酒馆轮次，再移除角色")
    character = owned(game, "t_characters", args["character_id"])
    remaining = [row for row in rows(game.state, "t_characters") if row["id"] != character["id"]]
    if not remaining:
        raise GameplayError("至少保留一个角色作为主页角色")
    settle_before_effect_change(game)
    dependent = {"t_character_abilities", "t_character_bond_parameters", "t_character_condition_resistances", "t_exploration_party_characters"}
    for table, container in game.state["records"].items():
        matches = [row for row in container["up"] if row.get("t_character_id") == character["id"]]
        if not matches:
            continue
        if table in dependent:
            container["up"][:] = [row for row in container["up"] if row not in matches]
        elif table == "t_idle_exploration_party_characters":
            for row in matches:
                row.update(t_character_id=0, t_weapon_id=0)
        else:
            raise GameplayError("角色仍被未支持清理的记录引用：" + table)
    for relation in list(rows(game.state, "t_idle_exploration_party_relations")):
        if not any(row["party_no"] == relation["party_no"] and row["t_character_id"] for row in rows(game.state, "t_idle_exploration_party_characters")):
            remove_relation(game, relation)
    if not rows(game.state, "t_idle_exploration_party_relations"):
        status(game).update(exploring_state=0, exploring_start_at=None)
    for key, members in data["assignments"].items():
        data["assignments"][key] = [0 if value == character["m_character_id"] else value for value in members]
    profile = rows(game.state, "t_profiles")[0]
    user = rows(game.state, "t_statuses")[0]
    if user["favorite_t_character_id"] == character["id"]:
        user["favorite_t_character_id"] = remaining[0]["id"]
        profile["favorite_m_character_skin_id"] = remaining[0]["battle_m_character_skin_id"]
    if user["rental_t_character_id"] == character["id"]:
        user["rental_t_character_id"] = 0
    novels = {row["id"] for row in game.catalog.rows("m_novel_characters") if row["m_character_id"] == character["m_character_id"]}
    remove_rows(game, "t_novel_characters", lambda row: row["m_novel_character_id"] in novels)
    rows(game.state, "t_characters").remove(character)
    game.progress["operation_receipts"].clear()
    return changed("已移除角色并清理编队、技能和展示关系", timing="page", warnings=["已拥有外观、历史领奖记录和累计获得统计保留。"])


def set_intimacy(game, character, target):
    definitions = game.catalog.rows("m_character_intimacy_levels")
    maximum = max((row["total_point"] for row in definitions), default=0)
    number(target, 0, maximum)
    old = character["intimacy_point"]
    character["intimacy_point"] = target
    if target > old:
        character["bond_point"] = number(character["bond_point"] + sum(row["add_bond_point"]
            for row in game.catalog.rows("m_intimacy_bond_point_rewards") if old < row["intimacy_point"] <= target), 0, MAX_VALUE)
    elif target < old:
        policy, _ = ability_release_policy(game)
        for ability in rows(game.state, "t_character_abilities"):
            if ability["t_character_id"] == character["id"] and ability["level"] > 1:
                key = f"{ability['id']}:{character['id']}:{ability['m_character_ability_id']}"
                policy["retained_levels"][key] = ability["level"]


def edit_characters(game, args):
    values = selected(game, args, "m_characters")
    updates = set(args) - {"ids", "all"}
    if not updates:
        raise GameplayError("至少填写一个要修改的成长字段", 400, 4007)
    warnings = []
    for identifier in values:
        character = find(game.state, "t_characters", m_character_id=identifier)
        if character is None:
            raise GameplayError("请先获得要修改的角色")
        master = game.catalog.get("m_characters", identifier)
        if "level" in args:
            target = number(args["level"], 1, game.rules["character_level_cap"])
            if target > 1 and not any(row["level"] == target for row in game.catalog.rows("m_character_levels")):
                raise GameplayError("角色等级缺少主数据")
            character.update(level=target, exp=0)
        if "limit_break_count" in args:
            limits = [row for row in game.catalog.rows("m_character_limit_break_defines") if row["rarity"] == master["rarity"]]
            if len(limits) != 1:
                raise GameplayError("角色突破规则缺失")
            target = number(args["limit_break_count"], 0, limits[0]["max_limit_break_count"])
            delta = max(0, target - character["limit_break_count"])
            character["limit_break_count"] = target
            character["limit_break_core_point"] = number(character["limit_break_core_point"] + delta * limits[0]["limit_break_core_point"], 0, MAX_VALUE)
        if "intimacy_point" in args:
            if args["intimacy_point"] < character["intimacy_point"]:
                warnings.append("降低好感保留已经取得的技能等级、剧情与奖励；再次跨越阈值按游戏现有赠礼规则处理。")
            set_intimacy(game, character, args["intimacy_point"])
        for name in ("love_point", "bond_point", "limit_break_core_point"):
            if name in args:
                character[name] = args[name]
        if "favorite" in args:
            character["is_favorite"] = int(args["favorite"])
    ensure_abilities(game)
    if args.get("max_skills"):
        owned_ids = {row["id"] for row in rows(game.state, "t_characters") if row["m_character_id"] in values}
        for ability in rows(game.state, "t_character_abilities"):
            if ability["t_character_id"] in owned_ids:
                ability["level"] = ability["possible_max_level"]
    return changed("已修改角色成长", len(values), timing="next_battle", warnings=sorted(set(warnings)))


def set_ability(game, args):
    ensure_abilities(game)
    ability = owned(game, "t_character_abilities", args["ability_id"])
    ability["level"] = number(args["level"], 0 if ability["possible_max_level"] == 0 else 1, ability["possible_max_level"])
    return changed("已修改技能等级", timing="next_battle")


def set_parameter(game, args):
    character = owned(game, "t_characters", args["character_id"])
    master = game.catalog.get("m_characters", character["m_character_id"])
    kind = args["parameter_type"]
    if args["kind"] == "bond":
        allowed = {row["up_parameter_type"] for row in game.catalog.rows("m_character_bond_parameter_groups") if row["party_position"] == master["party_position"]}
        candidates = [row["level"] for row in game.catalog.rows("m_character_bond_parameter_levels") if row["up_parameter_type"] == kind and kind in allowed]
        table = "t_character_bond_parameters"
    else:
        candidates = [row["level_max"] for row in game.catalog.rows("m_character_condition_resistance_levels") if row["rarity"] == master["rarity"] and row["up_parameter_type"] == kind]
        table = "t_character_condition_resistances"
    if not candidates:
        raise GameplayError("角色不支持该属性加成")
    level = number(args["level"], 0, max(candidates))
    put(game.state, table, {"t_character_id": character["id"], "up_parameter_type": kind}, {"level": level}, game.user_id)
    return changed("已修改属性加成", timing="next_battle")


def unlock_skins(game, args):
    kind = args["kind"]
    master = "m_character_skins" if kind == "character" else "m_weapon_skins"
    table = "t_character_skins" if kind == "character" else "t_weapon_skins"
    field_name = "m_character_skin_id" if kind == "character" else "m_weapon_skin_id"
    values = selected(game, args, master)
    added = 0
    for identifier in values:
        if find(game.state, table, **{field_name: identifier}) is None:
            put(game.state, table, {field_name: identifier}, {}, game.user_id)
            added += 1
    return changed("已解锁外观", added, timing="page")


def create_gear(game, args):
    for _ in range(args["amount"]):
        create_equipment(game.state, args["kind"], args["master_id"], game.catalog, game.user_id, game.progress)
    return changed("已创建装备", args["amount"], timing="page")


def edit_gear(game, args):
    from .gameplay_equipment import update_appraisal_state

    kind = args["kind"]
    singular, plural = EQUIPMENT[kind]
    gear = owned(game, "t_" + plural, args["equipment_id"])
    master = game.catalog.get("m_" + plural, gear[f"m_{singular}_id"])
    if "parameter" in args:
        value = number(args["parameter"], master["min_parameter"], master["max_parameter"])
        if (value - master["min_parameter"]) % master["lottery_interval"]:
            raise GameplayError("装备属性不符合主表间隔")
        gear["up_count"] = value
    if "locked" in args:
        gear["is_lock"] = int(args["locked"])
    if args.get("appraised"):
        for row in rows(game.state, f"t_{singular}_enchantments"):
            if row[f"t_{singular}_id"] == gear["id"]:
                row["is_appraised"] = 1
        remove_rows(game, "t_appraisal_result_pools", lambda row: (row["content_type"], row["t_equipment_id"]) == (kind, gear["id"]))
        update_appraisal_state(game, singular, gear)
    return changed("已修改装备", timing="next_battle")


def edit_enchantment(game, args):
    from .gameplay_equipment import update_appraisal_state

    singular, plural = EQUIPMENT[args["kind"]]
    enchant = owned(game, f"t_{singular}_enchantments", args["enchantment_id"])
    gear = owned(game, "t_" + plural, enchant[f"t_{singular}_id"])
    option = game.catalog.get("m_enchantment_abilities", args["ability_id"])
    if option["enchantment_ability_no"] != enchant["enchantment_ability_no"]:
        raise GameplayError("该词条选项不属于装备现有词条组")
    game.catalog.get("m_ability_effects", option["m_ability_effect_id"])
    enchant.update(m_ability_effect_id=option["m_ability_effect_id"], level=option["level"], is_appraised=1)
    remove_rows(game, "t_appraisal_result_pools", lambda row: (row["content_type"], row["t_equipment_id"]) == (args["kind"], gear["id"]))
    update_appraisal_state(game, singular, gear)
    return changed("已修改装备词条", timing="next_battle")


def delete_gear(game, args):
    no_expedition(game)
    kind = args["kind"]
    singular, plural = EQUIPMENT[kind]
    gear = owned(game, "t_" + plural, args["equipment_id"])
    settle_before_effect_change(game)
    for table, container in game.state["records"].items():
        if table != f"t_{singular}_enchantments":
            for row in container["up"]:
                if row.get(f"t_{singular}_id") == gear["id"]:
                    row[f"t_{singular}_id"] = 0
    remove_rows(game, f"t_{singular}_enchantments", lambda row: row[f"t_{singular}_id"] == gear["id"])
    remove_rows(game, "t_appraisal_result_pools", lambda row: (row["content_type"], row["t_equipment_id"]) == (kind, gear["id"]))
    rows(game.state, "t_" + plural).remove(gear)
    return changed("已删除装备并解除装备关系", timing="page")


def edit_building(game, args):
    from .gameplay_research import current_skill, ensure_research_rewards, skill_definitions

    identifier = args["building_id"]
    game.catalog.get("m_buildings", identifier)
    levels = sorted((row for row in game.catalog.rows("m_building_levels") if row["m_building_id"] == identifier), key=lambda row: row["level"])
    if not levels:
        raise GameplayError("设施等级主表缺失")
    settle_before_effect_change(game)
    building = find(game.state, "t_buildings", m_building_id=identifier)
    if building is None:
        building = put(game.state, "t_buildings", {"m_building_id": identifier}, {
            "level": levels[0]["level"], "exp": levels[0]["exp"], "checked_level": 0, "opened_at": game.now.isoformat()}, game.user_id)
    if "level" in args:
        target = next((row for row in levels if row["level"] == args["level"]), None)
        if target is None:
            raise GameplayError("设施不支持指定等级")
        building.update(level=target["level"], exp=target["exp"], checked_level=min(building["checked_level"], target["level"]))
    if "skill_type" in args or "skill_level" in args:
        if "skill_type" not in args or "skill_level" not in args:
            raise GameplayError("请同时填写研究类型和研究等级", 400, 4007)
        definitions = skill_definitions(game, identifier, args["skill_type"])
        level = number(args["skill_level"], 0, max(row["skill_level"] for row in definitions))
        record = current_skill(game, identifier, args["skill_type"])
        record.update(skill_level=level, checked_skill_level=min(record["checked_skill_level"], level))
        ensure_research_rewards(game)
    return changed("已修改设施与研究", timing="next_battle")


def maximize_research(game, args):
    values = selected(game, args, "m_buildings")
    for identifier in values:
        levels = [row["level"] for row in game.catalog.rows("m_building_levels") if row["m_building_id"] == identifier]
        if not levels:
            raise GameplayError("设施等级主表缺失")
        edit_building(game, {"building_id": identifier, "level": max(levels)})
        types = {row["skill_type"] for row in game.catalog.rows("m_building_skill_levels") if row["m_building_id"] == identifier}
        for kind in types:
            level = max(row["skill_level"] for row in game.catalog.rows("m_building_skill_levels") if row["m_building_id"] == identifier and row["skill_type"] == kind)
            edit_building(game, {"building_id": identifier, "skill_type": kind, "skill_level": level})
    return changed("已开放设施并完成研究", len(values), timing="next_battle")


def edit_synchro(game, args):
    from .gameplay_synchro import ensure_synchro

    definition = next((row for row in game.catalog.rows("m_level_synchros") if row["grade"] == args["grade"]), None)
    if definition is None:
        raise GameplayError("同步等级不存在")
    level = number(args["level"], definition["level_min"], definition["level_max"])
    ensure_synchro(game).update(grade=args["grade"], level=level)
    return changed("已修改等级同步", timing="next_battle")


def party_slots(game, args):
    from .gameplay_party_state import ensure_party

    target = args["amount"]
    if target < game.rules["party_slots"]:
        raise GameplayError("槽位不能低于当前规则，请先修改编队槽位规则")
    if any(row["party_no"] > target for row in rows(game.state, "t_exploration_parties")):
        raise GameplayError("请先删除超出目标数量的编队")
    rows(game.state, "t_statuses")[0]["max_party_count"] = target
    for number_ in range(1, target + 1):
        ensure_party(game, number_)
    return changed("已修改编队槽位", target, timing="page")


GEAR_KIND = lambda: choice("kind", "装备类别", [(70, "武器"), (80, "防具"), (90, "饰品")])

OPERATIONS = [
    (spec("character.unlock", "character", "获得角色", selection_fields("m_characters"), description="已拥有角色会跳过，不重复转换成碎片。", timing="page"), unlock_characters),
    (spec("character.delete", "character", "移除角色", [field("character_id", "拥有的角色", min=1, source="t_characters")], description="清理技能、编队、酒馆与展示关系；保留外观和历史奖励。", timing="page"), delete_character),
    (spec("character.edit", "character", "修改角色成长", [*selection_fields("m_characters"),
        field("level", "等级", required=False, min=1), field("limit_break_count", "突破次数", required=False),
        field("intimacy_point", "好感度", required=False), field("love_point", "爱心", required=False),
        field("bond_point", "剩余羁绊点", required=False), field("limit_break_core_point", "剩余核心点", required=False),
        field("favorite", "收藏", "boolean", required=False), field("max_skills", "技能升至当前可用上限", "boolean", required=False)],
        description="留空字段保持不变；突破和好感提升联动点数，显式填写点数时以填写值为准。", timing="next_battle"), edit_characters),
    (spec("character.ability", "character", "设置技能等级", [field("ability_id", "角色技能", min=1, source="t_character_abilities"), field("level", "等级")], timing="next_battle"), set_ability),
    (spec("character.parameter", "character", "设置羁绊或抗性加成", [field("character_id", "角色", min=1, source="t_characters"),
        choice("kind", "加成类别", [("bond", "羁绊属性"), ("resistance", "异常抗性")]), field("parameter_type", "属性类型", min=1), field("level", "等级")],
        description="直接校正加成等级，不扣剩余点数；合法类型和上限按角色主表校验。", timing="next_battle"), set_parameter),
    (spec("skin.unlock", "appearance", "解锁服装或武器外观", [choice("kind", "外观类别", [("character", "角色服装"), ("weapon", "武器外观")]), *selection_fields("m_character_skins")], timing="page"), unlock_skins),
    (spec("equipment.create", "equipment", "创建装备", [GEAR_KIND(), field("master_id", "装备主数据 ID", min=1), field("amount", "数量", min=1, max=100, default=1)], timing="page"), create_gear),
    (spec("equipment.edit", "equipment", "修改装备属性", [GEAR_KIND(), field("equipment_id", "拥有的装备 ID", min=1),
        field("parameter", "实际属性值", required=False, max=2 ** 32 - 1),
        field("locked", "锁定", "boolean", required=False), field("appraised", "鉴定现有词条", "boolean", required=False)], timing="next_battle"), edit_gear),
    (spec("equipment.enchantment", "equipment", "选择装备词条", [GEAR_KIND(), field("enchantment_id", "拥有的词条 ID", min=1),
        field("ability_id", "词条选项", min=1, source="m_enchantment_abilities")], description="仅允许原有词条组中的选项。", timing="next_battle"), edit_enchantment),
    (spec("equipment.delete", "equipment", "删除装备", [GEAR_KIND(), field("equipment_id", "拥有的装备 ID", min=1)],
        description="清除装备关系、词条与待鉴定结果，不发放分解奖励。", timing="page"), delete_gear),
    (spec("research.edit", "research", "修改设施与研究", [field("building_id", "设施", min=1, source="m_buildings"),
        field("level", "设施等级", required=False, min=1), field("skill_type", "研究类型", required=False, min=1), field("skill_level", "研究等级", required=False)], timing="next_battle"), edit_building),
    (spec("research.maximize", "research", "开放设施并完成研究", selection_fields("m_buildings"), description="先结算已有探索收益，再启用新设施效果。", timing="next_battle"), maximize_research),
    (spec("character.synchro", "character", "修改等级同步", [field("grade", "阶级", min=1), field("level", "等级", min=1)], timing="next_battle"), edit_synchro),
    (spec("party.slots", "party", "设置编队槽位", [field("amount", "槽位数", min=1, max=10)], timing="page"), party_slots),
]
