from __future__ import annotations

import copy
import re
from datetime import date, datetime

from .gameplay_catalog import GameplayError, JST, available, identifiers, number
from .gameplay_missions import Missions
from .gameplay_records import delta, find, grant, put, rows
from .newgame import POLICY_VERSION, NewGameError, validate_saved_state
from .protocol import validate_data
from .storage import GameStateError
from .gameplay_characters import CHARACTER_ROUTES, ensure_abilities
from .gameplay_profile import PROFILE_ROUTES
from .gameplay_routes import match_route
from .gameplay_rules import load_rules
from .gameplay_receipts import operation_digest, remember, replay, refresh_replay_records, validate_additional_progress
from .gameplay_equipment import EQUIPMENT_ROUTES, ensure_equipment_appraisal
from .gameplay_presents import PRESENT_ROUTES, present_count, presents
from .gameplay_gacha import GACHA_ROUTES, listing as gacha_listing
from .gameplay_exchange import EXCHANGE_ROUTES, active_lineups, listing as exchange_listing, read_lineups
from .gameplay_items import ITEM_ROUTES
from .gameplay_research import RESEARCH_ROUTES, ensure_tutorial_supplies, ensure_research_rewards
from .gameplay_library import LIBRARY_ROUTES, sync_novels
from .gameplay_party import PARTY_ROUTES
from .gameplay_party_state import ensure_party, validate_layout, write_layout
from .gameplay_party_edit import PARTY_EDIT_ROUTES
from .gameplay_idle import IDLE_ROUTES, world as prepare_idle_world, initialize_assignment, reconcile
from .gameplay_effects import settle_before_effect_change
from .gameplay_skin_bonds import reconcile_skin_bonds
from .gameplay_tavern import TAVERN_ROUTES
from .gameplay_statistics import initialize as initialize_statistics, record_operation
from .gameplay_plans import PLAN_ROUTES, sync_plans
from .gameplay_synchro import SYNCHRO_ROUTES, ensure_synchro
from .gameplay_pass import PASS_ROUTES
from .gameplay_tutorial_gacha import TUTORIAL_GACHA_ROUTES
from .gameplay_mileage import ensure_equipment_mileage
from .gameplay_friends import FRIEND_ROUTES
from .gameplay_shop import SHOP_ROUTES, listing as payment_listing, recommend_view, vip_record, capture_payment_response, ensure_vip_weekly_counters
from .gameplay_login import LOGIN_ROUTES, collect as collect_login
from .gameplay_home import HOME_ROUTES, greetings, visitors, eligible as greeting_eligible, receive as receive_greeting
from .gameplay_stamina import STAMINA_ROUTES, settle_stamina, recovery_record
from .gameplay_quest_state import ensure_nether_state
from .gameplay_repairs import repair_ability_responses
from .gameplay_wallet import expire_paid_lots
from .gameplay_battle_session import BATTLE_ROUTES, replay_response as replay_battle_response
from .gameplay_battle_notifications import battle_notifications
from .gameplay_main_repeat import REPEAT_ROUTES
from .gameplay_training import TRAINING_ROUTES, prepare_training
from .gameplay_union import UNION_ROUTES, prepare_union
from .gameplay_encounter import ENCOUNTER_ROUTES, prepare_encounters
from .gameplay_disaster import DISASTER_ROUTES
from .gameplay_disaster_state import prepare_disaster
from .gameplay_disaster_party import DISASTER_PARTY_ROUTES
from .gameplay_nether import NETHER_ROUTES
from .gameplay_side_story import SIDE_STORY_ROUTES, prepare_side_story, replay_response as replay_side_story_response
from .gameplay_events import EVENT_ROUTES
from .gameplay_event_quests import EVENT_QUEST_ROUTES
from .gameplay_event_board import EVENT_BOARD_ROUTES
from .gameplay_event_box import EVENT_BOX_ROUTES
from .gameplay_event_bonus import EVENT_BONUS_ROUTES
from .gameplay_event_effects import EVENT_EFFECT_ROUTES
from .gameplay_event_miner import MINER_ROUTES
from .gameplay_event_mutant import MUTANT_ROUTES
from .gameplay_encounter_campaign import ENCOUNTER_CAMPAIGN_ROUTES
from .gameplay_event_missions import EVENT_MISSION_ROUTES, receivable_events, replay_response as replay_event_mission_response
from .gameplay_story_progress import prepare_story_state, quest_row, update_sequence
from .gameplay_tutorial import apply_tutorial_policy, complete_feature_tutorial, unlock_feature

EXTRA_ROUTES = {**PROFILE_ROUTES, **CHARACTER_ROUTES, **EQUIPMENT_ROUTES, **PRESENT_ROUTES, **GACHA_ROUTES,
                **EXCHANGE_ROUTES, **ITEM_ROUTES, **RESEARCH_ROUTES, **LIBRARY_ROUTES, **PARTY_ROUTES, **PARTY_EDIT_ROUTES,
                **IDLE_ROUTES, **TAVERN_ROUTES, **PLAN_ROUTES, **SYNCHRO_ROUTES, **PASS_ROUTES, **TUTORIAL_GACHA_ROUTES,
                **FRIEND_ROUTES, **SHOP_ROUTES, **LOGIN_ROUTES, **HOME_ROUTES, **STAMINA_ROUTES, **BATTLE_ROUTES, **REPEAT_ROUTES,
                **TRAINING_ROUTES, **UNION_ROUTES, **ENCOUNTER_ROUTES, **DISASTER_ROUTES, **DISASTER_PARTY_ROUTES, **NETHER_ROUTES,
                **SIDE_STORY_ROUTES, **EVENT_ROUTES, **EVENT_QUEST_ROUTES, **EVENT_BOARD_ROUTES, **EVENT_BOX_ROUTES, **EVENT_BONUS_ROUTES,
                **EVENT_EFFECT_ROUTES, **MINER_ROUTES, **MUTANT_ROUTES, **ENCOUNTER_CAMPAIGN_ROUTES, **EVENT_MISSION_ROUTES}

GAMEPLAY_METHODS = {
    "api/tutorial/feature-exec": "POST", "api/character/new/check": "POST",
    "api/party/exploration/edit": "POST", "api/main-story/update-sequence": "POST",
    "api/main-story/start-battle": "POST", "api/main-story/clear-battle": "POST",
    "api/feature-lock/unlock": "POST", "api/exchange/lineup": "POST", "api/exchange/read/lineup": "POST",
    "api/shop/top": "GET", "api/quest/top": "GET", "api/gacha": "GET", "api/gacha/exec": "POST",
    "api/mission/list": "GET", "api/mission/reward/receive": "POST", "api/special-mission/top": "POST",
    "api/special-mission/mission-reward/receive": "POST", "api/home/daily-greeting/receive-reward": "POST",
    **{path: entry.method for path, entry in EXTRA_ROUTES.items()},
}
BLOCKED_ROUTES = {}


def prepare_progress(progress, now):
    if not isinstance(progress, dict):
        raise GameplayError("Stored gameplay progress is invalid")
    if not progress:
        progress.update(format_version=2, login_days=[], read_lineups=[], mission_claims={}, greeting_claims={}, operation_receipts={}, features={})
    old_fields = {"format_version", "login_days", "read_lineups", "mission_claims", "greeting_claims"}
    if type(progress.get("format_version")) is int and progress["format_version"] == 1 and set(progress) == old_fields:
        progress.update(format_version=2, operation_receipts={}, features={"presents": {"version": 1, "data": {"seeded": True, "entries": []}}})
    if set(progress) != old_fields | {"operation_receipts", "features"} or type(progress["format_version"]) is not int or progress["format_version"] != 2:
        raise GameplayError("Stored gameplay progress requires migration")
    validate_additional_progress(progress)
    days = progress["login_days"]
    if not isinstance(days, list) or len(days) > 10000 or any(not isinstance(d, str) for d in days):
        raise GameplayError("Stored login days are invalid")
    try:
        if len(set(days)) != len(days) or any(date.fromisoformat(d).isoformat() != d for d in days):
            raise ValueError()
    except ValueError as exc:
        raise GameplayError("Stored login days are invalid") from exc
    identifiers(progress["read_lineups"], 10000)
    for field in ("mission_claims", "greeting_claims"):
        if not isinstance(progress[field], dict) or len(progress[field]) > 10000:
            raise GameplayError("Stored reward receipts are invalid")
        for key, rewards in progress[field].items():
            if not isinstance(key, str) or not re.fullmatch(r"(?:once|\d{4}-\d{2}-\d{2}|\d{4}-W\d{2}):[1-9]\d{0,18}", key):
                raise GameplayError("Stored reward receipt key is invalid")
            if not isinstance(rewards, list) or len(rewards) > 1000:
                raise GameplayError("Stored reward receipt is invalid")
            for reward in rewards:
                if not isinstance(reward, dict) or set(reward) != {"content_type", "content_id", "amount"}:
                    raise GameplayError("Stored reward receipt fields are invalid")
                number(reward["content_type"], 1, 2 ** 31 - 1)
                number(reward["content_id"], 0)
                number(reward["amount"], 1, 2 ** 31 - 1)
    day = now.date().isoformat()
    if day not in days:
        days.append(day)


def owned_character(state, identifier):
    row = find(state, "t_characters", id=identifier)
    if row is None:
        raise GameplayError("Character is not owned")
    return row


def is_gameplay_path(path):
    return path in GAMEPLAY_METHODS or match_route(path, EXTRA_ROUTES) is not None


def merge_paid_expiry(response, expired):
    if expired:
        cached = response.setdefault("records", {}).setdefault("t_payment_dmm_currencies", {"up": [], "down": []})
        current = {row["id"]: row for row in cached["up"]}
        current.update({row["id"]: row for row in expired["up"]})
        cached["up"] = list(current.values())
        cached["down"] = [identifier for identifier in cached["down"] if identifier not in current]
    return response


class Gameplay:
    def __init__(self, state, progress, catalog, user_id, now=None, rules=None, community=None):
        self.state, self.progress, self.catalog, self.user_id = state, progress, catalog, user_id
        self.now = (now or datetime.now(JST)).astimezone(JST)
        self.rules = load_rules() if rules is None else rules
        self.community = community
        self.payment_receipt_token = ""

    def prepare(self):
        try:
            validate_data(self.state)
        except (ValueError, TypeError, RecursionError) as exc:
            raise GameStateError("Stored game state is corrupt") from exc
        if not isinstance(self.state, dict) or self.state.get("policy_version") != POLICY_VERSION:
            raise GameStateError("Stored game state requires a supported policy migration")
        try:
            validate_saved_state(self.state)
        except NewGameError as exc:
            raise GameStateError("Stored game state is corrupt: " + str(exc)) from exc
        owners = {row["user_id"] for collection in self.state["records"].values() for row in collection["up"] if "user_id" in row}
        if owners != {self.user_id}:
            raise GameStateError("Stored game state does not belong to the authenticated profile")
        prepare_progress(self.progress, self.now)
        apply_tutorial_policy(self)
        prepare_story_state(self)
        ensure_abilities(self)
        reconcile_skin_bonds(self)
        ensure_equipment_appraisal(self)
        repair_ability_responses(self)
        ensure_research_rewards(self)
        initialize_statistics(self.state, self.progress, self.catalog)
        if "m_stamina_recovery_costs" in self.catalog.tables:
            settle_stamina(self.state, self.catalog, self.user_id, self.now)
            recovery_record(self)
        if "m_level_synchros" in self.catalog.tables:
            ensure_synchro(self)
        if "m_mileage_levels" in self.catalog.tables:
            ensure_equipment_mileage(self)
        if "m_vip_ranks" in self.catalog.tables:
            vip_record(self)
        if "m_plan_restricts" in self.catalog.tables:
            sync_plans(self)
        status = rows(self.state, "t_statuses")[0]
        status["max_party_count"] = max(status["max_party_count"], self.rules["party_slots"])
        if self.rules["unlock_common_features"]:
            for identifier in (1, 2, 6, 7, 8):
                self.catalog.get("m_feature_locks", identifier)
                put(self.state, "t_feature_locks", {"m_feature_lock_id": identifier}, {"is_unlocked": True})
        presents(self)
        if "m_disaster_levels" in self.catalog.tables:
            prepare_disaster(self)
        prepare_training(self)
        prepare_union(self)
        prepare_encounters(self)
        prepare_side_story(self)

    def visit(self, view, home=False, login=False):
        self.prepare()
        previous_visitors = {row["id"] for row in rows(self.state, "t_visitors")}
        expire_paid_lots(self)
        ensure_nether_state(self)
        if self.rules["idle_auto_start"]:
            prepare_idle_world(self)
            initialize_assignment(self)
        if "m_vip_ranks" in self.catalog.tables:
            ensure_vip_weekly_counters(self)
        sync_novels(self)
        ensure_tutorial_supplies(self)
        extra = collect_login(self) if login and "m_login_bonuses" in self.catalog.tables else {}
        if home and "m_home_greeting_rewards" in self.catalog.tables:
            extra.update(daily_greetings=self.daily_greetings(), daily_visitors=visitors(self), available_recommends=recommend_view(self))
        pending_presents = present_count(self) if home else 0
        response = view(self.state)
        removed_visitors = previous_visitors - {row["id"] for row in rows(self.state, "t_visitors")}
        if removed_visitors:
            response["records"] = dict(response["records"])
            response["records"]["t_visitors"] = copy.deepcopy(response["records"]["t_visitors"])
            response["records"]["t_visitors"]["down"] = sorted(removed_visitors)
        response.update(extra)
        if home:
            if "m_missions" in self.catalog.tables:
                response["badges"].update(self._missions().badges())
            response["badges"]["present_count"] = pending_presents
        validate_data(response)
        return response

    def execute(self, route, method, body, operation_id=""):
        extra = match_route(route, EXTRA_ROUTES)
        expected_method = extra[1].method if extra else GAMEPLAY_METHODS.get(route)
        if expected_method != method:
            raise GameplayError("Method is not supported for this endpoint", 405, 4050)
        if route in BLOCKED_ROUTES:
            raise GameplayError(BLOCKED_ROUTES[route], 501, 5010)
        if not isinstance(body, dict):
            raise GameplayError("Request body must be an object", 400, 4007)
        self.prepare()
        before = copy.deepcopy(self.state["records"])
        expire_paid_lots(self)
        expired = delta(before, self.state["records"]).get("t_payment_dmm_currencies")
        digest = operation_digest(route, method, body)
        previous = replay(self.progress, operation_id, digest)
        if previous is not None:
            previous = refresh_replay_records(self.state, previous)
            previous = replay_battle_response(self, route, previous)
            previous = replay_side_story_response(self, route, previous)
            previous = replay_event_mission_response(self, route, previous)
            if expired:
                merge_paid_expiry(previous, expired)
                self.progress["operation_receipts"][operation_id]["response"] = copy.deepcopy(previous)
            return previous
        if "m_vip_ranks" in self.catalog.tables:
            ensure_vip_weekly_counters(self)
        settle_before_effect_change(self)
        handlers = {
            "api/tutorial/feature-exec": (self.tutorial, {"tutorial_type"}),
            "api/character/new/check": (self.character_seen, {"t_character_ids"}),
            "api/party/exploration/edit": (self.party_edit, {"party_no", "characters", "mana_gems"}),
            "api/main-story/update-sequence": (self.story_sequence, {"m_chapter_quest_id", "sequence"}),
            "api/feature-lock/unlock": (self.unlock, {"m_feature_lock_id"}),
            "api/exchange/lineup": (self.exchange, set()),
            "api/exchange/read/lineup": (self.exchange_read, {"m_exchange_lineup_ids"}),
            "api/shop/top": (self.shop, set()), "api/quest/top": (self.quest, set()),
            "api/gacha": (self.gacha, set()), "api/mission/list": (self.mission_list, set()),
            "api/mission/reward/receive": (self.mission_receive, {"m_mission_ids"}),
            "api/special-mission/top": (self.special_top, {"m_special_mission_id"}),
            "api/special-mission/mission-reward/receive": (self.special_receive, {"m_special_mission_id", "m_mission_ids"}),
            "api/home/daily-greeting/receive-reward": (self.greeting_receive, {"m_home_daily_greeting_id", "m_home_visitor_daily_greeting_id"}),
        }
        if extra:
            _, entry, values = extra
            result = entry.handler(self, entry.arguments(body, values))
        else:
            handler, fields = handlers[route]
            if set(body) != fields:
                raise GameplayError("Request fields do not match the endpoint", 400, 4007)
            result = handler(body)
        reconcile(self, settle_pending=False)
        reconcile_skin_bonds(self)
        record_operation(self, route, body, before, result)
        if "m_plan_restricts" in self.catalog.tables:
            sync_plans(self)
        ensure_abilities(self)
        sync_novels(self)
        if "m_disaster_levels" in self.catalog.tables:
            prepare_disaster(self)
        prepare_training(self)
        validate_saved_state(self.state)
        prepare_progress(self.progress, self.now)
        validate_data(self.progress)
        response = {"records": delta(before, self.state["records"]), "notifies": battle_notifications(before, self.state["records"], self.catalog), **result}
        merge_paid_expiry(response, expired)
        validate_data(response)
        capture_payment_response(self, response, refresh_expiry=bool(expired))
        remember(self.progress, operation_id, digest, response)
        return response

    def tutorial(self, body):
        return complete_feature_tutorial(self, body)

    def character_seen(self, body):
        selected = identifiers(body["t_character_ids"], 1000)
        for identifier in selected:
            owned_character(self.state, identifier)["is_new"] = 0
        return {"new_t_character_ids": selected}

    def party_edit(self, body):
        party_no = number(body["party_no"], 1, rows(self.state, "t_statuses")[0]["max_party_count"])
        validate_layout(self, body["characters"], body["mana_gems"])
        record = ensure_party(self, party_no)
        write_layout(self, record, 1, body["characters"], body["mana_gems"])
        return {}

    def _quest_row(self, identifier):
        return quest_row(self, identifier)

    def story_sequence(self, body):
        return update_sequence(self, body)

    def unlock(self, body):
        return unlock_feature(self, body)

    def _lineups(self):
        return active_lineups(self)

    def exchange(self, body):
        return exchange_listing(self, body)

    def exchange_read(self, body):
        return read_lineups(self, body)

    def shop(self, body):
        unseen = any(r["id"] not in self.progress["read_lineups"] for r in self._lineups())
        payment = payment_listing(self, {})
        return {"payment": payment["categories"], "exchange": {"is_new": int(unseen)},
                "t_login_thirty_days_bonuses": payment["t_login_thirty_days_bonuses"], "t_payment_lineup_steps": payment["t_payment_lineup_steps"]}

    def quest(self, body):
        ensure_nether_state(self)
        first = min(self.catalog.rows("m_chapter_quests"), key=lambda r: (r["m_chapter_area_id"], r["progress_order"]))
        self._quest_row(first["id"])
        return {"receivable_event_missions": [{"m_event_id": identifier, "is_receivable_mission": 1} for identifier in receivable_events(self)]}

    def gacha(self, body):
        return gacha_listing(self, body)

    def _missions(self):
        return Missions(self.state, self.progress, self.catalog, self.now, self.user_id, self.rules)

    def mission_list(self, body):
        return self._missions().listing()

    def mission_receive(self, body):
        return self._missions().receive(body["m_mission_ids"])

    def special_top(self, body):
        return self._missions().special_top(number(body["m_special_mission_id"]))

    def special_receive(self, body):
        return self._missions().receive(body["m_mission_ids"], number(body["m_special_mission_id"]))

    def greeting_receive(self, body):
        return receive_greeting(self, body)

    def _greeting_eligible(self, greeting):
        return greeting_eligible(self, greeting)

    def daily_greetings(self):
        return greetings(self)
