from __future__ import annotations

import random

from .gameplay_catalog import GameplayError, number
from .gameplay_economy import consume


def event_state(game, event_id, name, kinds, *, allow_closed=True):
    from .gameplay_events import event_data, require_event

    event_id = number(event_id)
    definition = require_event(game, event_id, kinds, allow_closed=allow_closed)
    state = event_data(game, event_id).setdefault(name, {})
    if not isinstance(state, dict):
        raise GameplayError("Stored event reward progress is invalid")
    return definition, state


def currency(game, event_id):
    matches = [row for row in game.catalog.rows("m_event_currency_relations") if row["m_event_id"] == event_id]
    if len(matches) != 1:
        raise GameplayError("Event currency binding is ambiguous", 503, 5036)
    return matches[0]["m_event_currency_id"]


def spend_currency(game, event_id, amount):
    consume(game, [{"content_type": 54, "content_id": currency(game, event_id), "amount": number(amount)}])


def randomizer(game, event_id, kind, *parts):
    from .gameplay_events import event_data

    return random.Random(":".join(map(str, (kind, game.user_id, event_id, event_data(game, event_id)["epoch"], *parts))))
