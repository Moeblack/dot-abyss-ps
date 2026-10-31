import hashlib
import json
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from dotabyss_ps.asset_index import (
    CATALOG_MAGIC, DEFAULT_REMOTE_BASE, DYNAMIC_FLAG, LOCAL_PROPERTY, NULL_OFFSET,
    OPTIONS_TYPE, PC_CATALOG_HASH_PATH, PC_CATALOG_PATH, PC_CATALOG_PREFIX,
    REMOTE_PROPERTY, UNICODE_FLAG, build_index,
)
from dotabyss_ps import asset_index
from dotabyss_ps.assets import AssetDataError, CRI_PROVIDER, UNITY_PROVIDER
from test_assets import asset_setup
from test_newgame import PC_CLIENT


class CatalogFixture:
    def __init__(self):
        self.data = bytearray(32)
        self.types = {}
        self.locations = []
        self.option_offsets = []
        self.dynamic_offsets = []

    def value(self, layout, *values):
        offset = len(self.data)
        self.data.extend(struct.pack(layout, *values))
        return offset

    def block(self, payload):
        self.value("<I", len(payload))
        offset = len(self.data)
        self.data.extend(payload)
        return offset

    def text(self, value, separator=""):
        if separator and separator in value:
            previous = NULL_OFFSET
            for part in value.split(separator):
                previous = self.value("<II", self.text(part), previous)
                self.dynamic_offsets.append(previous)
            return DYNAMIC_FLAG | previous
        unicode = not value.isascii()
        offset = self.block(value.encode("utf-16-le" if unicode else "ascii"))
        return offset | UNICODE_FLAG if unicode else offset

    def type(self, name, assembly="Unity.ResourceManager"):
        key = assembly, name
        if key not in self.types:
            self.types[key] = self.value("<II", self.text(assembly + ", Version=0.0.0.0", "."), self.text(name, "."))
        return self.types[key]

    def array(self, values):
        return self.block(struct.pack("<" + "I" * len(values), *values))

    def options(self, key, version, size):
        digest = len(self.data)
        self.data.extend(bytes.fromhex(version))
        common = self.value("<hBBI", 15, 32, 2, 8)
        options = self.value("<IIIII", digest, self.text(key, "_"), 0, size, common)
        self.option_offsets.append(options)
        return self.value("<II", self.type(OPTIONS_TYPE), options)

    def location(self, internal, provider, *, key="cache_key", version="a" * 32, size=80, dependencies=(), options=True):
        extra = self.options(key, version, size) if options else NULL_OFFSET
        offset = self.value("<7I", self.text("Assets/測試/角色.prefab", "/"), self.text(internal, "/"),
                            self.text(provider, "."), self.array(dependencies) if dependencies else NULL_OFFSET,
                            0, extra, self.type("UnityEngine.GameObject", "UnityEngine.CoreModule"))
        self.locations.append(offset)
        return offset

    def finish(self, key_locations):
        keys = []
        for index, locations in enumerate(key_locations):
            remap = self.value("<IH2x", self.text("角色/" + str(index), "/"), ord("/"))
            key = self.value("<II", self.type("System.String", "mscorlib"), remap)
            keys.extend((key, self.array(locations)))
        self.keys = self.array(keys)
        locator = self.text("AddressablesMainContentCatalog")
        build_hash = self.text("d" * 32)
        struct.pack_into("<8I", self.data, 0, CATALOG_MAGIC, 2, self.keys, locator,
                         NULL_OFFSET, NULL_OFFSET, NULL_OFFSET, build_hash)
        return self


def fixture_catalog():
    fixture = CatalogFixture()
    unity = fixture.location(REMOTE_PROPERTY + "/unit_a.bundle", UNITY_PROVIDER, key="ordinary_cache_name")
    cri = fixture.location(REMOTE_PROPERTY + "/audio/bgm.awb_" + "b" * 32, CRI_PROVIDER,
                           key="cri_cache", version="b" * 32, size=40)
    local = fixture.location(LOCAL_PROPERTY + "\\StandaloneWindows64\\local.bundle", UNITY_PROVIDER,
                             key="local_cache", version="c" * 32)
    bundled = fixture.location("f" * 32, "UnityEngine.ResourceManagement.ResourceProviders.BundledAssetProvider",
                               dependencies=(cri, unity), options=False)
    return fixture.finish([(unity, local), (bundled,), (unity,)])


def write_catalog(tmp_path, fixture):
    catalog, version = tmp_path / "1791059991.bin", tmp_path / "1791059991.hash"
    catalog.write_bytes(fixture.data)
    version.write_text("c" * 32, encoding="ascii")
    return catalog, version


def test_index_decodes_dependencies_unicode_dynamic_strings_and_provider_cache_names(tmp_path):
    fixture = fixture_catalog()
    catalog, version = write_catalog(tmp_path, fixture)
    index = build_index(catalog, version)
    records = {entry["cache_key"]: entry for entry in index["records"]}
    assert len(records) == 2
    assert records["ordinary_cache_name"] == {
        "cache_key": "ordinary_cache_name", "hash128": "a" * 32, "bundle_size": 80,
        "provider": UNITY_PROVIDER, "relative_path": PC_CATALOG_PREFIX + "/unit_a.bundle",
        "resolved_url": DEFAULT_REMOTE_BASE + "/unit_a.bundle",
    }
    assert records["cri_cache"]["relative_path"] == PC_CATALOG_PREFIX + "/audio/bgm.awb_" + "b" * 32
    assert records["cri_cache"]["provider"] == CRI_PROVIDER
    assert index["catalog_sha256"] == hashlib.sha256(fixture.data).hexdigest()
    assert index["catalog_version_hash"] == "c" * 32
    assert index["catalog_build_result_hash"] == "d" * 32


@pytest.mark.parametrize("asset_setup", [PC_CLIENT], indirect=True)
def test_generated_index_stages_serves_catalog_and_preserves_cache_verification(asset_setup):
    context = asset_setup
    fixture = CatalogFixture()
    locations = []
    for record in context.index["records"]:
        suffix = record["relative_path"].removeprefix(PC_CATALOG_PREFIX)
        locations.append(fixture.location(REMOTE_PROPERTY + suffix, record["provider"],
                                          key=record["cache_key"], version=record["hash128"], size=record["bundle_size"]))
    fixture.finish([locations])
    context.catalog_file.write_bytes(fixture.data)
    context.index = build_index(context.catalog_file, context.hash_file)
    context.index_file.write_text(json.dumps(context.index), encoding="utf-8")
    staged = context.assets.stage(context.index_file, context.catalog_file, context.hash_file, context.cache,
                                   "Synthetic binary catalog", catalog_relative_path=PC_CATALOG_PATH,
                                   catalog_hash_relative_path=PC_CATALOG_HASH_PATH)
    context.assets.activate(staged["revision"])
    assert context.assets.status()["active"]["verified_count"] == 2
    assert context.assets.read(PC_CATALOG_PATH)[1] == bytes(fixture.data)
    assert context.assets.read(PC_CATALOG_HASH_PATH)[1] == b"c" * 32


@pytest.mark.parametrize("position,value", [(0, 0), (4, 3), (8, NULL_OFFSET), (12, NULL_OFFSET)])
def test_invalid_header_and_required_references_are_rejected(tmp_path, position, value):
    fixture = fixture_catalog()
    struct.pack_into("<I", fixture.data, position, value)
    with pytest.raises(AssetDataError):
        build_index(*write_catalog(tmp_path, fixture))


@pytest.mark.parametrize("damage", ["partial-key-array", "oversized-array", "location-outside-file", "truncated-options", "invalid-utf16"])
def test_malformed_structured_data_is_rejected(tmp_path, damage):
    fixture = fixture_catalog()
    if damage == "partial-key-array":
        struct.pack_into("<I", fixture.data, fixture.keys - 4, 7)
    elif damage == "oversized-array":
        struct.pack_into("<I", fixture.data, fixture.keys - 4, 0xFFFFFFF8)
    elif damage == "location-outside-file":
        location_set, = struct.unpack_from("<I", fixture.data, fixture.keys + 4)
        struct.pack_into("<I", fixture.data, location_set, len(fixture.data) + 4)
    elif damage == "truncated-options":
        struct.pack_into("<I", fixture.data, fixture.option_offsets[0] + 16, len(fixture.data) - 2)
    else:
        text = fixture.text("角色")
        struct.pack_into("<I", fixture.data, (text & 0x3FFFFFFF) - 4, 3)
        struct.pack_into("<I", fixture.data, fixture.locations[0], text)
    with pytest.raises(AssetDataError):
        build_index(*write_catalog(tmp_path, fixture))


def test_dynamic_string_cycle_is_rejected(tmp_path):
    fixture = fixture_catalog()
    node = fixture.dynamic_offsets[0]
    struct.pack_into("<I", fixture.data, node + 4, node)
    with pytest.raises(AssetDataError, match="cyclic"):
        build_index(*write_catalog(tmp_path, fixture))


@pytest.mark.parametrize("path,provider", [
    (REMOTE_PROPERTY + "/../outside.bundle", UNITY_PROVIDER),
    (REMOTE_PROPERTY + "/catalog_1.hash", UNITY_PROVIDER),
    (REMOTE_PROPERTY + "/file.bundle?token=x", UNITY_PROVIDER),
    ("https://external.invalid/file.bundle", UNITY_PROVIDER),
    ("{Unknown.Property}/file.bundle", UNITY_PROVIDER),
    (REMOTE_PROPERTY + "/file.bundle", "Unknown.Provider"),
])
def test_unknown_and_escaping_resource_paths_are_rejected(tmp_path, path, provider):
    fixture = CatalogFixture()
    fixture.finish([(fixture.location(path, provider),)])
    with pytest.raises(AssetDataError):
        build_index(*write_catalog(tmp_path, fixture))


@pytest.mark.parametrize("damage", ["cache-traversal", "size-zero", "size-over-limit", "unknown-options", "invalid-options"])
def test_resource_options_are_validated_before_indexing(tmp_path, damage):
    fixture = fixture_catalog()
    options = fixture.option_offsets[0]
    if damage == "cache-traversal":
        struct.pack_into("<I", fixture.data, options + 4, fixture.text("../bad"))
    elif damage in ("size-zero", "size-over-limit"):
        struct.pack_into("<I", fixture.data, options + 12, 0 if damage == "size-zero" else 128 * 1024 * 1024 + 1)
    elif damage == "unknown-options":
        extra, = struct.unpack_from("<I", fixture.data, fixture.locations[0] + 20)
        struct.pack_into("<I", fixture.data, extra, fixture.type("Unknown.Options"))
    else:
        common, = struct.unpack_from("<I", fixture.data, options + 16)
        struct.pack_into("<I", fixture.data, common + 4, 0x80000000)
    with pytest.raises(AssetDataError):
        build_index(*write_catalog(tmp_path, fixture))


@pytest.mark.parametrize("same_path", [True, False])
def test_conflicting_paths_or_cache_tuples_are_rejected(tmp_path, same_path):
    fixture = CatalogFixture()
    first = fixture.location(REMOTE_PROPERTY + "/first.bundle", UNITY_PROVIDER)
    second = fixture.location(REMOTE_PROPERTY + ("/first.bundle" if same_path else "/second.bundle"), UNITY_PROVIDER,
                              key="other" if same_path else "cache_key")
    fixture.finish([(first, second)])
    with pytest.raises(AssetDataError, match="conflicting"):
        build_index(*write_catalog(tmp_path, fixture))


@pytest.mark.parametrize("remote_base", [
    "http://pc-local.invalid/" + PC_CATALOG_PREFIX,
    "https://pc-local.invalid/resources/android/r18/aas/0.1.0/aa",
    DEFAULT_REMOTE_BASE + "?token=x",
    DEFAULT_REMOTE_BASE + "\n",
    "https://" + "x" * 2048 + "/" + PC_CATALOG_PREFIX,
    "https://user:secret@pc-local.invalid/" + PC_CATALOG_PREFIX,
])
def test_resource_binding_cannot_change_platform_or_include_credentials(tmp_path, remote_base):
    with pytest.raises(AssetDataError):
        build_index(*write_catalog(tmp_path, fixture_catalog()), remote_base=remote_base)


@pytest.mark.parametrize("version", [b"", b"not-a-hash", b"A" * 32, b"\xff" * 32])
def test_invalid_catalog_version_hash_is_rejected(tmp_path, version):
    catalog, hash_file = write_catalog(tmp_path, fixture_catalog())
    hash_file.write_bytes(version)
    with pytest.raises(AssetDataError):
        build_index(catalog, hash_file)


def cli_arguments(catalog, version, output):
    return ["--catalog", str(catalog), "--catalog-hash", str(version), "--output", str(output)]


def test_cli_creates_parent_directories_and_prints_only_summary(tmp_path):
    catalog, version = write_catalog(tmp_path, fixture_catalog())
    output = tmp_path / "new" / "nested" / "resource-url-index.json"
    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "server")
    result = subprocess.run([sys.executable, "-m", "dotabyss_ps.asset_index", *cli_arguments(catalog, version, output)],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    summary = json.loads(result.stdout)
    assert summary["records"] == 2
    assert Path(summary["output"]) == output
    assert len(result.stdout) < 2048
    assert "ordinary_cache_name" not in result.stdout
    assert json.loads(output.read_text(encoding="utf-8")) == build_index(catalog, version)
    assert not list(output.parent.glob("*.partial"))


@pytest.mark.parametrize("source", ["catalog", "hash"])
def test_cli_refuses_to_overwrite_catalog_or_hash(tmp_path, capsys, source):
    catalog, version = write_catalog(tmp_path, fixture_catalog())
    before = catalog.read_bytes(), version.read_bytes()
    with pytest.raises(SystemExit) as error:
        asset_index.main(cli_arguments(catalog, version, catalog if source == "catalog" else version))
    assert error.value.code == 1
    assert "must not overwrite" in capsys.readouterr().err
    assert (catalog.read_bytes(), version.read_bytes()) == before


def test_cli_refuses_hard_link_output(tmp_path, capsys):
    catalog, version = write_catalog(tmp_path, fixture_catalog())
    output = tmp_path / "linked.json"
    os.link(catalog, output)
    before = catalog.read_bytes()
    with pytest.raises(SystemExit):
        asset_index.main(cli_arguments(catalog, version, output))
    assert "ordinary file" in capsys.readouterr().err
    assert catalog.read_bytes() == before


def test_cli_refuses_directory_output(tmp_path, capsys):
    catalog, version = write_catalog(tmp_path, fixture_catalog())
    output = tmp_path / "directory"
    output.mkdir()
    with pytest.raises(SystemExit):
        asset_index.main(cli_arguments(catalog, version, output))
    assert "ordinary file" in capsys.readouterr().err
    assert output.is_dir()


def test_cli_preserves_existing_index_and_cleans_temporary_file_on_failed_replace(tmp_path, monkeypatch, capsys):
    catalog, version = write_catalog(tmp_path, fixture_catalog())
    output = tmp_path / "index.json"
    output.write_bytes(b"previous index")

    def fail_replace(source, target):
        raise PermissionError("index locked")

    monkeypatch.setattr(asset_index.os, "replace", fail_replace)
    with pytest.raises(SystemExit):
        asset_index.main(cli_arguments(catalog, version, output))
    assert "index locked" in capsys.readouterr().err
    assert output.read_bytes() == b"previous index"
    assert not list(tmp_path.glob("*.partial"))


def test_cli_reports_bad_catalog_without_creating_output(tmp_path, capsys):
    catalog, version = write_catalog(tmp_path, fixture_catalog())
    catalog.write_bytes(b"truncated catalog")
    output = tmp_path / "new" / "index.json"
    with pytest.raises(SystemExit):
        asset_index.main(cli_arguments(catalog, version, output))
    captured = capsys.readouterr()
    assert "Resource index error:" in captured.err
    assert "Traceback" not in captured.err
    assert captured.out == ""
    assert not output.parent.exists()


def test_cli_refuses_symlink_output(tmp_path, capsys):
    catalog, version = write_catalog(tmp_path, fixture_catalog())
    output = tmp_path / "linked.json"
    try:
        output.symlink_to(catalog)
    except OSError:
        pytest.skip("Symlink creation is unavailable")
    before = catalog.read_bytes()
    with pytest.raises(SystemExit):
        asset_index.main(cli_arguments(catalog, version, output))
    assert "symbolic links" in capsys.readouterr().err
    assert catalog.read_bytes() == before
