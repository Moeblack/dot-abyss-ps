from __future__ import annotations

from .admin_actions_common import MAX_VALUE, changed, choice, field, no_battle, selected, selection_fields, spec
from .gameplay_catalog import GameplayError, number
from .gameplay_receipts import feature_data
from .gameplay_records import find, put, rows
from .gameplay_story_state import COMPLETED_SEQUENCE


def finish_story(game, identifiers, *, rewards=False):
    from .gameplay_battle_rewards import complete_quest, first_rewards, reward_state
    from .gameplay_effects import settle_before_effect_change
    from .gameplay_story_progress import available_quests, prepare_story_state, release_data, sequences

    settle_before_effect_change(game)
    definitions = {row["id"]: row for row in game.catalog.rows("m_chapter_quests")}
    completed, pending, visiting = set(), set(identifiers), set()

    def include(identifier):
        if identifier in completed:
            return
        if identifier in visiting:
            raise GameplayError("主线前置存在循环，无法自动补齐")
        visiting.add(identifier)
        quest = game.catalog.get("m_chapter_quests", identifier)
        area = game.catalog.get("m_chapter_areas", quest["m_chapter_area_id"])
        try:
            previous_areas = {int(value) for value in str(area["release_condition_area_ids"]).split(",") if value.strip()}
        except ValueError as error:
            raise GameplayError("主线区域前置格式错误") from error
        for previous in definitions.values():
            if previous["m_chapter_area_id"] in previous_areas or previous["m_chapter_area_id"] == area["id"] and previous["progress_order"] < quest["progress_order"]:
                include(previous["id"])
        visiting.remove(identifier)
        completed.add(identifier)

    for identifier in pending:
        include(identifier)
    before = available_quests(game)
    progress = prepare_story_state(game)
    ledger = reward_state(game)
    for identifier in sorted(completed):
        quest = definitions[identifier]
        area = game.catalog.get("m_chapter_areas", quest["m_chapter_area_id"])
        steps = sequences(game, identifier)
        put(game.state, "t_chapter_quests", {"m_chapter_quest_id": identifier}, {
            "m_chapter_id": area["m_chapter_id"], "m_chapter_area_id": area["id"], "sequence": COMPLETED_SEQUENCE})
        for step in steps:
            if not step["m_chapter_quest_battle_id"]:
                continue
            key = f"{identifier}:{step['sequence']}"
            if rewards:
                first_rewards(game, {"kind": "main", "m_chapter_quest_id": identifier, "sequence": step["sequence"]})
            else:
                ledger["first_claims"].setdefault(key, {"legacy": True})
        if rewards:
            complete_quest(game, quest)
        else:
            ledger["quest_claims"].setdefault(str(identifier), {"legacy": True})
        progress["pending_releases"].pop(str(identifier), None)
    release_data(game, before)
    return completed


def story_complete(game, args):
    no_battle(game)
    completed = finish_story(game, selected(game, args, "m_chapter_quests"), rewards=args["grant_unclaimed_rewards"])
    return changed("已完成选中主线及必要前置", len(completed), timing="page",
                   warnings=[] if args["grant_unclaimed_rewards"] else ["未发放的首通奖励已标为跳过；已发放奖励保留。"])


def story_reset_rewards(game, args):
    from .gameplay_battle_rewards import reward_state
    from .gameplay_story_progress import prepare_story_state

    no_battle(game)
    if not args["allow_duplicate_rewards"]:
        raise GameplayError("重置首通记录需要明确允许再次领奖", 400, 4007)
    ids = set(selected(game, args, "m_chapter_quests"))
    ledger = reward_state(game)
    ledger["quest_claims"] = {key: value for key, value in ledger["quest_claims"].items() if int(key) not in ids}
    ledger["first_claims"] = {key: value for key, value in ledger["first_claims"].items() if int(key.split(":")[0]) not in ids}
    progress = prepare_story_state(game)
    progress["legacy_completed_quests"] = [key for key in progress["legacy_completed_quests"] if key not in ids]
    progress["legacy_cleared_battles"] = [key for key in progress["legacy_cleared_battles"] if int(key.split(":")[0]) not in ids]
    game.progress["operation_receipts"].clear()
    return changed("已重置选中主线的首通奖励记录", len(ids), timing="page", warnings=["过去发放的奖励保留；重新满足领取流程时可以再次领取。"])


def tutorial(game, args):
    from .gameplay_tutorial import tutorial_data

    no_battle(game)
    current = find(game.state, "t_feature_tutorials")
    if args["all"]:
        flags = (1 << 19) - 1
    else:
        flags = 0
        for identifier in args["ids"]:
            number(identifier, 1, 19)
            flags |= 1 << (identifier - 1)
    if not flags and not args["complete_prologue"]:
        raise GameplayError("请选择要修改的教程", 400, 4007)
    old = current["completed_flags"] if current else 0
    value = old | flags if args["completed"] else old & ~flags
    put(game.state, "t_feature_tutorials", {}, {"completed_flags": value}, game.user_id)
    if args["complete_prologue"]:
        rows(game.state, "t_prologue_tutorials")[0]["status"] = 99
        tutorial_data(game)["mode"] = "legacy"
    return changed("已修改教程状态，奖励记录保留", timing="relogin", warnings=["数据可同步到客户端；重新运行引导流程需要重新登录。"])


def feature_unlock(game, args):
    ids = selected(game, args, "m_feature_locks")
    for identifier in ids:
        put(game.state, "t_feature_locks", {"m_feature_lock_id": identifier}, {"is_unlocked": args["unlocked"]})
    return changed("已修改功能入口", len(ids), timing="page", warnings=["启用规则自动开放的入口可能再次打开；入口状态不代替关卡自身前置。"] if not args["unlocked"] else [])


def novels(game, args):
    from .admin_actions_growth import set_intimacy, unlock_characters
    from .gameplay_library import NOVELS, NOVEL_RECORDS, read_receipts, sync_novels
    from .gameplay_tutorial import tutorial_data

    kind = args["kind"]
    ids = selected(game, args, "m_novel_" + NOVELS[kind])
    definitions = [game.catalog.get("m_novel_" + NOVELS[kind], identifier) for identifier in ids]
    if kind == 1:
        quests = {row["m_chapter_quest_id"] for row in game.catalog.rows("m_chapter_quest_sequences") if row["m_novel_main_id"] in ids}
        finish_story(game, quests)
    if kind in (6, 7):
        rows(game.state, "t_prologue_tutorials")[0]["status"] = 99
        tutorial_data(game)["mode"] = "legacy"
    for definition in definitions:
        identifier = definition["id"]
        if kind == 3:
            if definition["condition_type"] != 1:
                raise GameplayError("该角色剧情条件尚未绑定")
            unlock_characters(game, {"ids": [definition["m_character_id"]]})
            character = find(game.state, "t_characters", m_character_id=definition["m_character_id"])
            level = next((row for row in game.catalog.rows("m_character_intimacy_levels") if row["level"] == definition["condition_value"]), None)
            if level is None:
                raise GameplayError("剧情好感度条件缺少主数据")
            if character["intimacy_point"] < level["total_point"]:
                set_intimacy(game, character, level["total_point"])
        elif kind == 5:
            game.catalog.get("m_character_skins", definition["m_character_skin_id"])
            put(game.state, "t_character_skins", {"m_character_skin_id": definition["m_character_skin_id"]}, {}, game.user_id)
        elif kind == 4:
            put(game.state, "t_novel_homes", {"m_novel_home_id": identifier}, {}, game.user_id)
        if kind in NOVEL_RECORDS:
            table, column = NOVEL_RECORDS[kind]
            record = find(game.state, table, **{column: identifier})
            if record is None:
                record = put(game.state, table, {column: identifier}, {"is_unread": 1, **({"status": 1} if kind == 3 else {})}, None if kind == 7 else game.user_id)
            if args["read_state"] != "keep":
                record["is_unread"] = int(args["read_state"] == "unread")
        if args["read_state"] == "read":
            read_receipts(game)[f"{kind}:{identifier}"] = True
            if kind == 2:
                feature_data(game, "library").setdefault("event_reward_claims", {}).setdefault(str(identifier), {"source": "existing_read", "rewards": []})
    sync_novels(game)
    return changed("已解锁剧情并补齐必要条件", len(ids), timing="page", warnings=["标记未读只改变显示，不清除领奖记录；主线剧情解锁会完成关联章节及前置。", "剧情播放仍需要对应素材，活动剧情还受主数据开放日期限制。"])


def idle_edit(game, args):
    from .gameplay_effects import settle_before_effect_change
    from .gameplay_idle import status, world

    settle_before_effect_change(game)
    if "points" in args:
        status(game)["point"] = args["points"]
    if args["all_areas"]:
        for definition in game.catalog.rows("m_idle_exploration_map_areas"):
            levels = [row["difficulty"] for row in game.catalog.rows("m_idle_exploration_map_area_difficulties") if row["m_idle_exploration_map_area_id"] == definition["id"]]
            if levels:
                put(game.state, "t_idle_exploration_maps", {"m_idle_exploration_map_id": definition["m_idle_exploration_map_id"]}, {}, game.user_id)
                put(game.state, "t_idle_exploration_map_areas", {"m_idle_exploration_map_area_id": definition["id"]}, {"difficulty": max(levels)}, game.user_id)
    world(game)
    return changed("已修改探索点和区域", timing="page", warnings=["探索点不能低于已完成主线提供的点数；收益修改前已结算旧时间段。"])


def tavern_reset(game, args):
    from .gameplay_tavern import daily, state

    data = state(game)
    if data["active"] is not None:
        raise GameplayError("请先结束当前酒馆轮次，再重置次数")
    data["free_used"] = args["used"]
    daily(game)["worked_count"] = args["used"]
    return changed("已修改酒馆本日使用次数", timing="page")


def tavern_finish(game, args):
    from .gameplay_statistics import record
    from .gameplay_tavern import play_turn, state

    data = state(game)
    session = data["active"]
    if session is None:
        raise GameplayError("当前没有正在进行的酒馆轮次")
    turns = number(session["max_turn"] - session["turn"] + 1, 1, 1000)
    for _ in range(turns):
        if not session["hand"]:
            raise GameplayError("酒馆本轮没有可选卡片")
        response, _ = play_turn(game, session, session["hand"][0])
        if data["active"] is None:
            record(game.progress, game.now, 200000)
            return {**changed("已自动完成当前酒馆轮次", timing="page"), "response": response}
    raise GameplayError("酒馆轮次没有按预期结束")


def training_complete(game, args):
    from .gameplay_training import coaching_rewards, prepare_training, progress

    no_battle(game)
    kind = args["kind"]
    ids = selected(game, args, f"m_{kind}_quests")
    prepare_training(game)
    for identifier in ids:
        current = find(game.state, f"t_{kind}_quests", **{f"m_{kind}_quest_id": identifier})
        current["status"] = 99
        if kind == "tryout":
            current["high_score"] = max(current["high_score"], args["score"])
        if kind == "coaching":
            if args["grant_unclaimed_rewards"]:
                coaching_rewards(game, identifier)
            else:
                progress(game)["reward_claims"].setdefault(str(identifier), {"legacy": True})
    return changed("已完成选中训练关卡", len(ids), timing="page")


def nether_points(game, args):
    from .gameplay_nether_state import POINT_FIELDS, state

    no_battle(game)
    data = state(game)
    for key in (*POINT_FIELDS, "total_score"):
        if key in args:
            if key == "total_score":
                data[key] = args[key]
            else:
                data["points"][key] = args[key]
    return changed("已修改深渊研究点和总分", timing="page")


def nether_hp(game, args):
    from .gameplay_nether_state import run

    no_battle(game)
    expedition = run(game)
    for key in expedition["hp"]:
        expedition["hp"][key] = args["ratio"]
    return changed("已修改当前远征队伍 HP", timing="page")


def side_story_complete(game, args):
    from .gameplay_side_story_state import progress, stages, writable_stage

    no_battle(game)
    identifiers = selected(game, args, "m_side_stories")
    data = progress(game)
    for identifier in identifiers:
        if identifier not in data["seen_stories"]:
            data["seen_stories"].append(identifier)
        for stage in stages(game, identifier):
            entry = writable_stage(data, stage["id"])
            entry.update(seen=True, clears=max(1, entry["clears"]), reward_received=True)
    return changed("已完成常驻外传", len(identifiers), timing="page", warnings=["未发放奖励标为跳过，避免后续重复补发。"])


def event_join(game, args):
    from .gameplay_events import join

    no_battle(game)
    ids = selected(game, args, "m_events")
    for identifier in ids:
        definition = game.catalog.get("m_events", identifier)
        if args["unlock_entry"]:
            feature_id = 1000 + definition["type"]
            game.catalog.get("m_feature_locks", feature_id)
            put(game.state, "t_feature_locks", {"m_feature_lock_id": feature_id}, {"is_unlocked": True})
        join(game, {"m_event_id": identifier})
    return changed("已参加活动", len(ids), timing="page")


def event_supply(game, args):
    from .gameplay_events import event_supply as supply, require_event

    event = require_event(game, args["event_id"])
    existing = find(game.state, "t_event_timed_parameters", m_event_id=event["id"], type=args["type"])
    if existing is None:
        raise GameplayError("所选活动没有这种计时资源")
    supply(game, event["id"], type_id=args["type"])["amount"] = args["amount"]
    return changed("已修改活动次数", timing="page")


def event_progress(game, args):
    from .gameplay_event_quest_state import QUEST_KINDS, prepare_event_quests, saved_node
    from .gameplay_events import require_event

    no_battle(game)
    event = require_event(game, args["event_id"])
    kind = next((kind for kind in QUEST_KINDS.values() if kind.event_type == event["type"]), None)
    if kind is None:
        raise GameplayError("此编辑器适用于剧情、狩猎、委托、养成和竞速活动；其他玩法使用各自操作")
    prepare_event_quests(game, event)
    count = 0
    for node in game.catalog.rows(kind.nodes):
        if node["m_event_id"] != event["id"]:
            continue
        record = find(game.state, kind.records, **{kind.field: node["id"]})
        record["status"] = 99
        if kind.name in ("speedrun", "commission"):
            record["clear_time"] = args["clear_time"]
        entry = saved_node(game, kind, node)
        entry.update(clears=max(1, entry["clears"]), first_claimed=True)
        count += 1
    prepare_event_quests(game, event)
    if kind.staged:
        from .gameplay_event_quest_state import expose_novel

        node_ids = {row["id"] for row in game.catalog.rows(kind.nodes) if row["m_event_id"] == event["id"] and row["type"] == 1}
        claims = feature_data(game, "library").setdefault("event_reward_claims", {})
        for novel in game.catalog.rows(f"m_event_{kind.name}_novels"):
            if novel[kind.field] in node_ids:
                expose_novel(game, novel["m_novel_event_id"])
                claims.setdefault(str(novel["m_novel_event_id"]), {"source": "existing_read", "rewards": []})
    return changed("已完成活动关卡", count, timing="page", warnings=["未发放首通奖励标为跳过；任务战绩统计通过独立操作修改。"])


def event_reset(game, args):
    from .gameplay_event_missions import definitions
    from .gameplay_events import event_data, prepare_event
    from .gameplay_library import read_receipts

    no_battle(game)
    if not args["allow_duplicate_rewards"]:
        raise GameplayError("活动重置会使奖励再次可领，需要明确确认", 400, 4007)
    identifier = args["event_id"]
    event = game.catalog.get("m_events", identifier)
    old_epoch = event_data(game, identifier)["epoch"]
    targets = {}
    for table in game.catalog.tables:
        if not table.startswith("m_event_"):
            continue
        records = game.catalog.rows(table)
        ids = {row["id"] for row in records if row.get("m_event_id") == identifier}
        if ids:
            singular = table[:-3] + "y" if table.endswith("ies") else table[:-1]
            targets[singular + "_id"] = ids
    for table, container in game.state["records"].items():
        if table.startswith("t_event_"):
            container["up"][:] = [row for row in container["up"] if row.get("m_event_id") != identifier and not any(row.get(key) in ids for key, ids in targets.items())]
    feature_data(game, "events")["instances"][str(identifier)] = {"epoch": number(old_epoch + 1)}
    mission_ids = {row["id"] for row in definitions(game, event)}
    game.progress["mission_claims"] = {key: value for key, value in game.progress["mission_claims"].items() if int(key.rsplit(":", 1)[1]) not in mission_ids}
    novel_ids = {row["id"] for row in game.catalog.rows("m_novel_events") if row["m_event_id"] == identifier}
    reads = read_receipts(game)
    for novel_id in novel_ids:
        reads.pop(f"2:{novel_id}", None)
        feature_data(game, "library").setdefault("event_reward_claims", {}).pop(str(novel_id), None)
    rows(game.state, "t_novel_events")[:] = [row for row in rows(game.state, "t_novel_events") if row["m_novel_event_id"] not in novel_ids]
    lineups = {row["id"] for row in game.catalog.rows("m_exchange_lineups") if row["m_exchange_category_id"] == event["m_exchange_category_id"]}
    counts = feature_data(game, "exchange").setdefault("counts", {})
    for lineup in lineups:
        counts.pop(str(lineup), None)
    game.progress["operation_receipts"].clear()
    battle = feature_data(game, "battle")
    if battle:
        battle["last_result"] = None
    if find(game.state, "t_events", m_event_id=identifier):
        prepare_event(game, event)
    return changed("已重置活动实例、任务与兑换记录", timing="page", warnings=["过去获得的货币、道具与邮件保留；活动奖励可以再次获取。"])


def event_statistic(game, args):
    from .gameplay_event_missions import EVENT_SITUATIONS, statistics
    from .gameplay_statistics import key

    game.catalog.get("m_events", args["event_id"])
    if args["situation"] not in EVENT_SITUATIONS:
        raise GameplayError("该统计类型不是已绑定的活动战绩")
    data = statistics(game, args["event_id"])
    identity = key(args["situation"], args["target"], args["sub_target"])
    values = data["totals"] if args["period"] == "total" else data["days"].setdefault(game.now.date().isoformat(), {})
    values[identity] = args["value"]
    return changed("已修改活动任务统计", timing="page")


def statistic(game, args):
    from .gameplay_statistics import bucket, key

    known = {row["situation_type"] for row in game.catalog.rows("m_missions")}
    if args["situation"] not in known:
        raise GameplayError("主表没有使用这种任务统计")
    data = bucket(game.progress)
    identity = key(args["situation"], args["target"], args["sub_target"])
    values = data["totals"] if args["period"] == "total" else data["days"].setdefault(game.now.date().isoformat(), {})
    values[identity] = args["value"]
    return changed("已修改累计任务统计", timing="page", warnings=["直接读取角色、关卡等状态的任务仍以实际状态为准。"])


STAT_FIELDS = [field("situation", "任务统计类型", min=1), field("target", "目标 ID", default=0), field("sub_target", "次目标 ID", default=0),
               choice("period", "周期", [("total", "累计"), ("today", "今日")], default="total"), field("value", "数值")]

OPERATIONS = [
    (spec("story.complete", "story", "完成主线及必要前置", [*selection_fields("m_chapter_quests"), field("grant_unclaimed_rewards", "发放尚未领取的首通奖励", "boolean", default=False)], timing="page"), story_complete),
    (spec("story.reset_rewards", "story", "重置主线首通奖励", [*selection_fields("m_chapter_quests"), field("allow_duplicate_rewards", "允许再次领取过去奖励", "boolean", default=False)], timing="page"), story_reset_rewards),
    (spec("tutorial.set", "story", "修改教程状态", [*selection_fields("t_feature_tutorials"), field("completed", "标记为已完成", "boolean", default=True), field("complete_prologue", "完成序章并退出强制引导", "boolean", default=False)], description="教程编号为 1 至 19；重开单个引导保留既有奖励记录。", timing="relogin"), tutorial),
    (spec("feature.set", "story", "开放或关闭功能入口", [*selection_fields("m_feature_locks"), field("unlocked", "开放入口", "boolean", default=True)], timing="page"), feature_unlock),
    (spec("novel.unlock", "story", "解锁剧情与回想", [choice("kind", "剧情类别", [(1, "主线"), (2, "活动"), (3, "角色好感"), (4, "主页"), (5, "服装"), (6, "序章"), (7, "其他")]),
        *selection_fields("m_novel_characters"), choice("read_state", "阅读状态", [("keep", "保持原状态"), ("read", "已读并跳过未领奖励"), ("unread", "显示未读，保留领奖记录")], default="keep")], timing="page"), novels),
    (spec("idle.edit", "idle", "修改探索成长和区域", [field("points", "探索点", required=False), field("all_areas", "开放全部已有探索区域", "boolean", default=False)], timing="page"), idle_edit),
    (spec("tavern.reset_count", "tavern", "设置酒馆本日次数", [field("used", "已使用次数")], timing="page"), tavern_reset),
    (spec("tavern.finish", "tavern", "自动完成当前酒馆轮次", description="每轮选择当前第一张卡片，按正常规则结算奖励。", timing="page"), tavern_finish),
    (spec("training.complete", "battle", "完成训练关卡", [choice("kind", "训练类别", [("coaching", "训练"), ("recollection", "回忆战"), ("tryout", "试炼")]),
        *selection_fields("m_coaching_quests"), field("score", "试炼分数", default=0), field("grant_unclaimed_rewards", "发放未领取训练奖励", "boolean", default=False)], timing="page"), training_complete),
    (spec("nether.points", "battle", "设置深渊研究点", [field(name, label, required=False) for name, label in (("sphere_skill_point", "技能研究点"), ("sphere_attack_point", "攻击研究点"),
        ("sphere_erosion_resist_point", "侵蚀抗性研究点"), ("sphere_erosion_up_point", "侵蚀强化研究点"), ("total_score", "累计得分"))], timing="page"), nether_points),
    (spec("nether.hp", "battle", "恢复或设置深渊队伍 HP", [field("ratio", "HP 千分比", max=1000, default=1000)], timing="page"), nether_hp),
    (spec("side_story.complete", "story", "完成常驻外传", selection_fields("m_side_stories"), timing="page"), side_story_complete),
    (spec("event.join", "event", "参加活动", [*selection_fields("m_events"), field("unlock_entry", "同时开放活动入口", "boolean", default=True)], timing="page"), event_join),
    (spec("event.supply", "event", "设置活动次数", [field("event_id", "活动", source="m_events", min=1), field("type", "次数类型", min=1, max=100, default=1), field("amount", "数量", max=100000)], timing="page"), event_supply),
    (spec("event.complete", "event", "完成活动已有关卡", [field("event_id", "活动", source="m_events", min=1), field("clear_time", "通关毫秒数", min=1, max=86400000, default=60000)], description="支持剧情、狩猎、委托、养成、竞速；不发放首通奖励。", timing="page"), event_progress),
    (spec("event.reset", "event", "重置整个活动实例", [field("event_id", "活动", source="m_events", min=1), field("allow_duplicate_rewards", "允许重新获取活动奖励", "boolean", default=False)], timing="page"), event_reset),
    (spec("event.statistic", "event", "修改活动任务战绩", [field("event_id", "活动", source="m_events", min=1), *STAT_FIELDS], timing="page"), event_statistic),
    (spec("mission.statistic", "mission", "修改任务累计统计", STAT_FIELDS, timing="page"), statistic),
]
