from __future__ import annotations

import random

from .gameplay_catalog import GameplayError, number


def targets(effect, workers, game, seed):
    kind, param = effect["target_type"], effect["target_param"]
    work_order = {r["id"]: i for i, r in enumerate(sorted(game.catalog.rows("m_tavern_works"), key=lambda r: r["id"]), 1)}
    result = []
    for worker in workers:
        definition = game.catalog.get("m_characters", worker["character"])
        work = game.catalog.get("m_tavern_works", worker["work"])
        matches = {0: False, 1: True, 2: len(worker["categories"]) >= param, 3: work_order[work["id"]] == param,
                   100: definition["union_type"] == param, 101: param in worker["categories"],
                   102: work["floor"] == param, 103: definition["element_type"] == param}
        if kind not in matches:
            raise GameplayError("Unknown tavern effect target", 503, 5036)
        if matches[kind]:
            result.append(worker)
    if effect["random_num"] > 0:
        result = random.Random(seed + effect["id"]).sample(result, min(len(result), effect["random_num"]))
    return result


def apply_card(game, session, definition, seed):
    for field in ("m_tavern_card_effect_id_1", "m_tavern_card_effect_id_2", "m_tavern_card_effect_id_3"):
        if not definition[field]:
            continue
        effect = game.catalog.get("m_tavern_card_effects", definition[field])
        kind = effect["effect_type"]
        if kind in (100, 101, 102, 103):
            session["reload"] = {"kind": kind, "param": effect["effect_param"]}
            continue
        for worker in targets(effect, session["workers"], game, seed):
            if kind == 10:
                worker["rate"] += effect["effect_param"]
            elif kind == 11:
                worker["flat"] += effect["effect_param"]
            elif kind == 20:
                if effect["effect_param"] not in worker["categories"]:
                    worker["categories"].append(effect["effect_param"])
            else:
                raise GameplayError("Unknown tavern card effect", 503, 5036)


def score_turn(game, session):
    total = 0
    for worker in session["workers"]:
        combo = max(0, len(worker["categories"]) - 1)
        if session["score_rules"].get("tavern_use_client_score_parameters", 0):
            base = (5 * (session["work_level"] + 2) + worker["intimacy_level"]) * 10
            multiplier = (1000, 1200, 2000)[min(combo, 2)]
        else:
            base = session["score_rules"]["tavern_base_score"] * worker["job_level"]
            multiplier = 1000 + combo * session["score_rules"]["tavern_combo_permille"]
        score = (base * (1000 + worker["rate"]) // 1000 + worker["flat"]) * multiplier // 1000
        worker["score"] = number(worker["score"] + score, 0, 2 ** 31 - 1)
        total += score
    return number(total, 0, 2 ** 31 - 1)


def matches_reload(game, session, definition, reload):
    kind, param = reload["kind"], reload["param"]
    if kind == 101:
        categories = {1: (1,), 2: (2,), 3: (3,), 4: (1, 2), 5: (2, 3), 6: (1, 3)}
        return param in categories.get(definition["category"], ())
    selected = []
    for worker in session["workers"]:
        character = game.catalog.get("m_characters", worker["character"])
        work = game.catalog.get("m_tavern_works", worker["work"])
        value = {100: character["union_type"], 102: work["floor"], 103: character["element_type"]}.get(kind)
        if value == param:
            selected.append(worker)
    if not selected:
        return False
    for field in ("m_tavern_card_effect_id_1", "m_tavern_card_effect_id_2", "m_tavern_card_effect_id_3"):
        if definition[field]:
            effect = game.catalog.get("m_tavern_card_effects", definition[field])
            if effect["effect_type"] in (10, 11, 20) and targets({**effect, "random_num": 0}, selected, game, 0):
                return True
    return False


def draw_hand(game, session):
    definitions = [r for r in game.catalog.rows("m_tavern_card_levels") if r["card_level"] == session["card_level"]]
    deck = sorted({r["m_tavern_card_id"] for r in definitions})
    reload = session.pop("reload", None)
    if reload:
        matching = [identifier for identifier in deck
                    if matches_reload(game, session, game.catalog.get("m_tavern_cards", identifier), reload)]
        if matching:
            deck = matching
    if not deck:
        raise GameplayError("Tavern deck is empty", 503, 5036)
    session["hand"] = random.Random(session["seed"] + session["turn"] * 1009 + session["rerolls"]).sample(deck, min(3, len(deck)))
