import hashlib
import json
import shutil
import struct
from pathlib import Path
from types import SimpleNamespace

import msgpack
import pytest

from dotabyss_ps import assets as asset_module
from dotabyss_ps.asset_index import PC_CATALOG_HASH_PATH, PC_CATALOG_PATH, REMOTE_PROPERTY, build_index
from dotabyss_ps.assets import AssetCatalog, AssetDataError, UNITY_PROVIDER
from dotabyss_ps.masterdata import MasterCatalog
from dotabyss_ps.process_lock import ProcessLockBusy, exclusive_process_lock
from dotabyss_ps.storage import Store
from pc import setup as module
from pc.prepare_client import patch_settings, prepare
from test_asset_index import CatalogFixture, fixture_catalog
from test_assets import cri_payload, unity_payload


def write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)
    return path


@pytest.fixture
def setup_case(tmp_path, monkeypatch):
    root, source = tmp_path / "project", tmp_path / "package/game"
    cache = source.parent / "resource-cache"
    shared = source / "game_Data/Caches"
    profile = tmp_path / "user/AppData/LocalLow/LocalGames/DotAbyssLocalPlayer"
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    monkeypatch.setattr(module, "game_processes", lambda _: [])
    settings = struct.pack("<I", 10) + b"EXNOA LLC." + bytes(2) + b"unparsed-tail"
    write(source / "settings", settings)
    write(source / "app.info", b"original\n")
    write(source / "game.exe", b"test executable, never launched")
    metadata = write(source / "game_Data/il2cpp_data/Metadata/global-metadata.dat", b"test metadata")
    client = {"package": "dotabyss_x_cl", "version": "1.15.0", "metadata_sha256": hashlib.sha256(metadata.read_bytes()).hexdigest()}
    monkeypatch.setattr(asset_module, "SUPPORTED_CLIENTS", (*asset_module.SUPPORTED_CLIENTS, client))
    schema = {"schema_version": 1, "client": client, "tables": {"m_items": [{"name": "id", "type": "long"}]}}
    write(root / "artifacts/master-schema-pc-1.15.0.json", json.dumps(schema).encode())
    release = {
        "release": "synthetic-pc-1.15.0", "launcher_product_id": "dotabyss_x_cl", "application_version": "1.15.0",
        "executable": "game.exe", "app_info": "app.info", "identity": {"company": "LocalGames", "product": "DotAbyssLocalPlayer"},
        "settings": {"path": "settings", "sha256": hashlib.sha256(settings).hexdigest(),
                     "string_changes": [{"offset": 0, "before": "EXNOA LLC.", "after": "LocalGames"}], "integer_changes": []},
        "source_fingerprints": {str(path.relative_to(source).as_posix()): hashlib.sha256(path.read_bytes()).hexdigest()
                                for path in source.rglob("*") if path.is_file()},
    }
    release["prepared_fingerprints"] = {**release["source_fingerprints"],
                                        "settings": hashlib.sha256(patch_settings(settings, release)).hexdigest(),
                                        "app.info": hashlib.sha256(b"LocalGames\nDotAbyssLocalPlayer\n").hexdigest()}
    catalog = write(cache / "com.unity.addressables/downloaded-number.bin", bytes(fixture_catalog().data))
    catalog_hash = write(cache / "com.unity.addressables/downloaded-number.hash", b"c" * 32)
    master = write(cache / "DownloadCache/downloaded-master.dat", msgpack.packb({"m_items": [[1]]}))
    release["profile_seeds"] = [
        {"source": "C:/Users/YourName/unused", "relative": relative, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        for path, relative in ((catalog, "com.unity.addressables/1791059991.bin"),
                               (catalog_hash, "com.unity.addressables/1791059991.hash"),
                               (master, "DownloadCache/master.dat"))]
    release_path = write(root / "pc/releases/pc-1.15.0.example.json", json.dumps(release).encode())
    for record in build_index(catalog, catalog_hash)["records"]:
        name = "__data" if record["provider"] == UNITY_PROVIDER else record["relative_path"].rsplit("/", 1)[-1]
        folder = shared / record["cache_key"] / record["hash128"]
        write(folder / "__info", ("-1\n1793371989\n1\n" + name + "\n").encode())
        write(folder / name, unity_payload() if record["provider"] == UNITY_PROVIDER else cri_payload())
    return SimpleNamespace(root=root, source=source, cache=cache, shared=shared, release=release, release_path=release_path,
                           profile=profile, catalog=catalog, catalog_hash=catalog_hash, master=master,
                           client=root.parent / "dotabyss_pc/local-client", data=root / "data-pc-1.15.0")


def inspect(case):
    return module.inspect_inputs(case.root, source=case.source)


def import_assets(case, inputs=None, **kwargs):
    return module.import_resources(inputs or inspect(case), case.data, progress=lambda _: None, **kwargs)


def add_missing_resource(case, inputs):
    fixture = fixture_catalog()
    extra = fixture.location(REMOTE_PROPERTY + "/missing.bundle", UNITY_PROVIDER, key="missing_cache")
    fixture.finish([(location,) for location in fixture.locations])
    assert extra in fixture.locations
    case.catalog.write_bytes(fixture.data)
    inputs["index"] = build_index(case.catalog, case.catalog_hash)


def save_state(case, revision):
    store = Store(case.data / "local.sqlite3")
    player = store.login("preserve-test-player")["user_id"]
    with store.connection() as db:
        db.execute("INSERT INTO game_states VALUES(?,1,?,?,?)", (player, revision, '{"test":"keep saved progress"}', "today"))
    return store


def state_rows(store):
    with store.connection() as db:
        return [tuple(row) for row in db.execute("SELECT * FROM game_states")]


def test_inspection_discovers_packaged_cache_and_game_data_caches_without_writes(setup_case):
    c = setup_case
    result = inspect(c)
    assert result["cache"] == c.cache and result["shared"] == c.shared
    assert result["catalog"] == c.catalog and result["catalog_hash"] == c.catalog_hash
    assert result["release"]["profile_seeds"][0]["source"] == str(c.catalog)
    assert len(result["index"]["records"]) == 2
    assert not c.client.exists() and not c.data.exists()
    assert not c.release_path.with_name("pc-1.15.0.json").exists()


def test_check_mode_is_read_only(setup_case):
    c = setup_case
    result = module.initialize(c.root, source=c.source, check=True)
    assert result["checked_inputs_only"] and result["indexed_count"] == 2
    assert not c.client.exists() and not c.data.exists()
    assert not c.profile.exists()


def test_initialization_shares_client_lock_across_checkouts(setup_case):
    c = setup_case
    with exclusive_process_lock(c.client.parent / ".local-client-setup.lock"):
        with pytest.raises(ProcessLockBusy):
            module.initialize(c.root, source=c.source, progress=lambda _: None)
    assert not c.client.exists() and not c.data.exists()


def test_first_initialization_prepares_client_and_imports_resources(setup_case):
    c = setup_case
    result = module.initialize(c.root, source=c.source, progress=lambda _: None)
    assert result["ready"] is False and "BepInEx" in result["next"]
    assert result["assets"]["verified_count"] == 2
    assert (c.client / "app.info").read_bytes() == b"LocalGames\nDotAbyssLocalPlayer\n"
    assert (c.source / "app.info").read_bytes() == b"original\n"
    assert (c.profile / "com.unity.addressables/1791059991.bin").read_bytes() == c.catalog.read_bytes()
    assert c.release_path.with_name("pc-1.15.0.json").is_file()


def test_prepared_client_reinitialization_installs_plugin_and_preserves_saves(setup_case, monkeypatch):
    c = setup_case
    inputs = inspect(c)
    prepare(c.source, c.client, inputs["release"], c.profile)
    first = import_assets(c, inputs)
    store = save_state(c, first["master_revision"])
    before = state_rows(store)
    write(c.client / "BepInEx/interop/Project.dll", b"synthetic interop")
    config = write(c.client / "BepInEx/plugins/DotAbyss.Local.Pc/pc-local.json", b"keep existing config")
    installs = []
    monkeypatch.setattr(module, "install_plugin", lambda *args: installs.append(args) or {"changed": True})
    result = module.initialize(c.root, progress=lambda _: None)
    assert result["ready"] is True and result["reused"] is True
    assert installs == [(c.root, c.client)]
    assert config.read_bytes() == b"keep existing config" and state_rows(store) == before


def test_prepared_client_can_use_explicit_cache_after_original_source_moves(setup_case):
    c = setup_case
    inputs = inspect(c)
    prepare(c.source, c.client, inputs["release"], c.profile)
    shared = c.cache / "Shared"
    shutil.copytree(c.shared, shared)
    c.source.rename(c.source.with_name("moved-original"))
    result = module.inspect_inputs(c.root, cache_root=c.cache, shared_root=shared)
    assert result["client"] == c.client and result["shared"] == shared


def test_explicit_missing_source_is_rejected_even_for_prepared_client(setup_case):
    c = setup_case
    inputs = inspect(c)
    prepare(c.source, c.client, inputs["release"], c.profile)
    with pytest.raises(ValueError, match="Source game directory is missing"):
        module.inspect_inputs(c.root, source=c.source / "missing", cache_root=c.cache, shared_root=c.shared)


def test_import_activates_fixed_catalog_urls_and_is_repeatable(setup_case):
    c = setup_case
    first = import_assets(c)
    store = save_state(c, first["master_revision"])
    before = state_rows(store)
    assets = AssetCatalog(c.data, store)
    assert assets.read(PC_CATALOG_PATH)[1] == c.catalog.read_bytes()
    assert assets.read(PC_CATALOG_HASH_PATH)[1] == b"c" * 32
    assert assets.status()["active"]["catalog_served"] is True
    assert import_assets(c)["reused"] is True
    assert state_rows(store) == before


def test_changed_master_during_setup_is_not_activated(setup_case):
    c = setup_case
    inputs = inspect(c)
    c.master.write_bytes(msgpack.packb({"m_items": [[2]]}))
    with pytest.raises(ValueError, match="changed during setup"):
        import_assets(c, inputs)
    store = Store(c.data / "local.sqlite3")
    assert store.master() is None and AssetCatalog(c.data, store).active() is None


def test_missing_resources_require_explicit_partial_activation(setup_case):
    c = setup_case
    inputs = inspect(c)
    add_missing_resource(c, inputs)
    with pytest.raises(ValueError, match="AllowPartial"):
        import_assets(c, inputs)
    store = Store(c.data / "local.sqlite3")
    assert store.master() is None and AssetCatalog(c.data, store).active() is None
    report = json.loads((c.data / "setup/resource-report.json").read_text())
    assert report["indexed_count"] == 3 and report["verified_count"] == 2 and len(report["missing"]) == 1
    result = import_assets(c, inputs, allow_partial=True)
    assert result["assets"]["complete"] is False and result["assets"]["missing_count"] == 1
    assert AssetCatalog(c.data, store).read(PC_CATALOG_HASH_PATH)[1] == b"c" * 32


@pytest.mark.parametrize("allow_partial", [False, True])
def test_empty_explicit_cache_never_activates(setup_case, allow_partial):
    c = setup_case
    empty = c.root / "empty-cache"
    empty.mkdir()
    inputs = module.inspect_inputs(c.root, source=c.source, shared_root=empty)
    with pytest.raises(ValueError, match="No resource files"):
        import_assets(c, inputs, allow_partial=allow_partial)
    store = Store(c.data / "local.sqlite3")
    assert store.master() is None and AssetCatalog(c.data, store).active() is None


@pytest.mark.parametrize("failure", ["missing", "corrupt"])
def test_failed_reimport_keeps_previous_activation_and_saved_progress(setup_case, failure):
    c = setup_case
    inputs = inspect(c)
    first = import_assets(c, inputs)
    store = save_state(c, first["master_revision"])
    before = state_rows(store)
    pointer = c.data / "assets/active.json"
    active_before = pointer.read_bytes()
    add_missing_resource(c, inputs)
    if failure == "corrupt":
        record = next(record for record in inputs["index"]["records"] if record["cache_key"] == "ordinary_cache_name")
        (c.shared / record["cache_key"] / record["hash128"] / "__data").write_bytes(b"broken resource")
    with pytest.raises((ValueError, AssetDataError)):
        import_assets(c, inputs)
    assert pointer.read_bytes() == active_before
    assert store.master()["revision"] == first["master_revision"] and state_rows(store) == before


def test_different_active_master_is_not_replaced(setup_case):
    c = setup_case
    inputs = inspect(c)
    store = Store(c.data / "local.sqlite3")
    other = write(c.root / "other-master.dat", msgpack.packb({"m_items": [[2]]}))
    masters = MasterCatalog(c.data, store)
    record = masters.stage(other, inputs["schema"], "Different synthetic master")
    masters.activate(record["revision"])
    save_state(c, record["revision"])
    before = state_rows(store)
    with pytest.raises(ValueError, match="another master revision"):
        import_assets(c, inputs)
    assert store.master()["revision"] == record["revision"] and state_rows(store) == before
    assert not (c.data / "setup/resource-url-index.json").exists()


def test_save_bound_to_other_master_blocks_import_even_without_active_master(setup_case):
    c = setup_case
    inputs = inspect(c)
    store = Store(c.data / "local.sqlite3")
    other = write(c.root / "other-master.dat", msgpack.packb({"m_items": [[2]]}))
    record = MasterCatalog(c.data, store).stage(other, inputs["schema"], "Other saved revision")
    save_state(c, record["revision"])
    before = state_rows(store)
    with pytest.raises(ValueError, match="Existing saves use another master revision"):
        import_assets(c, inputs)
    assert store.master() is None and state_rows(store) == before
