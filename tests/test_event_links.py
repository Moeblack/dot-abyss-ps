import copy
import json
from types import SimpleNamespace

import pytest

from dotabyss_ps.gameplay import Gameplay
from dotabyss_ps.gameplay_event_battle_link import battle_event_rewards
from dotabyss_ps.gameplay_event_bonus import bonus_record, trigger_bonus

from dotabyss_ps.gameplay_catalog import GameplayCatalog
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_main_repeat import prepare_repeat
from test_side_story import advance, clear_stage, ordered_stages
from test_training_encounter_modes import edit, prepare, records


def currency_balance(context, identifier):
    return next((row["amount"] for row in records(context)["t_event_currencies"]["up"] if row["m_event_currency_id"] == identifier), 0)


def prime_bonus(context, event_id):
    call(context, "api/event/join", {"m_event_id": event_id})
    edit(context, lambda state, _: next(row for row in state["records"]["t_event_bonus_quests"]["up"] if row["m_event_id"] == event_id).update(probability=1000))


@pytest.mark.parametrize("stamina,amount", [(40, 30), (41, 31), (1, 1)])
def test_passive_currency_uses_native_boost_rate_and_rounds_up(setup_gameplay, stamina, amount):
    context = setup_gameplay
    prepare_repeat(context)
    prime_bonus(context, 600002)
    state, progress = map(json.loads, snapshot(context))
    game = Gameplay(state, progress, GameplayCatalog(context.tables, context.schema), context.identity["user_id"], NOW)
    rewards = battle_event_rewards(game, {"kind": "repeat", "stamina_spent": stamina, "party_no": 1, "party_characters": []})
    assert len(rewards) == 1 and rewards[0]["content_type"] == 54
    assert rewards[0]["content_id"] == 6001 and rewards[0]["amount"] == amount


def test_passive_currency_requires_a_matching_stamina_rate(setup_gameplay):
    context = setup_gameplay
    prepare_repeat(context)
    prime_bonus(context, 600002)
    state, progress = map(json.loads, snapshot(context))
    catalog = GameplayCatalog(context.tables, context.schema)
    catalog.tables["m_event_stamina_drop_boost_rates"] = {}
    game = Gameplay(state, progress, catalog, context.identity["user_id"], NOW)
    assert battle_event_rewards(game, {"kind": "repeat", "stamina_spent": 40, "party_no": 1}) == []


@pytest.mark.parametrize("members,roll,appears", [([], 100, False), ([1300035], 100, True), ([1300035, 1300035], 300, False)])
def test_bonus_character_rate_changes_only_the_roll_threshold(setup_gameplay, monkeypatch, members, roll, appears):
    context = setup_gameplay
    prepare_repeat(context)
    prime_bonus(context, 600002)
    state, progress = map(json.loads, snapshot(context))
    game = Gameplay(state, progress, GameplayCatalog(context.tables, context.schema), context.identity["user_id"], NOW)
    record = bonus_record(game, 600002)
    record["probability"] = 0
    definition = next(row for row in game.catalog.rows("m_event_bonus_quests") if row["m_event_id"] == 600002)
    monkeypatch.setattr("dotabyss_ps.gameplay_event_bonus.randomizer", lambda *args: SimpleNamespace(randrange=lambda _: roll, choice=lambda items: items[0]))
    result = trigger_bonus(game, chapter_id=1, difficulty=1, party_characters=members)
    assert bool(result) is appears
    assert record["probability"] == (definition["default_probability"] if appears else definition["increase_probability"])


def test_repeat_earns_event_currency_triggers_bonus_and_funds_box_once(setup_gameplay):
    context = setup_gameplay
    repeat = prepare_repeat(context)
    catalog = GameplayCatalog(context.tables, context.schema)
    event_id = 600002
    prime_bonus(context, event_id)
    currency = next(row["m_event_currency_id"] for row in catalog.rows("m_event_currency_relations") if row["m_event_id"] == event_id)
    tokens = copy.deepcopy(records(context)["t_gacha_tokens"])
    count = max(1, (20 + repeat["stamina"] - 1) // repeat["stamina"])
    body = {"m_chapter_area_id": repeat["m_chapter_area_id"], "skip_count": count}
    headers = {**context.headers, "x-request-id": "repeat-event-income"}
    result = call(context, "api/main-story/repeat/skip-battle", body, headers=headers)
    income = sum(row["amount"] for row in result["event_drops"] if row["content_type"] == 54 and row["content_id"] == currency)
    assert income >= 10 and currency_balance(context, currency) == income
    assert result["notifies"]["event_bonus_quest_occurred"]
    before = snapshot(context)
    call(context, "api/main-story/repeat/skip-battle", body, headers=headers)
    assert snapshot(context) == before
    call(context, "api/event/box-gacha/exec", {"m_event_id": event_id, "exec_count": 1})
    assert currency_balance(context, currency) == income - 10
    assert records(context)["t_gacha_tokens"] == tokens


def test_side_story_clear_and_skip_preserve_event_rewards_and_bonus_binding(setup_gameplay):
    context = setup_gameplay
    prepare(context, chapters=True)
    advance(context, 11)
    catalog = GameplayCatalog(context.tables, context.schema)
    final = ordered_stages(context)[-1]
    mapping = next(row for row in catalog.rows("m_event_bonus_quest_chapters") if row["m_side_story_stage_id"] == final["id"])
    event_id = catalog.get("m_event_bonus_quests", mapping["m_event_bonus_quest_id"])["m_event_id"]
    prime_bonus(context, event_id)
    result = clear_stage(context, final)
    assert result["event_drops"] and result["notifies"]["event_bonus_quest_occurred"]
    pending = copy.deepcopy(records(context)["t_event_bonus_quests"])
    skipped = call(context, "api/side-story/skip-quest", {"m_side_story_stage_id": final["id"], "skip_count": 1})
    assert skipped["event_drops"] and not skipped["first_rewards"]
    assert records(context)["t_event_bonus_quests"] == pending


def test_closed_event_stops_passive_income_without_resetting_joined_state(setup_gameplay):
    context = setup_gameplay
    repeat = prepare_repeat(context)
    prime_bonus(context, 600002)
    joined = copy.deepcopy(records(context)["t_events"])
    (context.settings.data_dir / "gameplay-rules.json").write_text(json.dumps({"event_open_all": 0}), encoding="utf-8")
    result = call(context, "api/main-story/repeat/skip-battle", {"m_chapter_area_id": repeat["m_chapter_area_id"], "skip_count": 1})
    assert result["event_drops"] == []
    assert records(context)["t_events"] == joined
