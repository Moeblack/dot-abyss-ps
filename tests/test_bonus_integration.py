import json
from datetime import timedelta

import pytest

from dotabyss_ps.admin_service import AdminError
from dotabyss_ps.gameplay_catalog import GameplayCatalog
from test_admin_console import apply, headers, preview
from test_economy_features import update
from test_gameplay import NOW, call, gameplay_master, setup_gameplay, snapshot
from test_idle_features import initialize


@pytest.fixture
def context(setup_gameplay):
    context = setup_gameplay
    call(context, "api/user")

    def legacy(state, progress):
        state["records"]["t_prologue_tutorials"]["up"][0]["status"] = 99
        progress["features"]["tutorial"]["data"]["mode"] = "legacy"

    update(context, legacy)
    return context


def edit(context, operation, args):
    return apply(context, preview(context, operation, args)["ticket"])


def move_time(monkeypatch, seconds):
    monkeypatch.setattr("test_gameplay.NOW", NOW + timedelta(seconds=seconds))


def state(context):
    return json.loads(snapshot(context)[0])


def progress(context):
    return json.loads(snapshot(context)[1])


def pending_gold(context):
    return sum(row["amount"] for row in progress(context)["features"]["idle"]["data"]["pending"]
               if row["content_type"] == 50)


def test_global_configuration_preserves_unsupported_old_profile(context):
    current = state(context)
    current["policy_version"] = 0
    with context.store.connection() as db:
        db.execute("UPDATE game_states SET contents=? WHERE profile_id=?", (json.dumps(current), context.identity["user_id"]))
    before = snapshot(context)
    rules = context.client.get("/admin/rules", headers=headers(context)).json()
    proposal = context.client.post("/admin/rules/preview", headers=headers(context), json={
        "changes": {"local_vip_points_per_price": 2}, "expected_version": rules["version"]})
    assert proposal.status_code == 200, proposal.text
    assert any("迁移" in warning for warning in proposal.json()["warnings"])
    applied = context.client.post("/admin/rules/apply", headers=headers(context), json={"ticket": proposal.json()["ticket"]})
    assert applied.status_code == 200, applied.text
    assert snapshot(context) == before


def master_preview(context, table, predicate, changes):
    response = context.client.get(f"/admin/master/table/{table}?limit=500", headers=headers(context))
    assert response.status_code == 200, response.text
    data = response.json()
    row = next(row for row in data["rows"] if predicate(row["values"]))
    response = context.client.post("/admin/master/preview", headers=headers(context), json={
        "table": table, "index": row["index"], "changes": changes, "expected_version": data["version"]})
    assert response.status_code == 200, response.text
    return response.json()


def config_apply(context, kind, ticket, expected=200):
    response = context.client.post(f"/admin/{kind}/apply", headers=headers(context), json={"ticket": ticket})
    assert response.status_code == expected, response.text
    return response.json()


def gold_master_preview(context, amount):
    return master_preview(context, "m_vip_rank_bonuses", lambda row: row["bonus_type"] == 4 and row["rank"] == 2,
                          {"bonus_param": amount})


def rules_preview(context, duration):
    current = context.client.get("/admin/rules", headers=headers(context)).json()
    response = context.client.post("/admin/rules/preview", headers=headers(context), json={
        "changes": {"idle_max_seconds": duration}, "expected_version": current["version"]})
    assert response.status_code == 200, response.text
    return response.json()


def config_files(context):
    service = context.client.app.state.admin
    return tuple(path.read_bytes() if path.exists() else None for path in (service.rules_path, service.overrides_path))


def test_master_bonus_change_settles_old_income_before_publishing(context, monkeypatch):
    edit(context, "shop.vip", {"points": 9000})
    initialize(context)
    move_time(monkeypatch, 900)
    before = snapshot(context)
    proposal = gold_master_preview(context, 1000)
    assert snapshot(context) == before
    result = config_apply(context, "master", proposal["ticket"])
    assert pending_gold(context) == 168
    assert config_apply(context, "master", proposal["ticket"])["revision"] == result["revision"]
    assert pending_gold(context) == 168
    assert call(context, "api/idle-exploration/receive")["grant_gold"] == 168
    move_time(monkeypatch, 1800)
    assert call(context, "api/idle-exploration/receive")["grant_gold"] == 332


def test_apply_uses_commit_time_instead_of_preview_time_and_retries_once(context, monkeypatch):
    initialize(context)
    move_time(monkeypatch, 1799)
    before = snapshot(context)
    proposal = preview(context, "shop.vip", {"points": 9000})
    assert snapshot(context) == before
    move_time(monkeypatch, 1919)
    result = apply(context, proposal["ticket"])
    assert pending_gold(context) == 332
    stable = snapshot(context)
    move_time(monkeypatch, 2000)
    assert apply(context, proposal["ticket"])["revision"] == result["revision"]
    assert snapshot(context) == stable
    assert call(context, "api/idle-exploration/receive")["grant_gold"] == 332
    move_time(monkeypatch, 2700)
    assert call(context, "api/idle-exploration/receive")["grant_gold"] == 168


def test_research_capacity_reduction_withdraws_and_keeps_earned_rewards(context, monkeypatch):
    args = {"building_id": 100, "skill_type": 1005, "skill_level": 1}
    edit(context, "research.edit", args)
    call(context, "api/party/idle-exploration/edit", {
        "party_no": 2, "characters": [{"position_no": 1, "t_character_id": 1, "t_weapon_id": 0}]})
    call(context, "api/party/idle-exploration/assign", {"party_no": 2, "assign_to": 101})
    original = [row for row in state(context)["records"]["t_idle_exploration_party_characters"]["up"] if row["party_no"] == 2]
    move_time(monkeypatch, 901)
    edit(context, "research.edit", {**args, "skill_level": 0})
    current = state(context)["records"]
    assert not current["t_idle_exploration_party_relations"]["up"]
    assert [row for row in current["t_idle_exploration_party_characters"]["up"] if row["party_no"] == 2] == original
    assert pending_gold(context) == 166
    assert call(context, "api/idle-exploration/receive")["grant_gold"] == 166
    move_time(monkeypatch, 1800)
    assert call(context, "api/idle-exploration/receive")["grant_gold"] == 0


@pytest.mark.parametrize("source", ["admin", "reward"])
def test_skin_acquisition_through_real_endpoints_grants_bonds_once(context, source):
    catalog = GameplayCatalog(context.tables, context.schema)
    records = state(context)["records"]
    characters = {row["m_character_id"]: row for row in records["t_characters"]["up"]}
    owned = {row["m_character_skin_id"] for row in records["t_character_skins"]["up"]}
    skin = next(row for row in catalog.rows("m_character_skins") if row["m_character_id"] in characters and row["id"] not in owned)
    proposal = master_preview(context, "m_character_skins", lambda row: row["id"] == skin["id"], {"bonus_bond_point": 7})
    config_apply(context, "master", proposal["ticket"])
    character_id = skin["m_character_id"]
    before = characters[character_id]["bond_point"]
    if source == "admin":
        proposal = preview(context, "skin.unlock", {"kind": "character", "ids": [skin["id"]]})
        apply(context, proposal["ticket"])
        apply(context, proposal["ticket"])
        edit(context, "skin.unlock", {"kind": "character", "ids": [skin["id"]]})
    else:
        edit(context, "mail.create", {"kind": 140, "master_id": skin["id"], "title": "Skin reward"})
        presents = progress(context)["features"]["presents"]["data"]["entries"]
        entry = next(row for row in presents if row["content_type"] == 140 and row["content_id"] == skin["id"])
        call(context, "api/present/receive", {"ids": [entry["id"]]})
        call(context, "api/present/receive", {"ids": [entry["id"]]})
    current = next(row for row in state(context)["records"]["t_characters"]["up"] if row["m_character_id"] == character_id)
    assert current["bond_point"] == before + 7
    key = f"character:{skin['id']}:{character_id}"
    assert progress(context)["features"]["character"]["data"]["skin_bond_rewards"][key] == 7
    proposal = master_preview(context, "m_character_skins", lambda row: row["id"] == skin["id"], {"bonus_bond_point": 20})
    config_apply(context, "master", proposal["ticket"])
    current = next(row for row in state(context)["records"]["t_characters"]["up"] if row["m_character_id"] == character_id)
    assert current["bond_point"] == before + 7


@pytest.mark.parametrize("kind", ["rules", "master"])
def test_stale_config_ticket_cannot_switch_configuration(context, kind):
    propose = rules_preview if kind == "rules" else gold_master_preview
    first = propose(context, 36000 if kind == "rules" else 1000)
    stale = propose(context, 30000 if kind == "rules" else 2000)
    config_apply(context, kind, first["ticket"])
    before = snapshot(context), config_files(context)
    response = config_apply(context, kind, stale["ticket"], expected=409)
    assert response
    assert (snapshot(context), config_files(context)) == before


@pytest.mark.parametrize("kind", ["rules", "master"])
def test_config_commit_failure_restores_files_and_all_profile_changes(context, monkeypatch, kind):
    edit(context, "shop.vip", {"points": 9000})
    initialize(context)
    move_time(monkeypatch, 900)
    proposal = rules_preview(context, 36000) if kind == "rules" else gold_master_preview(context, 1000)
    before = snapshot(context), config_files(context)
    journal = context.client.app.state.admin.journal
    original = journal.append
    calls = []

    def fail_after_profile_write(*args, **kwargs):
        calls.append(args)
        if len(calls) == 2:
            raise AdminError("Injected configuration commit failure")
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(journal, "append", fail_after_profile_write)
        config_apply(context, kind, proposal["ticket"], expected=400)
    assert len(calls) == 2
    assert (snapshot(context), config_files(context)) == before
    move_time(monkeypatch, 1800)
    assert call(context, "api/idle-exploration/receive")["grant_gold"] == 336
