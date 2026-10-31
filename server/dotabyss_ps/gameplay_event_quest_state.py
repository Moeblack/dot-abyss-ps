from __future__ import annotations

from dataclasses import dataclass

from .gameplay_catalog import GameplayError, identifiers, number
from .gameplay_records import find, put, rows


@dataclass(frozen=True)
class EventQuestKind:
    name: str
    event_type: int
    staged: bool = False

    @property
    def nodes(self):
        return f"m_event_{self.name}_{'stages' if self.staged else 'quests'}"

    @property
    def quests(self):
        return f"m_event_{self.name}_quests"

    @property
    def records(self):
        return f"t_event_{self.name}_{'stages' if self.staged else 'quests'}"

    @property
    def field(self):
        return f"m_event_{self.name}_{'stage' if self.staged else 'quest'}_id"

    @property
    def rewards(self):
        return f"m_event_{self.name}_{'rewards' if self.staged else 'quest_rewards'}"


QUEST_KINDS = {kind.name: kind for kind in (
    EventQuestKind("story", 3, True), EventQuestKind("training", 8, True), EventQuestKind("hunt", 5),
    EventQuestKind("commission", 7), EventQuestKind("speedrun", 9),
)}


def quest_data(game, event_id):
    from .gameplay_events import event_data

    data = event_data(game, event_id).setdefault("quests", {})
    if not isinstance(data, dict) or len(data) > 10000:
        raise GameplayError("Stored event quest progress is invalid")
    return data


def saved_node(game, kind, node):
    data = quest_data(game, node["m_event_id"])
    key = f"{kind.name}:{node['id']}"
    if key not in data:
        native = find(game.state, kind.records, **{kind.field: node["id"]})
        cleared = native is not None and native["status"] == 99
        data[key] = {"clears": int(cleared), "first_claimed": cleared, "party_no": 1, "party_characters": []}
    entry = data[key]
    if not isinstance(entry, dict) or set(entry) != {"clears", "first_claimed", "party_no", "party_characters"}:
        raise GameplayError("Stored event quest state is invalid")
    number(entry["clears"], 0, 2 ** 31 - 1)
    number(entry["party_no"], 0, 10)
    identifiers(entry["party_characters"], 10)
    if type(entry["first_claimed"]) is not bool:
        raise GameplayError("Stored event quest reward flag is invalid")
    return entry


def node_record(game, kind, identifier):
    return find(game.state, kind.records, **{kind.field: identifier})


def prerequisite_ids(game, kind, node):
    if kind.staged or kind.name == "commission":
        field = f"release_condition_m_event_{kind.name}_{'stage' if kind.staged else 'quest'}_ids"
        value = node[field]
        if not isinstance(value, str):
            raise GameplayError("Event prerequisite list is invalid", 503, 5036)
        try:
            return identifiers([int(part) for part in value.split(",") if part], 100)
        except ValueError as error:
            raise GameplayError("Event prerequisite identifiers are invalid", 503, 5036) from error
    result = []
    for condition in game.catalog.rows(f"m_event_{kind.name}_quest_restricts"):
        if condition[kind.field] != node["id"]:
            continue
        if kind.name == "hunt":
            result.append(condition["m_event_hunt_quest_restrict_id"])
        else:
            if condition["restrict_type"] != 1:
                raise GameplayError("Unsupported event quest restriction", 503, 5036)
            result.append(condition["restrict_param_1"])
    return result


def released(game, kind, node):
    from .gameplay_events import event_available

    field = "released_at" if kind.name == "commission" else "open_at"
    if not event_available(game, node["m_event_id"], node, start=field):
        return False
    for identifier in prerequisite_ids(game, kind, node):
        prerequisite = game.catalog.get(kind.nodes, identifier)
        if prerequisite["m_event_id"] != node["m_event_id"]:
            raise GameplayError("Event prerequisite points to another event", 503, 5036)
        record = node_record(game, kind, identifier)
        if record is None or record["status"] != 99:
            return False
    return True


def expose_novel(game, identifier):
    from .gameplay_library import read_receipts

    game.catalog.get("m_novel_events", identifier)
    if find(game.state, "t_novel_events", m_novel_event_id=identifier) is None:
        unread = int(f"2:{identifier}" not in read_receipts(game))
        put(game.state, "t_novel_events", {"m_novel_event_id": identifier}, {"is_unread": unread}, game.user_id)


def release_novels(game, kind, event):
    from .gameplay_events import event_available

    before = {row["m_novel_event_id"] for row in rows(game.state, "t_novel_events")}
    if kind.staged:
        for novel in game.catalog.rows(f"m_event_{kind.name}_novels"):
            node = game.catalog.get(kind.nodes, novel[kind.field])
            if node["m_event_id"] != event["id"]:
                continue
            record = node_record(game, kind, node["id"])
            if record is not None and record["status"]:
                expose_novel(game, novel["m_novel_event_id"])
    elif kind.name == "hunt":
        ordinary = sorted((row for row in game.catalog.rows(kind.nodes) if row["m_event_id"] == event["id"] and row["type"] == 2),
                          key=lambda row: row["id"])
        for novel in game.catalog.rows("m_event_hunt_novel_restricts"):
            if novel["m_event_id"] != event["id"] or not event_available(game, event["id"], novel):
                continue
            if novel["restrict_type"] == 1:
                first = node_record(game, kind, ordinary[0]["id"]) if ordinary else None
                if first is None or first["status"] != 99:
                    continue
            elif novel["restrict_type"] != 0:
                raise GameplayError("Unsupported event novel release condition", 503, 5036)
            expose_novel(game, novel["m_novel_event_id"])
    elif kind.name == "speedrun" and "m_event_speedrun_novel_restricts" in game.catalog.tables:
        for rule in game.catalog.rows("m_event_speedrun_novel_restricts"):
            if rule["m_event_id"] != event["id"]:
                continue
            if rule["restrict_type"] != 1 or rule["restrict_param_2"] != 0:
                raise GameplayError("Unsupported speedrun novel release condition", 503, 5036)
            target = game.catalog.get(kind.nodes, rule["restrict_param_1"])
            novel = game.catalog.get("m_novel_events", rule["m_novel_event_id"])
            if target["m_event_id"] != event["id"] or novel["m_event_id"] != event["id"]:
                raise GameplayError("Speedrun novel release refers to another event", 503, 5036)
            record = node_record(game, kind, target["id"])
            if record is not None and record["status"] == 99 and event_available(game, event["id"], novel):
                expose_novel(game, novel["id"])
    return sorted({row["m_novel_event_id"] for row in rows(game.state, "t_novel_events")} - before)


def prepare_event_quests(game, event):
    kind = next((kind for kind in QUEST_KINDS.values() if kind.event_type == event["type"]), None)
    if kind is None or kind.nodes not in game.catalog.tables:
        return []
    if kind.name == "training":
        from .gameplay_events import event_supply

        event_supply(game, event["id"])
    for node in game.catalog.rows(kind.nodes):
        if node["m_event_id"] != event["id"]:
            continue
        saved_node(game, kind, node)
        record = node_record(game, kind, node["id"])
        if record is None:
            values = {"status": 0}
            if kind.name != "speedrun":
                values.update(skip_extra_drop_probability=0, skip_additional_drop_rate=0, skip_party_characters=[])
            if kind.name == "hunt":
                values["open_at"] = node["open_at"]
            if kind.name == "commission":
                values.update(clear_time=0, last_cleared_at="")
            if kind.name == "speedrun":
                values.update(clear_time=0, created_at=game.now.strftime("%Y-%m-%d %H:%M:%S"), updated_at=game.now.isoformat())
            record = put(game.state, kind.records, {kind.field: node["id"]}, values,
                         game.user_id if kind.name in ("commission", "speedrun") else None)
        if record["status"] == 0 and released(game, kind, node):
            record["status"] = 1
    return release_novels(game, kind, event)


def selected_node(game, kind, identifier, *, battle=False):
    from .gameplay_events import require_event

    node = game.catalog.get(kind.nodes, number(identifier))
    event = require_event(game, node["m_event_id"], (kind.event_type,))
    prepare_event_quests(game, event)
    record = node_record(game, kind, node["id"])
    if record is None or not record["status"] or not released(game, kind, node):
        raise GameplayError("Event quest is not unlocked")
    if kind.staged and (node["type"] == 1) == battle:
        raise GameplayError("Event stage does not match this endpoint")
    definition = node
    if kind.staged and battle:
        candidates = [row for row in game.catalog.rows(kind.quests) if row[kind.field] == node["id"]]
        if len(candidates) != 1:
            raise GameplayError("Event stage does not have one battle definition", 503, 5036)
        definition = candidates[0]
    return event, node, definition, record
