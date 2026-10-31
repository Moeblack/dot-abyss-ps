from __future__ import annotations

from .gameplay_catalog import available
from .gameplay_records import rows


def ensure_nether_state(game):
    if rows(game.state, "t_nethers") or "m_nethers" not in game.catalog.tables:
        return
    definitions = [row for row in game.catalog.rows("m_nethers")
                   if available(row, game.now, "open_at", "close_at")]
    if not definitions:
        return
    from .gameplay_nether_state import ensure

    ensure(game)
