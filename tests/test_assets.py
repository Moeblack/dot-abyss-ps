import copy
import hashlib
import json
import struct
from types import SimpleNamespace
from pathlib import Path

import msgpack
import pytest
from fastapi.testclient import TestClient

from dotabyss_ps.app import Settings, create_app
from dotabyss_ps.assets import AssetCatalog, AssetDataError, CRI_PROVIDER, UNITY_PROVIDER, read_bounded, validate_payload
from dotabyss_ps.masterdata import MasterCatalog
from dotabyss_ps.newgame import SUPPORTED_CLIENTS
from dotabyss_ps.storage import Store
from test_newgame import PC_CLIENT, PLAY_CLIENT


def unity_payload():
    prefix = b"UnityFS\0" + struct.pack(">I", 8) + b"5.x.x\0" + b"6000.3.8f1\0"
    header = prefix + struct.pack(">QIII", 80, 4, 4, 0)
    return header + b"\0" * (64 - len(header)) + b"info" + b"bundle bytes"


def cri_payload():
    header = b"AFS2" + bytes((2, 4)) + struct.pack("<HiHH", 4, 1, 32, 0)
    tables = struct.pack("<III", 7, 28, 40)
    return header + tables + b"\0" * 4 + b"HCA\0test"


def usm_payload():
    def chunk(tag, body, padding):
        header = tag + struct.pack(">I", 24 + len(body) + padding) + bytes((0, 24)) + struct.pack(">H", padding) + bytes(20)
        return header + body + bytes(padding)

    return chunk(b"CRID", b"@UTFmetadata", 4) + chunk(b"@SFV", b"video packet", 4)


@pytest.fixture
def asset_setup(tmp_path, request):
    settings = Settings.from_directory(tmp_path / "data")
    store = Store(settings.data_dir / "local.sqlite3")
    masters = MasterCatalog(settings.data_dir, store)
    selected_client = getattr(request, "param", PLAY_CLIENT)
    schema = {"schema_version": 1, "client": copy.deepcopy(selected_client),
              "tables": {"m_items": [{"name": "id", "type": "long"}]}}

    def master(client=None, number=1):
        selected = copy.deepcopy(schema)
        if client is not None:
            selected["client"] = copy.deepcopy(client)
        layout = tmp_path / "master-schema.json"
        layout.write_text(json.dumps(selected), encoding="utf-8")
        payload = tmp_path / "master.msgpack"
        payload.write_bytes(msgpack.packb({"m_items": [[number]]}))
        record = masters.stage(payload, layout, "Synthetic master")
        masters.activate(record["revision"])
        return record

    master_record = master()
    source = tmp_path / "source"
    source.mkdir()
    cache = source / "Shared"
    cache.mkdir()
    catalog_file, hash_file = source / "catalogcached.bin", source / "catalogcached.hash"
    catalog_file.write_bytes(b"synthetic decoded catalog")
    hash_file.write_text("c" * 32, encoding="ascii")
    namespace = "windows/r18" if selected_client == PC_CLIENT else "android_googleplay/normal"
    base = "https://example.test/resources/" + namespace + "/aas/0.1.0/aa"
    prefix = base.split("example.test/", 1)[1]
    payloads = [unity_payload(), cri_payload()]
    records, payload_paths = [], []
    for index, (payload, provider, key, version) in enumerate(zip(payloads, (UNITY_PROVIDER, CRI_PROVIDER), ("unity-key", "cri-key"), ("a" * 32, "b" * 32))):
        suffix = "synthetic_" + version + ".bundle" if index == 0 else "audio/nested/bgm.awb_" + version
        relative = prefix + "/" + suffix
        record = {"relative_path": relative, "resolved_url": base + "/" + suffix, "cache_key": key,
                  "hash128": version, "provider": provider, "bundle_size": len(payload)}
        records.append(record)
        folder = cache / key / version
        folder.mkdir(parents=True)
        filename = "__data" if index == 0 else suffix.rsplit("/", 1)[1]
        (folder / "__info").write_text("-1\n1793371989\n1\n" + filename + "\n", encoding="ascii")
        target = folder / filename
        target.write_bytes(payload)
        payload_paths.append(target)
    index = {"catalog_sha256": hashlib.sha256(catalog_file.read_bytes()).hexdigest(),
             "catalog_build_result_hash": "d" * 32, "binding": {"remote_base": base}, "records": records}
    index_file = source / "index.json"
    assets = AssetCatalog(settings.data_dir, store)

    def stage(**kwargs):
        index_file.write_text(json.dumps(index), encoding="utf-8")
        return assets.stage(index_file, catalog_file, hash_file, cache, "Synthetic assets", **kwargs)

    yield SimpleNamespace(settings=settings, store=store, assets=assets, stage=stage, index=index,
                          index_file=index_file, master=master, master_record=master_record, source=source,
                          cache=cache, catalog_file=catalog_file, hash_file=hash_file, payloads=payloads,
                          payload_paths=payload_paths, prefix=prefix, records=records)


def test_stage_is_immutable_and_activation_does_not_change_runtime(asset_setup):
    context = asset_setup
    runtime = context.store.runtime()
    staged = context.stage()
    assert context.assets.active() is None
    assert staged["complete"] and staged["verified_count"] == staged["indexed_count"] == 2
    assert len(staged["entries"]) == 2
    assert staged["catalog"]["relative_path"] is None
    assert staged["catalog_hash"]["relative_path"] is None
    assert staged["catalog_hash_value"] == "c" * 32
    assert staged["catalog_build_result_hash"] == "d" * 32
    assert context.stage()["revision"] == staged["revision"]
    result = context.assets.activate(staged["revision"])
    assert result["catalog_served"] is False
    assert context.store.runtime() == runtime
    for record, original in zip(context.records, context.payloads):
        entry, payload, revision = context.assets.read(record["relative_path"])
        assert payload == original
        assert entry["sha256"] == hashlib.sha256(original).hexdigest()
        assert entry["hash128"] == record["hash128"]
        assert entry["hash128"] != hashlib.md5(original).hexdigest()
        assert revision == staged["revision"]


@pytest.mark.parametrize("asset_setup", [PLAY_CLIENT, PC_CLIENT], indirect=True, ids=["play", "pc"])
def test_http_serves_verified_snapshot_with_content_etag(asset_setup):
    context = asset_setup
    staged = context.stage()
    context.assets.activate(staged["revision"])
    with TestClient(create_app(context.settings)) as client:
        for record, expected in zip(context.records, context.payloads):
            response = client.get("/" + record["relative_path"])
            assert response.status_code == 200
            assert response.content == expected
            assert response.headers["etag"] == '"' + hashlib.sha256(expected).hexdigest() + '"'
            assert response.headers["x-asset-revision"] == staged["revision"]
            cached = client.get("/" + record["relative_path"], headers={"if-none-match": "W/" + response.headers["etag"]})
            assert cached.status_code == 304 and cached.content == b""
        diagnostic = client.get("/diagnostics").json()["assets"]
        assert diagnostic["active"]["verified_count"] == 2
        assert diagnostic["error"] == ""


@pytest.mark.parametrize("asset_setup", [PLAY_CLIENT, PC_CLIENT], indirect=True, ids=["play", "pc"])
def test_explicit_catalog_urls_are_served_without_guessing_names(asset_setup):
    context = asset_setup
    catalog_path, hash_path = context.prefix + "/actual.catalog", context.prefix + "/actual.hash"
    staged = context.stage(catalog_relative_path=catalog_path, catalog_hash_relative_path=hash_path)
    context.assets.activate(staged["revision"])
    with TestClient(create_app(context.settings)) as client:
        assert client.get("/" + catalog_path).content == context.catalog_file.read_bytes()
        assert client.get("/" + hash_path).content == context.hash_file.read_bytes()
        assert client.get("/" + context.prefix + "/catalog_1.bin").status_code == 404


def test_catalog_urls_revalidate_after_another_manifest_is_activated(asset_setup):
    context = asset_setup
    urls = {"catalog_relative_path": context.prefix + "/catalog.bin", "catalog_hash_relative_path": context.prefix + "/catalog.hash"}
    first = context.stage(**urls)
    context.assets.activate(first["revision"])
    with TestClient(create_app(context.settings)) as client:
        previous = {path: client.get("/" + path) for path in urls.values()}
        assert all(response.headers["cache-control"] == "no-cache" for response in previous.values())
        context.catalog_file.write_bytes(b"updated synthetic catalog")
        context.index["catalog_sha256"] = hashlib.sha256(context.catalog_file.read_bytes()).hexdigest()
        context.hash_file.write_text("f" * 32, encoding="ascii")
        second = context.stage(**urls)
        context.assets.activate(second["revision"])
        for path, original in previous.items():
            response = client.get("/" + path, headers={"if-none-match": original.headers["etag"]})
            assert response.status_code == 200
            assert response.content != original.content
            assert response.headers["etag"] != original.headers["etag"]
            assert response.headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("kwargs", [
    {"catalog_relative_path": "resources/test/catalog.bin"},
    {"catalog_relative_path": "../catalog.bin", "catalog_hash_relative_path": "../catalog.hash"},
])
def test_catalog_urls_must_be_explicit_and_inside_binding(asset_setup, kwargs):
    with pytest.raises(AssetDataError):
        asset_setup.stage(**kwargs)
    assert asset_setup.assets.active() is None


@pytest.mark.parametrize("field,value", [
    ("relative_path", "../secret"), ("relative_path", "resources/../secret"),
    ("relative_path", "resources/a%2fb.bundle"), ("relative_path", "resources/a\\b.bundle"),
    ("cache_key", "../escape"), ("cache_key", "C:/outside"), ("hash128", "../escape"),
    ("resolved_url", "https://other.test/unrelated.bundle"),
    ("provider", "unknown"), ("bundle_size", True),
])
def test_index_paths_and_cache_identity_cannot_escape_or_change_binding(asset_setup, field, value):
    context = asset_setup
    context.records[0][field] = value
    with pytest.raises(AssetDataError):
        context.stage()
    assert context.assets.active() is None


@pytest.mark.parametrize("info", [
    "-1\n1793371989\n1\n../outside\n", "-1\n1793371989\n2\n__data\n",
    "-1\n1793371989\n1\nwrong-file\n", "-1\nnot-a-time\n1\n__data\n",
])
def test_cache_info_rejects_unknown_layout_or_wrong_payload_filename(asset_setup, info):
    context = asset_setup
    context.payload_paths[0].with_name("__info").write_text(info, encoding="ascii")
    with pytest.raises(AssetDataError):
        context.stage(allow_partial=True)
    assert context.assets.active() is None


def test_expected_cache_version_is_required_without_guessing_other_versions(asset_setup):
    context = asset_setup
    context.records[0]["hash128"] = "d" * 32
    with pytest.raises(AssetDataError, match="Missing 1"):
        context.stage()
    partial = context.stage(allow_partial=True)
    assert partial["verified_count"] == 1
    assert partial["missing"][0]["hash128"] == "d" * 32
    assert context.records[0]["relative_path"] not in partial["entries"]


def test_missing_files_require_explicit_partial_import_and_never_fall_back(asset_setup):
    context = asset_setup
    context.payload_paths[1].unlink()
    with pytest.raises(AssetDataError, match="Missing 1"):
        context.stage()
    assert context.assets.active() is None
    partial = context.stage(allow_partial=True)
    context.assets.activate(partial["revision"])
    path = context.records[1]["relative_path"]
    static = context.settings.resources_dir / path
    static.parent.mkdir(parents=True)
    static.write_bytes(b"unverified fallback")
    with TestClient(create_app(context.settings)) as client:
        assert client.get("/" + path).status_code == 404
        assert client.get("/" + context.records[0]["relative_path"]).status_code == 200
    assert partial["complete"] is False
    assert len(partial["missing"]) == 1


def test_static_fallback_is_preserved_without_assets_and_outside_active_prefix(asset_setup):
    context = asset_setup
    existing = context.settings.resources_dir / "resources/legacy/test.bin"
    existing.parent.mkdir(parents=True)
    existing.write_bytes(b"legacy")
    with TestClient(create_app(context.settings)) as client:
        assert client.get("/resources/legacy/test.bin").content == b"legacy"
        staged = context.stage()
        context.assets.activate(staged["revision"])
        assert client.get("/resources/legacy/test.bin").content == b"legacy"


@pytest.mark.parametrize("active", [False, True])
def test_invalid_resource_path_remains_a_client_error(asset_setup, active):
    context = asset_setup
    if active:
        context.assets.activate(context.stage()["revision"])
    with TestClient(create_app(context.settings)) as client:
        assert client.get("/" + context.prefix + "/bad%5Cname.bundle").status_code == 400


@pytest.mark.parametrize("mutation", ["catalog", "hash", "duplicate-path", "duplicate-tuple", "truncated"])
def test_invalid_staging_cannot_replace_active_manifest(asset_setup, mutation):
    context = asset_setup
    previous = context.stage()
    context.assets.activate(previous["revision"])
    if mutation == "catalog":
        context.catalog_file.write_bytes(b"different")
    elif mutation == "hash":
        context.hash_file.write_text("e" * 31, encoding="ascii")
    elif mutation == "truncated":
        context.payload_paths[0].write_bytes(context.payloads[0][:-1])
    else:
        duplicate = copy.deepcopy(context.records[0])
        if mutation == "duplicate-path":
            duplicate["cache_key"] = "different"
        else:
            duplicate["relative_path"] += "-different"
            duplicate["resolved_url"] += "-different"
        context.records.append(duplicate)
    with pytest.raises(AssetDataError):
        context.stage(allow_partial=True)
    assert context.assets.active()["revision"] == previous["revision"]


@pytest.mark.parametrize("asset_setup", [PLAY_CLIENT, PC_CLIENT], indirect=True, ids=["play", "pc"])
def test_namespace_and_master_revision_cannot_be_crossed(asset_setup):
    context = asset_setup
    staged = context.stage()
    context.assets.activate(staged["revision"])
    context.master(number=2)
    with pytest.raises(AssetDataError, match="active master"):
        context.assets.activate(staged["revision"])
    with TestClient(create_app(context.settings)) as client:
        response = client.get("/" + context.records[0]["relative_path"])
        assert response.status_code == 503
    context.master(client=SUPPORTED_CLIENTS[0])
    with pytest.raises(AssetDataError, match="namespace"):
        context.stage()
    assert context.assets.active()["revision"] == staged["revision"]


@pytest.mark.parametrize("asset_setup", [PLAY_CLIENT, PC_CLIENT], indirect=True, ids=["play-to-pc", "pc-to-play"])
def test_pc_and_play_assets_cannot_cross_identical_master_payloads(asset_setup):
    context = asset_setup
    staged = context.stage()
    context.assets.activate(staged["revision"])
    other = PC_CLIENT if context.master_record["client"] == PLAY_CLIENT else PLAY_CLIENT
    replacement = context.master(client=other)
    assert replacement["sha256"] == context.master_record["sha256"]
    assert replacement["revision"] != context.master_record["revision"]
    with pytest.raises(AssetDataError, match="active master"):
        context.assets.activate(staged["revision"])
    with pytest.raises(AssetDataError, match="namespace"):
        context.stage()
    with TestClient(create_app(context.settings)) as client:
        assert client.get("/" + context.records[0]["relative_path"]).status_code == 503


def test_tampered_blob_returns_503_even_for_matching_etag(asset_setup):
    context = asset_setup
    staged = context.stage()
    context.assets.activate(staged["revision"])
    path = context.records[0]["relative_path"]
    entry = staged["entries"][path]
    blob = context.settings.data_dir / "assets/blobs" / entry["sha256"]
    payload = bytearray(blob.read_bytes())
    payload[-1] ^= 1
    blob.write_bytes(payload)
    with TestClient(create_app(context.settings)) as client:
        assert client.get("/" + path, headers={"if-none-match": '"' + entry["sha256"] + '"'}).status_code == 503
    with pytest.raises(AssetDataError, match="integrity"):
        context.assets.activate(staged["revision"])


def test_response_body_and_etag_use_same_snapshot_if_blob_changes_after_read(asset_setup, monkeypatch):
    context = asset_setup
    staged = context.stage()
    context.assets.activate(staged["revision"])
    path = context.records[0]["relative_path"]
    original_read = AssetCatalog._read_blob

    def replace_after_read(catalog, entry):
        payload = original_read(catalog, entry)
        catalog._path("blobs/" + entry["sha256"]).write_bytes(b"changed after read")
        return payload

    monkeypatch.setattr(AssetCatalog, "_read_blob", replace_after_read)
    with TestClient(create_app(context.settings)) as client:
        response = client.get("/" + path)
        assert response.status_code == 200
        assert response.content == context.payloads[0]
        assert response.headers["etag"] == '"' + hashlib.sha256(response.content).hexdigest() + '"'


def test_failed_pointer_publication_preserves_previous_activation(asset_setup, monkeypatch):
    context = asset_setup
    first = context.stage()
    context.assets.activate(first["revision"])
    second = context.stage(catalog_relative_path=context.prefix + "/catalog.bin", catalog_hash_relative_path=context.prefix + "/catalog.hash")
    original = context.assets._atomic

    def fail_pointer(path, payload):
        if path.name == "active.json":
            raise OSError("Synthetic write failure")
        return original(path, payload)

    monkeypatch.setattr(context.assets, "_atomic", fail_pointer)
    with pytest.raises(OSError):
        context.assets.activate(second["revision"])
    assert context.assets.active()["revision"] == first["revision"]


@pytest.mark.parametrize("corruption", ["length", "info-bounds", "header", "version"])
def test_unity_headers_reject_truncated_or_out_of_bounds_data(corruption):
    payload = bytearray(unity_payload())
    if corruption == "length":
        payload[-1:] = b""
    elif corruption == "info-bounds":
        struct.pack_into(">I", payload, 37, 999999)
    elif corruption == "header":
        payload[7:] = b""
    else:
        struct.pack_into(">I", payload, 8, 999)
    with pytest.raises(AssetDataError):
        validate_payload(bytes(payload), UNITY_PROVIDER, len(payload))


@pytest.mark.parametrize("corruption", ["length", "offset-order", "count", "alignment", "header"])
def test_afs2_headers_reject_truncated_or_out_of_bounds_data(corruption):
    payload = bytearray(cri_payload())
    if corruption == "length":
        struct.pack_into("<I", payload, 24, 999)
    elif corruption == "offset-order":
        struct.pack_into("<I", payload, 20, 41)
    elif corruption == "count":
        struct.pack_into("<i", payload, 8, 1000001)
    elif corruption == "alignment":
        struct.pack_into("<H", payload, 12, 0)
    else:
        payload[4:] = b""
    with pytest.raises(AssetDataError):
        validate_payload(bytes(payload), CRI_PROVIDER, len(payload))


def test_catalog_version_hash_is_checked_when_observed_in_index(asset_setup):
    context = asset_setup
    context.index["catalog_version_hash"] = "f" * 32
    with pytest.raises(AssetDataError, match="observed version"):
        context.stage()
    context.index["catalog_version_hash"] = "c" * 32
    assert context.stage()["catalog_hash_value"] == "c" * 32


def test_usm_video_uses_exact_cache_filename_and_verified_http_snapshot(asset_setup):
    context = asset_setup
    version = "e" * 32
    filename = "movie.usm_" + version
    path = context.prefix + "/movie/nested/" + filename
    payload = usm_payload()
    context.records.append({"relative_path": path, "resolved_url": "https://example.test/" + path,
                            "cache_key": "movie-key", "hash128": version, "provider": CRI_PROVIDER, "bundle_size": len(payload)})
    folder = context.cache / "movie-key" / version
    folder.mkdir(parents=True)
    (folder / "__info").write_text("-1\n1793371989\n1\n" + filename + "\n", encoding="ascii")
    (folder / filename).write_bytes(payload)
    staged = context.stage()
    assert staged["entries"][path]["format"] == "USM"
    context.assets.activate(staged["revision"])
    with TestClient(create_app(context.settings)) as client:
        response = client.get("/" + path)
        assert response.status_code == 200
        assert response.content == payload
        assert response.headers["etag"] == '"' + hashlib.sha256(payload).hexdigest() + '"'


@pytest.mark.parametrize("damage", ["chunk-size", "offset", "padding", "short-header", "unknown-chunk", "trailing-byte", "no-stream"])
def test_usm_chunk_boundaries_and_signatures_are_validated(damage):
    payload = bytearray(usm_payload())
    second = 48
    if damage == "chunk-size":
        struct.pack_into(">I", payload, second + 4, len(payload))
    elif damage == "offset":
        payload[second + 9] = 23
    elif damage == "padding":
        struct.pack_into(">H", payload, second + 10, 65535)
    elif damage == "short-header":
        payload = payload[:second + 12]
    elif damage == "unknown-chunk":
        payload[second:second + 4] = b"????"
    elif damage == "trailing-byte":
        payload += b"x"
    else:
        payload = payload[:second]
    with pytest.raises(AssetDataError):
        validate_payload(bytes(payload), CRI_PROVIDER, len(payload))


def test_unknown_cri_format_is_not_treated_as_an_audio_or_video_container():
    payload = b"unknown format" + bytes(64)
    with pytest.raises(AssetDataError, match="supported"):
        validate_payload(payload, CRI_PROVIDER, len(payload))


def test_bounded_file_snapshot_rejects_growth_during_read(tmp_path, monkeypatch):
    target = tmp_path / "changing.bin"
    target.write_bytes(b"original")
    original_open = Path.open

    class ChangingFile:
        def __enter__(self):
            self.stream = original_open(target, "rb")
            return self

        def __exit__(self, *args):
            self.stream.close()

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            payload = self.stream.read(size)
            with original_open(target, "ab") as writer:
                writer.write(b"appended")
            return payload

    def open_path(path, *args, **kwargs):
        return ChangingFile() if path == target else original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", open_path)
    with pytest.raises(AssetDataError, match="changed"):
        read_bounded(target, 1024)
