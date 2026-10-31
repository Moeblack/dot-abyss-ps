from datetime import timedelta

from dotabyss_ps.gameplay_catalog import GameplayCatalog
from dotabyss_ps.gameplay_records import put
from test_economy_features import give, update
from test_gameplay import NOW, call, gameplay_master, setup_gameplay
from test_idle_features import initialize
from test_permanent_features import records


def test_subscription_bonus_rounds_each_tick_and_expires_at_boundary(setup_gameplay, monkeypatch):
    c = setup_gameplay
    initialize(c)
    update(c, lambda s, p: put(s, "t_payment_subscription_benefits", {"m_payment_subscription_id": 20001}, {
        "expire_at": (NOW + timedelta(seconds=1800)).isoformat(), "continue_count": 1,
        "is_notify_grace_period": False, "is_notify_expired": False}, c.identity["user_id"]))
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(seconds=1800))
    reply = call(c, "api/idle-exploration/receive")
    assert reply["grant_gold"] == 171 + 166
    assert reply["grant_character_exp"] == 215 + 208


def test_subscription_purchase_settles_old_income_before_new_bonuses(setup_gameplay, monkeypatch):
    c = setup_gameplay
    initialize(c)
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(seconds=900))
    call(c, "api/payment-subscription/verify", {"m_payment_id": 20001})
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(seconds=1800))
    reply = call(c, "api/idle-exploration/receive")
    catalog = GameplayCatalog(c.tables, c.schema)
    rank = max(r["rank"] for r in catalog.rows("m_vip_ranks") if r["total_point"] <= 800)
    bonus = sum(r["bonus_param"] for r in catalog.rows("m_vip_rank_bonuses") if r["rank"] <= rank and r["bonus_type"] == 4) + 30
    assert reply["grant_gold"] == 166 + 166 + (166 * bonus + 999) // 1000


def test_research_unlocks_idle_party_capacity_and_mileage_bonus(setup_gameplay):
    c = setup_gameplay
    call(c, "api/idle-exploration", {"sequence": ""})
    def research(state, progress):
        put(state, "t_building_skills", {"m_building_id": 100, "skill_type": 1005}, {"skill_level": 1, "checked_skill_level": 0}, c.identity["user_id"])
        put(state, "t_building_skills", {"m_building_id": 300, "skill_type": 3003}, {"skill_level": 2, "checked_skill_level": 0}, c.identity["user_id"])
    update(c, research)
    call(c, "api/party/idle-exploration/edit", {"party_no": 2, "characters": [{"position_no": 1, "t_character_id": 1, "t_weapon_id": 0}]})
    assert {r["party_no"] for r in records(c)["t_idle_exploration_parties"]["up"]} == {1, 2}
    give(c, [{"content_type": 70, "content_id": 11010110, "amount": 1}])
    identifier = records(c)["t_weapons"]["up"][0]["id"]
    call(c, "api/equipment/disassembly/execute", {"t_weapon_ids": [identifier], "t_armor_ids": [], "t_accessory_ids": []})
    assert records(c)["t_equipment_mileages"]["up"][0]["weapon_mileage"] == 102


def test_vip_time_bonus_and_title_unlock_match_master_thresholds(setup_gameplay):
    c = setup_gameplay
    call(c, "api/payment/grant/free", {"m_payment_id": 80001})
    call(c, "api/idle-exploration", {"sequence": ""})
    catalog = GameplayCatalog(c.tables, c.schema)
    points = records(c)["t_vip_ranks"]["up"][0]["total_point"]
    rank = max(r["rank"] for r in catalog.rows("m_vip_ranks") if r["total_point"] <= points)
    extra = sum(r["bonus_param"] for r in catalog.rows("m_vip_rank_bonuses") if r["rank"] <= rank and r["bonus_type"] == 2)
    assert records(c)["t_idle_exploration_statuses"]["up"][0]["explorable_duration"] == 43200 + extra
    titles = {r["bonus_param"] for r in catalog.rows("m_vip_rank_bonuses") if r["rank"] <= rank and r["bonus_type"] == 7}
    assert titles <= {r["m_degree_id"] for r in records(c)["t_degrees"]["up"]}
