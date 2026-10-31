import msgpack
import hashlib
from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from dotabyss_ps.gameplay_catalog import GameplayCatalog, available
from dotabyss_ps.event_master import EventMasterView
from dotabyss_ps.masterdata import MasterCatalog
from test_event_core import rows
from test_gameplay import NOW, call, gameplay_master, setup_gameplay
from test_localstate import login
from test_training_encounter_modes import edit, prepare


def version(client, headers):
    response = client.get("/maintenance", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["versions"]["resource"][0]


def active_campaigns(client, revision, schema):
    response = client.get("/data/" + revision)
    assert response.status_code == 200, response.text
    data = msgpack.unpackb(response.content, raw=False)
    return [row["id"] for row in rows(data, schema, "m_encounter_campaigns") if available(row, NOW, "open_at", "close_at")]


def test_campaign_selection_updates_client_master_per_profile_and_survives_restart(setup_gameplay):
    context = setup_gameplay
    prepare(context, chapters=True)
    catalog = GameplayCatalog(context.tables, context.schema)
    first, second = catalog.rows("m_encounter_campaigns")[:2]
    initial = version(context.client, context.headers)
    assert active_campaigns(context.client, initial, context.schema) == [first["id"]]
    selected = context.client.post("/api/idle-exploration/campaign/join", headers=context.headers,
                                   json={"m_encounter_campaign_id": second["id"]})
    assert selected.status_code == 200, selected.text
    changed = selected.json()["versions"]["resource"][0]
    assert changed != initial
    assert active_campaigns(context.client, changed, context.schema) == [second["id"]]
    assert version(context.client, context.headers) == changed
    assert context.client.get("/api/health-check", headers=context.headers).json()["versions"]["resource"] == [changed]
    _, other_headers = login(context.client, "campaign-other-profile")
    assert version(context.client, other_headers) == initial
    assert version(context.client, context.headers) == changed
    with TestClient(create_app(context.settings)) as restarted:
        assert version(restarted, context.headers) == changed
        assert active_campaigns(restarted, changed, context.schema) == [second["id"]]
    assert context.store.master()["revision"] == context.record["revision"]


def test_missing_saved_campaign_is_rejected_without_changing_another_profile(setup_gameplay):
    context = setup_gameplay
    prepare(context, chapters=True)
    first = GameplayCatalog(context.tables, context.schema).rows("m_encounter_campaigns")[0]
    call(context, "api/idle-exploration/campaign/join", {"m_encounter_campaign_id": first["id"]})
    anonymous = version(context.client, {})
    edit(context, lambda _, progress: progress["features"]["encounter"]["data"]["campaigns"].update(current_id=999999999))
    response = context.client.get("/maintenance", headers=context.headers)
    assert response.status_code == 503
    assert "Selected encounter campaign" in response.json()["errors"]["message"]
    assert version(context.client, {}) == anonymous


def test_published_campaign_view_survives_eviction_and_cold_restart(setup_gameplay):
    context = setup_gameplay
    view = EventMasterView(MasterCatalog(context.settings.data_dir, context.store), context.schema,
                           context.settings.data_dir / "gameplay-rules.json")
    campaigns = GameplayCatalog(context.tables, context.schema).rows("m_encounter_campaigns")
    record, payload, _ = view.current(campaigns[0]["id"])
    for campaign in campaigns[1:7]:
        view.current(campaign["id"])
    assert record["revision"] not in view.cache
    assert view.read(record["revision"])[1] == payload
    with TestClient(create_app(context.settings)) as restarted:
        response = restarted.get("/data/" + record["revision"])
        assert response.status_code == 200 and response.content == payload
    saved = context.settings.data_dir / record["payload_path"]
    saved.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
    with TestClient(create_app(context.settings)) as restarted:
        assert restarted.get("/data/" + record["revision"]).status_code == 503
        original = restarted.get("/data/" + context.record["revision"])
        assert original.status_code == 200
        assert hashlib.sha256(original.content).hexdigest() == context.record["sha256"]
