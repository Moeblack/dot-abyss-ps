from __future__ import annotations

from .gameplay_catalog import number
from .gameplay_records import rows, find, grant
from .gameplay_wallet import timestamp


def research_effects(game, kind):
    if "m_building_skill_params" not in game.catalog.tables:
        return []
    acquired = {(r["m_building_id"], r["skill_type"]): r["skill_level"] for r in rows(game.state, "t_building_skills")}
    levels = {r["id"] for r in game.catalog.rows("m_building_skill_levels") if r["skill_level"] <= acquired.get((r["m_building_id"], r["skill_type"]), 0)}
    return [r for r in game.catalog.rows("m_building_skill_params") if r["m_building_skill_level_id"] in levels and r["param_type"] == kind]


def vip_bonus(game, kind):
    if "m_vip_ranks" not in game.catalog.tables or "m_vip_rank_bonuses" not in game.catalog.tables:
        return 0
    points = max((r["total_point"] for r in rows(game.state, "t_vip_ranks")), default=0)
    rank = max((r["rank"] for r in game.catalog.rows("m_vip_ranks") if r["total_point"] <= points), default=0)
    return sum(r["bonus_param"] for r in game.catalog.rows("m_vip_rank_bonuses") if r["rank"] <= rank and r["bonus_type"] == kind)


def grant_vip_titles(game):
    points = max((r["total_point"] for r in rows(game.state, "t_vip_ranks")), default=0)
    rank = max((r["rank"] for r in game.catalog.rows("m_vip_ranks") if r["total_point"] <= points), default=0)
    for bonus in game.catalog.rows("m_vip_rank_bonuses"):
        if bonus["bonus_type"] == 7 and bonus["rank"] <= rank and find(game.state, "t_degrees", m_degree_id=bonus["bonus_param"]) is None:
            grant(game.state, [{"content_type": 190, "content_id": bonus["bonus_param"], "amount": 1}], game.catalog, game.user_id, game.now, game.progress)


def subscription_bonus(game, kind, at=None):
    if "m_payment_subscription_effects" not in game.catalog.tables:
        return 0
    at = game.now if at is None else at
    groups = {game.catalog.get("m_payment_subscriptions", r["m_payment_subscription_id"])["m_payment_subscription_effect_group_id"]
              for r in rows(game.state, "t_payment_subscription_benefits") if timestamp(r["expire_at"]) > at}
    return sum(r["bonus_param"] for r in game.catalog.rows("m_payment_subscription_effects") if r["group_id"] in groups and r["effect_type"] == kind)


def boosted(amount, permille):
    number(amount, 0, 2 ** 31 - 1)
    number(permille, 0, 1000000)
    return number(amount + (amount * permille + 999) // 1000, 0, 2 ** 31 - 1)


def idle_capacity(game):
    return min(10, game.rules["idle_party_slots"] + sum(r["param_1"] for r in research_effects(game, 1100)))


def extra_drop_bonus(game, at=None):
    return min(1000, vip_bonus(game, 1) + subscription_bonus(game, 3, at) + sum(r["param_1"] for r in research_effects(game, 100)))


def idle_reward_amount(game, kind, amount, at=None):
    types = {110: (3, 4), 50: (4, 5), 30: (5, 6)}
    if kind not in types:
        return amount
    vip_kind, subscription_kind = types[kind]
    return boosted(amount, vip_bonus(game, vip_kind) + subscription_bonus(game, subscription_kind, at))


def settle_before_effect_change(game):
    if rows(game.state, "t_idle_exploration_party_relations"):
        from .gameplay_idle import settle
        settle(game)
