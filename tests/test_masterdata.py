import copy
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor

import msgpack
import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import Settings, create_app
from dotabyss_ps.masterdata import MasterCatalog, MasterDataError, inspect_payload
from dotabyss_ps.storage import Store


@pytest.fixture
def schema():
    return {
        "schema_version": 1,
        "client": {"package": "test.game", "version": "1.14.4", "metadata_sha256": "a" * 64},
        "tables": {
            "m_buildings": [{"name": "id", "type": "long", "unique": True, "uniqueness_evidence": "Fixture key contract"}, {"name": "name", "type": "string"}],
            "m_chapters": [{"name": "id", "type": "long"}, {"name": "stage_id", "type": "long"}],
        },
        "compatibility": {
            "skip_unknown_tables": True,
            "unknown_table_evidence": "Verified native Skip branch",
            "defaulted_tails": {"m_chapters": {"minimum_columns": 1, "evidence": "Verified native array length loop"}},
        },
    }


@pytest.fixture
def tables():
    return {"m_buildings": [[1, "Building"]], "m_chapters": [[2]], "m_future": [[1, "Future"]]}


def stage(tmp_path, schema, tables, store=None):
    root = tmp_path / "data"
    store = store or Store(root / "local.sqlite3")
    payload = msgpack.packb(tables, use_bin_type=True)
    source = tmp_path / "source.msgpack"
    source.write_bytes(payload)
    layout = tmp_path / "schema.json"
    layout.write_text(json.dumps(schema), encoding="utf-8")
    catalog = MasterCatalog(root, store)
    record = catalog.stage(source, layout, "Test fixture")
    return catalog, store, record, payload


def test_native_supported_defaults_do_not_rewrite_payload(schema, tables):
    payload = msgpack.packb(tables, use_bin_type=True)
    result = inspect_payload(payload, schema)
    assert result["sha256"] == hashlib.sha256(payload).hexdigest()
    assert result["defaulted_tail_rows"] == {"m_chapters": 1}
    assert result["skipped_tables"] == ["m_future"]
    assert result["payload_modified"] is False
    assert result["business_validated"] is False


def test_missing_required_table_rejected(schema, tables):
    tables.pop("m_chapters")
    with pytest.raises(MasterDataError, match="Missing required"):
        inspect_payload(msgpack.packb(tables), schema)


@pytest.mark.parametrize("row", [[1, 7], [True, "name"], [2**64 - 1, "name"], [1], [1, "name", 3]])
def test_field_type_and_column_drift_rejected(schema, tables, row):
    tables["m_buildings"] = [row]
    with pytest.raises(MasterDataError):
        inspect_payload(msgpack.packb(tables), schema)


def test_no_silent_default_without_evidence(schema, tables):
    schema["compatibility"]["defaulted_tails"] = {}
    with pytest.raises(MasterDataError, match="column count"):
        inspect_payload(msgpack.packb(tables), schema)
    schema["compatibility"]["skip_unknown_tables"] = False
    with pytest.raises(MasterDataError, match="Unknown tables"):
        inspect_payload(msgpack.packb(tables), schema)


def test_duplicate_ids_and_duplicate_map_keys_rejected(schema, tables):
    tables["m_buildings"].append([1, "Other"])
    with pytest.raises(MasterDataError, match="unique key"):
        inspect_payload(msgpack.packb(tables), schema)
    packer = msgpack.Packer()
    data = packer.pack_map_header(2) + packer.pack("m_buildings") + packer.pack([]) + packer.pack("m_buildings") + packer.pack([])
    with pytest.raises(MasterDataError):
        inspect_payload(data, schema)


def test_ids_are_not_assumed_to_be_unique_in_views(schema, tables):
    schema["tables"]["m_buildings"][0].pop("unique")
    tables["m_buildings"].append([1, "Another view row"])
    report = inspect_payload(msgpack.packb(tables), schema)
    assert report["duplicate_id_rows"] == {"m_buildings": 1}


def test_schema_evidence_and_bounds_required(schema, tables):
    schema["compatibility"].pop("unknown_table_evidence")
    with pytest.raises(MasterDataError, match="evidence"):
        inspect_payload(msgpack.packb(tables), schema)


def test_stage_is_nonactivating_and_idempotent(tmp_path, schema, tables):
    catalog, store, record, payload = stage(tmp_path, schema, tables)
    assert store.master() is None
    assert store.runtime() == {}
    assert catalog.checked(record["revision"])[1].read_bytes() == payload
    again = stage(tmp_path, schema, tables, store)[2]
    assert again == record


def test_activation_binds_payload_version_and_preserves_session(tmp_path, schema, tables):
    catalog, store, record, _ = stage(tmp_path, schema, tables)
    session = store.login("existing")
    store.set_runtime({"application_version": "1.14.4", "bundle_version": "0.1.0"})
    catalog.activate(record["revision"])
    assert store.runtime()["resource_version"] == record["revision"]
    assert store.authenticate(session["token"], session["api_token"])["user_id"] == session["user_id"]
    assert store.runtime()["bundle_version"] == "0.1.0"
    with pytest.raises(ValueError, match="bound"):
        store.update_runtime({"resource_version": "unrelated"})
    assert store.runtime()["resource_version"] == record["revision"]


def test_incompatible_client_cannot_activate_or_overwrite_runtime(tmp_path, schema, tables):
    catalog, store, record, _ = stage(tmp_path, schema, tables)
    store.set_runtime({"application_version": "1.15.0"})
    with pytest.raises(ValueError, match="does not match"):
        catalog.activate(record["revision"])
    assert store.master() is None
    assert store.runtime() == {"application_version": "1.15.0"}
    store.set_runtime({"application_version": "1.14.4"})
    catalog.activate(record["revision"])
    with pytest.raises(ValueError, match="does not match"):
        store.update_runtime({"application_version": "1.15.0"})


def test_bad_second_import_keeps_active_version(tmp_path, schema, tables):
    catalog, store, first, _ = stage(tmp_path, schema, tables)
    catalog.activate(first["revision"])
    tables["m_chapters"] = [[2, "wrong-type"]]
    with pytest.raises(MasterDataError):
        stage(tmp_path, schema, tables, store)
    assert store.master()["revision"] == first["revision"]


def test_rollback_selects_previous_immutable_payload(tmp_path, schema, tables):
    catalog, store, first, first_payload = stage(tmp_path, schema, tables)
    catalog.activate(first["revision"])
    changed = copy.deepcopy(tables)
    changed["m_buildings"].append([3, "New"])
    second = stage(tmp_path, schema, changed, store)[2]
    catalog.activate(second["revision"])
    catalog.activate(first["revision"])
    assert store.master()["revision"] == first["revision"]
    assert catalog.checked(first["revision"])[1].read_bytes() == first_payload


def test_runtime_updates_cannot_drop_master_revision(tmp_path, schema, tables):
    catalog, store, record, _ = stage(tmp_path, schema, tables)
    catalog.activate(record["revision"])
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(store.update_runtime, [{"application_version": "1.14.4"}, {"bundle_version": "0.1.0"}]))
    assert store.runtime() == {"resource_version": record["revision"], "application_version": "1.14.4", "bundle_version": "0.1.0"}


def test_http_serves_identical_bytes_and_cache_identity(tmp_path, schema, tables):
    catalog, store, record, payload = stage(tmp_path, schema, tables)
    catalog.activate(record["revision"])
    with TestClient(create_app(Settings.from_directory(tmp_path / "data"))) as client:
        response = client.get(f"/data/{record['revision']}?s=ignored-local-signature&t=0")
        assert response.status_code == 200
        assert response.content == payload
        assert response.headers["content-type"] == "application/x-msgpack"
        assert response.headers["x-resource-version"] == record["revision"]
        cached = client.get(f"/data/{record['revision']}", headers={"If-None-Match": "W/" + response.headers["etag"]})
        assert cached.status_code == 304 and not cached.content
        assert client.get("/local/master").json()["active"]["revision"] == record["revision"]
        assert client.get("/data/unknown").status_code == 404
        assert "ignored-local-signature" not in client.get("/diagnostics").text


def test_corrupt_payload_never_reports_success(tmp_path, schema, tables):
    catalog, store, record, _ = stage(tmp_path, schema, tables)
    catalog.activate(record["revision"])
    path = catalog.checked(record["revision"])[1]
    path.write_bytes(b"corrupt")
    with TestClient(create_app(Settings.from_directory(tmp_path / "data"))) as client:
        response = client.get(f"/data/{record['revision']}")
        assert response.status_code == 503
    with pytest.raises(MasterDataError):
        catalog.activate(record["revision"])


def test_http_body_uses_the_verified_snapshot(tmp_path, schema, tables, monkeypatch):
    catalog, _, record, payload = stage(tmp_path, schema, tables)
    catalog.activate(record["revision"])
    read = MasterCatalog.read

    def mutate_after_read(self, revision):
        metadata, snapshot = read(self, revision)
        changed = copy.deepcopy(tables)
        changed["m_buildings"][0][0] = 9
        (self.root / metadata["payload_path"]).write_bytes(msgpack.packb(changed, use_bin_type=True))
        return metadata, snapshot

    monkeypatch.setattr(MasterCatalog, "read", mutate_after_read)
    with TestClient(create_app(Settings.from_directory(tmp_path / "data"))) as client:
        response = client.get(f"/data/{record['revision']}")
    assert response.content == payload
    assert response.headers["etag"].strip('"') == hashlib.sha256(response.content).hexdigest()
