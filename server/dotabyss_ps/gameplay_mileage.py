from __future__ import annotations

from .gameplay_catalog import GameplayError, number
from .gameplay_delivery import deliver
from .gameplay_records import find, put, rows


def ensure_equipment_mileage(game):
    record = find(game.state, "t_equipment_mileages", user_id=game.user_id)
    if record is None:
        record = put(game.state, "t_equipment_mileages", {}, {field: 0 for field in (
            "weapon_mileage", "armor_mileage", "accessory_mileage", "loop_weapon_mileage", "loop_armor_mileage", "loop_accessory_mileage")}, game.user_id)
    return record


def group_rewards(game, group):
    numbers = {r["reward_no"] for r in game.catalog.rows("m_mileage_reward_groups") if r["group_no"] == group}
    rewards = [{key: r[key] for key in ("content_type", "content_id", "amount")} for r in game.catalog.rows("m_mileage_rewards") if r["reward_no"] in numbers]
    if not rewards:
        raise GameplayError("Equipment mileage rewards are missing", 503, 5036)
    return rewards


def add_equipment_mileage(game, amounts):
    record = ensure_equipment_mileage(game)
    rewards = []
    for singular, amount in amounts.items():
        number(amount, 0)
        normal = sorted(game.catalog.rows(f"m_{singular}_mileage_rewards"), key=lambda r: r["mileage"])
        loops = sorted(game.catalog.rows(f"m_loop_{singular}_mileage_rewards"), key=lambda r: r["mileage"])
        if not normal or not loops or loops[-1]["mileage"] <= 0:
            raise GameplayError("Equipment mileage thresholds are missing", 503, 5036)
        field, loop_field = singular + "_mileage", "loop_" + singular + "_mileage"
        old = record[field]
        target = min(normal[-1]["mileage"], old + amount)
        for step in normal:
            if old < step["mileage"] <= target:
                rewards.extend(group_rewards(game, step["group_no"]))
        remainder = max(0, old + amount - normal[-1]["mileage"])
        record[field] = target
        if remainder:
            old_loop = record[loop_field]
            loop_target = old_loop + remainder
            period = loops[-1]["mileage"]
            cycles = loop_target // period
            for step in loops:
                claims = max(0, (loop_target - step["mileage"]) // period + 1) - int(old_loop >= step["mileage"])
                if claims:
                    rewards.extend({**r, "amount": number(r["amount"] * claims, 1, 2 ** 31 - 1)} for r in group_rewards(game, step["group_no"]))
            record[loop_field] = loop_target - cycles * period
    return deliver(game, rewards)


def add_gacha_mileage(game, group, draws):
    if not group:
        return []
    steps = sorted([r for r in game.catalog.rows("m_gacha_mileage_rewards") if r["group_no"] == group], key=lambda r: r["display_order"])
    if not steps:
        raise GameplayError("Gacha mileage rewards are missing", 503, 5036)
    record = find(game.state, "t_gacha_mileages", m_gacha_mileage_reward_group_no=group)
    if record is None:
        record = put(game.state, "t_gacha_mileages", {"m_gacha_mileage_reward_group_no": group}, {"mileage": 0})
    before = record["mileage"]
    period = len(steps) * game.rules["gacha_mileage_draw_interval"]
    target = before + number(draws)
    rewards = []
    for i, step in enumerate(steps, 1):
        threshold = i * game.rules["gacha_mileage_draw_interval"]
        times = max(0, (target - threshold) // period + 1) - int(before >= threshold)
        if times:
            rewards.append({"content_type": step["content_type"], "content_id": step["content_id"], "amount": step["amount"] * times})
    record["mileage"] = target % period
    received, mailed = deliver(game, rewards)
    return received + mailed
