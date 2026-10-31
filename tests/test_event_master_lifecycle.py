from fastapi.testclient import TestClient

from dotabyss_ps.app import create_app
from dotabyss_ps.event_master import EventMasterView
from dotabyss_ps.gameplay_catalog import GameplayCatalog
from dotabyss_ps.masterdata import MasterCatalog
from test_gameplay import gameplay_master, setup_gameplay


def test_campaign_views_share_unchanged_master_rows_and_release_cache(setup_gameplay):
    context = setup_gameplay
    view = EventMasterView(MasterCatalog(context.settings.data_dir, context.store), context.schema,
                           context.settings.data_dir / "gameplay-rules.json")
    campaigns = GameplayCatalog(context.tables, context.schema).rows("m_encounter_campaigns")
    first = view.current(campaigns[0]["id"])[2]
    second = view.current(campaigns[1]["id"])[2]
    assert first["m_items"] is second["m_items"] is view.base_tables["m_items"]
    assert first["m_encounter_campaigns"] is not second["m_encounter_campaigns"]
    assert view.base_tables["m_events"] == context.tables["m_events"]
    view.clear()
    assert view.base_tables is view.base_payload is view.base_record is None
    assert not view.views and not view.cache


def test_application_shutdown_releases_event_master_views(setup_gameplay, monkeypatch):
    context = setup_gameplay
    released = []
    original = EventMasterView.clear

    def observe(view):
        original(view)
        released.append(view)

    monkeypatch.setattr(EventMasterView, "clear", observe)
    with TestClient(create_app(context.settings)) as client:
        assert client.get("/maintenance").status_code == 200
    assert released
    assert all(view.base_tables is None and not view.views and not view.cache for view in released)
