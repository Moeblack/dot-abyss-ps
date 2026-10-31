from __future__ import annotations

from datetime import timedelta

from .admin_actions_common import MAX_VALUE, changed, choice, field, owned, spec
from .gameplay_catalog import GameplayError, number
from .gameplay_content import BALANCES
from .gameplay_effects import grant_vip_titles, settle_before_effect_change
from .gameplay_records import find, grant, put, rows


def balance(game, args):
    row = rows(game.state, "t_statuses")[0]
    name = BALANCES[args["kind"]]
    previous = row[name]
    row[name] = number(args["amount"] if args["mode"] == "set" else previous + args["amount"] if args["mode"] == "add" else previous - args["amount"], 0, MAX_VALUE)
    return changed(f"余额已从 {previous} 调整为 {row[name]}")


def item_balance(game, args):
    kind, identifier = args["kind"], args["master_id"]
    table, column, field_name, master = {
        30: ("t_items", "m_item_id", "amount", "m_items"),
        54: ("t_event_currencies", "m_event_currency_id", "amount", "m_event_currencies"),
        56: ("t_event_boss_tickets", "m_event_boss_ticket_id", "amount", "m_event_boss_tickets"),
        170: ("t_gacha_tokens", "m_gacha_token_id", "token_num", "m_gacha_tokens"),
    }[kind]
    definition = game.catalog.get(master, identifier)
    current = find(game.state, table, **{column: identifier})
    before = current[field_name] if current else 0
    amount = args["amount"] if args["mode"] == "set" else before + args["amount"] if args["mode"] == "add" else before - args["amount"]
    number(amount, 0, definition.get("possession_limit", 0) or MAX_VALUE)
    put(game.state, table, {column: identifier}, {field_name: amount})
    return changed(f"数量已从 {before} 调整为 {amount}")


def reward(game, args):
    kind = args["kind"]
    identifier = args["master_id"]
    if kind in {*BALANCES, 20, 111} and identifier:
        raise GameplayError("货币与玩家经验的内容 ID 必须为 0", 400, 4007)
    granted = grant(game.state, [{"content_type": kind, "content_id": identifier, "amount": args["amount"]}], game.catalog, game.user_id, game.now, game.progress)
    result = changed("已发放内容", args["amount"], timing="page" if kind in (40, 60, 70, 80, 90, 140, 141) else "live")
    result["rewards"] = granted
    return result


def paid_grant(game, args):
    from .gameplay_wallet import grant_paid

    row = grant_paid(game.state, game.user_id, game.now, args["amount"], valid_days=args["days"])
    return changed(f"已发放本地付费宝石，批次 {row['id']}")


def paid_edit(game, args):
    from .gameplay_wallet import timestamp

    row = owned(game, "t_payment_dmm_currencies", args["lot_id"])
    if timestamp(row["valid_at"]) <= game.now and args["remaining"]:
        raise GameplayError("已过期批次不能恢复余额，请创建新的本地宝石批次")
    row["rest_amount"] = number(args["remaining"], 0, row["acquire_amount"])
    row["updated_at"] = game.now.isoformat()
    if "days" in args:
        row["valid_at"] = (game.now + timedelta(days=args["days"])).isoformat()
    return changed("已修改付费宝石批次")


def limited_edit(game, args):
    row = owned(game, "t_limited_items", args["lot_id"])
    definition = game.catalog.get("m_limited_items", row["m_limited_item_id"])
    amount = number(args["amount"], 0, definition["possession_limit"] or MAX_VALUE)
    from .gameplay_wallet import timestamp

    other = sum(item["amount"] for item in rows(game.state, "t_limited_items")
                if item["id"] != row["id"] and item["m_limited_item_id"] == row["m_limited_item_id"] and timestamp(item["expire_at"]) > game.now)
    if other + amount > (definition["possession_limit"] or MAX_VALUE):
        raise GameplayError("限时物品所有有效批次的数量超过持有上限")
    row.update(amount=amount, expire_at=(game.now + timedelta(days=args["days"])).isoformat())
    return changed("已修改限时物品批次")


def timed(game, args):
    from .gameplay_stamina import STAMINA_LIMIT, STAMINA_TYPE, recovery_record

    row = find(game.state, "t_timed_parameters", type=args["type"])
    if row is None:
        raise GameplayError("计时资源尚未初始化，请先开放对应玩法")
    row["amount"] = number(args["amount"], 0, STAMINA_LIMIT if args["type"] == STAMINA_TYPE else MAX_VALUE)
    row["last_recovery_at"] = game.now.isoformat()
    if args.get("reset_purchase_count"):
        if args["type"] != STAMINA_TYPE:
            raise GameplayError("购买次数重置只适用于体力", 400, 4007)
        recovery_record(game).update(recovery_count=0, last_recovery_at=game.now.isoformat())
    return changed("已修改计时资源")


def player_exp(game, args):
    from .gameplay_stamina import settle_stamina

    status = rows(game.state, "t_statuses")[0]
    candidates = [row for row in game.catalog.rows("m_user_ranks") if row["total_exp"] <= args["exp"]]
    if not candidates:
        raise GameplayError("玩家经验未匹配任何等级")
    rank = max(candidates, key=lambda row: row["rank"])
    status.update(player_exp=args["exp"], user_rank=rank["rank"])
    settle_stamina(game.state, game.catalog, game.user_id, game.now)
    return changed("已修改玩家经验和等级")


def subscription(game, args):
    identifier = args["master_id"]
    game.catalog.get("m_payment_subscriptions", identifier)
    settle_before_effect_change(game)
    previous = find(game.state, "t_payment_subscription_benefits", m_payment_subscription_id=identifier)
    put(game.state, "t_payment_subscription_benefits", {"m_payment_subscription_id": identifier}, {
        "expire_at": (game.now + timedelta(days=args["days"])).isoformat(),
        "continue_count": args.get("renewals", previous["continue_count"] if previous else 1),
        "is_notify_grace_period": False, "is_notify_expired": False}, game.user_id)
    return changed("已设置订阅期限，既有每日领取记录保留", timing="page")


def pass_edit(game, args):
    from .gameplay_pass_state import pass_state

    identifier = args["master_id"]
    game.catalog.get("m_mission_passes", identifier)
    data = pass_state(game.progress, identifier)
    if "points" in args:
        data["points"] = args["points"]
    if "premium" in args:
        data["premium"] = int(args["premium"])
    return changed("已修改通行证，已领取奖励记录保留", timing="page")


def pass_reset_claims(game, args):
    from .gameplay_pass_state import pass_state

    identifier = args["master_id"]
    game.catalog.get("m_mission_passes", identifier)
    if not args["allow_duplicate_rewards"]:
        raise GameplayError("重置领取记录需要明确允许再次领奖", 400, 4007)
    data = pass_state(game.progress, identifier)
    data.update(rewards={}, loop_received=0)
    return changed("已重置通行证领奖记录", timing="page", warnings=["过去已经发放的奖励保留；满足条件时可再次领取。"])


def vip_edit(game, args):
    from .gameplay_shop import vip_record

    settle_before_effect_change(game)
    vip_record(game).update(total_point=args["points"], updated_at=game.now.isoformat())
    grant_vip_titles(game)
    return changed("已修改 VIP 点数及已满足条件的称号", timing="page")


def gacha_edit(game, args):
    from .gameplay_gacha import gacha_record, gacha_state

    definition = game.catalog.get("m_gachas", args["master_id"])
    record = gacha_record(game, definition)
    group = definition["m_gacha_group_id"]
    target = args.get("step", record["step"])
    step = next((row for row in game.catalog.rows("m_gacha_steps") if row["m_gacha_group_id"] == group and row["step"] == target), None)
    if step is None:
        raise GameplayError("指定卡池阶梯不存在")
    count = args.get("step_count", record["step_count"])
    if step["limit_num"] and count >= step["limit_num"]:
        raise GameplayError("阶梯次数必须低于进入下一阶梯的阈值")
    record.update(step=target, step_count=count, exec_count=args.get("exec_count", record["exec_count"]))
    if "pity" in args:
        gacha_state(game)["pity"][str(group)] = args["pity"]
    return changed("已修改抽卡统计和保底", timing="page")


def gacha_mileage(game, args):
    group = args["group"]
    entries = [row for row in game.catalog.rows("m_gacha_mileage_rewards") if row["group_no"] == group]
    if not entries:
        raise GameplayError("抽卡里程奖励组不存在")
    maximum = len(entries) * game.rules["gacha_mileage_draw_interval"]
    value = number(args["value"], 0, maximum - 1)
    put(game.state, "t_gacha_mileages", {"m_gacha_mileage_reward_group_no": group}, {"mileage": value})
    return changed("已修改抽卡里程，未补发跨越档位的奖励", timing="page")


def mail_create(game, args):
    from .gameplay_presents import PRESENT_CONTENT_TYPES, enqueue, presents

    if args["kind"] not in PRESENT_CONTENT_TYPES:
        raise GameplayError("该奖励类型无法在原生邮箱显示，请使用直接发放", 400, 4007)
    reward_ = {"content_type": args["kind"], "content_id": args["master_id"], "amount": args["amount"]}
    tables = {30: "m_items", 33: "m_limited_items", 40: "m_characters", 41: "m_characters", 45: "m_visitors", 54: "m_event_currencies",
              56: "m_event_boss_tickets", 60: "m_mana_gems", 70: "m_weapons", 80: "m_armors", 90: "m_accessories",
              140: "m_character_skins", 141: "m_weapon_skins", 180: "m_mission_passes", 190: "m_degrees"}
    if args["kind"] in tables:
        definition = game.catalog.get(tables[args["kind"]], args["master_id"])
        if args["kind"] in (30, 33):
            number(args["amount"], 1, definition.get("possession_limit") or MAX_VALUE)
    elif args["master_id"] != 0:
        raise GameplayError("货币或经验邮件的内容 ID 必须为 0", 400, 4007)
    if args["kind"] in (70, 80, 90):
        number(args["amount"], 1, 100)
    elif args["kind"] == 40:
        number(args["amount"], 1, 1000)
    enqueue(game, [reward_], title=args["title"])
    entry = presents(game)[-1]
    entry["message"] = args["message"]
    if args["days"]:
        entry["end_at"] = (game.now + timedelta(days=args["days"])).isoformat()
    return changed("已创建本地邮件", timing="page")


def mail_expire(game, args):
    from .gameplay_presents import presents

    entry = next((row for row in presents(game) if row["id"] == args["mail_id"]), None)
    if entry is None:
        raise GameplayError("邮件不存在")
    if entry["accepted_at"]:
        raise GameplayError("已领取邮件保留为奖励记录，不能撤回")
    entry["end_at"] = game.now.isoformat()
    return changed("已撤回未领取邮件", timing="page")


def login_extend(game, args):
    from .gameplay_login_state import bonus_entry, grant_limited_login

    definition = game.catalog.get("m_login_bonuses", args["master_id"])
    if definition["bonus_type"] not in (91, 92):
        raise GameplayError("仅支持限时登录礼包")
    entry = bonus_entry(game.progress, definition["id"])
    if not entry["paid_at"]:
        grant_limited_login(game.progress, game.catalog, definition["id"], game.now)
    entry["limit_at"] = (game.now + timedelta(days=args["days"])).isoformat()
    return changed("已修改登录礼包期限，领取进度保留", timing="page")


def counter_reset(game, args):
    from .gameplay_shop import shop_data
    from .gameplay_exchange import counter

    identifier = args["master_id"]
    if args["kind"] == "shop":
        game.catalog.get("m_payments", identifier)
        shop_data(game)["counts"].pop(str(identifier), None)
    else:
        definition = game.catalog.get("m_exchange_lineups", identifier)
        counter(game, definition)["count"] = 0
    return changed("已重置当前商品限购计数，凭证保留", timing="page")


CURRENCY_CHOICES = [(10, "免费宝石"), (50, "金币"), (51, "Soul"), (52, "厄灾币"), (53, "友情点"), (110, "角色经验库存")]
REWARD_CHOICES = [*CURRENCY_CHOICES, (20, "本地付费宝石"), (30, "道具"), (33, "限时道具"), (40, "角色"), (41, "角色核心点"),
                  (45, "访客"), (54, "活动币"), (56, "首领券"), (60, "魔晶石"), (70, "武器"), (80, "防具"), (90, "饰品"),
                  (111, "玩家经验"), (120, "计时资源"), (131, "开发计划"), (140, "角色服装"), (141, "武器外观"),
                  (170, "抽卡代币"), (180, "高级通行证"), (181, "通行证点数"), (185, "限时登录礼包"), (186, "月登录礼包"), (190, "称号")]
MODE = lambda: choice("mode", "调整方式", [("set", "设为指定值"), ("add", "增加"), ("subtract", "扣除")], default="set")

OPERATIONS = [
    (spec("currency.balance", "currency", "调整货币余额", [choice("kind", "货币", CURRENCY_CHOICES), MODE(), field("amount", "数量")]), balance),
    (spec("inventory.balance", "inventory", "调整道具与代币数量", [choice("kind", "内容类别", [(30, "道具"), (54, "活动币"), (56, "首领券"), (170, "抽卡代币")]),
        field("master_id", "内容主数据 ID", min=1), MODE(), field("amount", "数量")]), item_balance),
    (spec("inventory.grant", "inventory", "发放内容", [choice("kind", "内容类别", REWARD_CHOICES), field("master_id", "内容主数据 ID", default=0), field("amount", "数量", min=1, default=1)],
        description="使用游戏正常奖励逻辑；重复角色转换为碎片，装备遵守容量限制。", timing="page"), reward),
    (spec("currency.paid_grant", "currency", "发放本地付费宝石", [field("amount", "数量", min=1), field("days", "有效天数", min=1, max=36500, default=180)]), paid_grant),
    (spec("currency.paid_edit", "currency", "修改付费宝石批次", [field("lot_id", "批次", source="t_payment_dmm_currencies", min=1), field("remaining", "剩余数量"), field("days", "距今有效天数", required=False, min=1, max=36500)]), paid_edit),
    (spec("inventory.limited_edit", "inventory", "修改限时物品批次", [field("lot_id", "批次", source="t_limited_items", min=1), field("amount", "数量"), field("days", "距今有效天数", min=1, max=36500)]), limited_edit),
    (spec("supply.set", "currency", "设置体力或玩法次数", [field("type", "计时资源类型", min=1), field("amount", "数量"), field("reset_purchase_count", "同时重置体力购买次数", "boolean", default=False)]), timed),
    (spec("profile.exp", "profile", "设置玩家经验", [field("exp", "累计经验")], description="重算等级与体力上限，不补发升级奖励。"), player_exp),
    (spec("subscription.set", "shop", "设置订阅权益", [field("master_id", "订阅", min=1, source="m_payment_subscriptions"), field("days", "剩余天数（0 为到期）", max=36500), field("renewals", "累计续订次数", min=1, required=False)], timing="page"), subscription),
    (spec("pass.set", "shop", "修改通行证", [field("master_id", "通行证", min=1, source="m_mission_passes"), field("points", "累计点数", required=False), field("premium", "高级权益", "boolean", required=False)], timing="page"), pass_edit),
    (spec("pass.reset_claims", "shop", "重置通行证领奖记录", [field("master_id", "通行证", min=1, source="m_mission_passes"), field("allow_duplicate_rewards", "允许再次领取过去的奖励", "boolean", default=False)], timing="page"), pass_reset_claims),
    (spec("shop.vip", "shop", "设置 VIP 点数", [field("points", "点数")], timing="page"), vip_edit),
    (spec("shop.login_extend", "shop", "设置登录礼包期限", [field("master_id", "登录礼包", min=1, source="m_login_bonuses"), field("days", "剩余天数（0 为到期）", max=36500)], timing="page"), login_extend),
    (spec("shop.reset_counter", "shop", "重置商品限购", [choice("kind", "类别", [("shop", "购买商品"), ("exchange", "兑换商品")]), field("master_id", "商品主数据 ID", min=1)], timing="page"), counter_reset),
    (spec("gacha.set", "gacha", "修改卡池计数与保底", [field("master_id", "卡池", min=1, source="m_gachas"), field("step", "当前阶梯", min=1, required=False),
        field("step_count", "当前阶梯次数", required=False), field("exec_count", "累计抽取次数", required=False), field("pity", "距上次最高稀有度抽取数", required=False)], timing="page"), gacha_edit),
    (spec("gacha.mileage", "gacha", "设置抽卡里程", [field("group", "里程奖励组", min=1), field("value", "当前循环进度")], timing="page"), gacha_mileage),
    (spec("mail.create", "mission", "创建本地奖励邮件", [choice("kind", "奖励类别", REWARD_CHOICES), field("master_id", "内容主数据 ID", default=0),
        field("amount", "数量", min=1, default=1), field("title", "标题", "string", max_length=100), field("message", "正文", "string", max_length=1000, multiline=True, default="本地控制台发放"), field("days", "有效天数（0 为永久）", max=36500, default=0)], timing="page"), mail_create),
    (spec("mail.withdraw", "mission", "撤回未领取邮件", [field("mail_id", "邮件 ID", min=1)], timing="page"), mail_expire),
]
