import copy
import hashlib
import importlib.util
import json
import struct
from pathlib import Path

import pytest


module_path = Path(__file__).parents[1] / "pc" / "prepare_client.py"
spec = importlib.util.spec_from_file_location("pc_prepare", module_path)
pc_prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pc_prepare)


@pytest.fixture
def prepared_case(tmp_path, monkeypatch):
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "user"))
    source, destination = tmp_path / "official", tmp_path / "local"
    source.mkdir()
    settings = struct.pack("<I", 10) + b"EXNOA LLC." + bytes(2) + b"unparsed-tail"
    (source / "settings").write_bytes(settings)
    (source / "app.info").write_bytes(b"original\n")
    release = {"release": "test", "settings": {"path": "settings", "sha256": hashlib.sha256(settings).hexdigest(),
               "string_changes": [{"offset": 0, "before": "EXNOA LLC.", "after": "LocalGames"}], "integer_changes": []},
               "identity": {"company": "LocalGames", "product": "DotAbyssLocalPlayer"},
               "app_info": "app.info", "executable": "test.exe", "application_version": "1.15.0", "profile_seeds": []}
    release["source_fingerprints"] = {p.name: pc_prepare.digest(p) for p in source.iterdir()}
    release["prepared_fingerprints"] = {"settings": hashlib.sha256(pc_prepare.patch_settings(settings, release)).hexdigest(),
                                         "app.info": hashlib.sha256(b"LocalGames\nDotAbyssLocalPlayer\n").hexdigest()}
    profile = tmp_path / "user/AppData/LocalLow/LocalGames/DotAbyssLocalPlayer"
    return source, destination, release, profile


def test_prepare_isolates_identity_and_preserves_unknown_settings(prepared_case):
    source, destination, release, profile = prepared_case
    original = (source / "settings").read_bytes()
    manifest = pc_prepare.prepare(*prepared_case)
    assert (source / "settings").read_bytes() == original
    assert (destination / "settings").read_bytes()[14:] == original[14:]
    assert (destination / "app.info").read_bytes() == b"LocalGames\nDotAbyssLocalPlayer\n"
    assert manifest["profile"] == str(profile)
    assert pc_prepare.prepare(*prepared_case) == manifest


def test_changed_source_and_overlapping_destination_are_rejected(prepared_case):
    source, destination, release, profile = prepared_case
    with pytest.raises(ValueError, match="separate directories"):
        pc_prepare.prepare(source, source / "copy", release, profile)
    (source / "settings").write_bytes(b"different build")
    with pytest.raises(ValueError, match="source fingerprint"):
        pc_prepare.prepare(*prepared_case)
    assert not destination.exists()


def test_modified_manifest_cannot_approve_modified_client(prepared_case):
    _, destination, release, _ = prepared_case
    manifest = pc_prepare.prepare(*prepared_case)
    (destination / "settings").write_bytes(b"corrupt")
    manifest["prepared_fingerprints"]["settings"] = pc_prepare.digest(destination / "settings")
    pc_prepare.write_json(destination / "local-client.json", manifest)
    with pytest.raises(ValueError, match="reviewed release"):
        pc_prepare.verify_client(destination, release)


def test_partial_resume_only_accepts_known_release_files(prepared_case):
    source, destination, release, _ = prepared_case
    destination.mkdir()
    (destination / "settings").write_bytes((source / "settings").read_bytes())
    (destination / "unrelated").write_bytes(b"user file")
    with pytest.raises(ValueError, match="Unrecognized file"):
        pc_prepare.prepare(*prepared_case)
    assert (destination / "unrelated").read_bytes() == b"user file"
    (destination / "unrelated").unlink()
    pc_prepare.prepare(*prepared_case)
    assert pc_prepare.verify_client(destination, release)["release"] == "test"


def test_settings_width_changes_and_path_escape_are_rejected(prepared_case):
    source, destination, release, _ = prepared_case
    changed = copy.deepcopy(release)
    changed["settings"]["string_changes"][0]["after"] = "longer-company-name"
    with pytest.raises(ValueError, match="serialized width"):
        pc_prepare.patch_settings((source / "settings").read_bytes(), changed)
    with pytest.raises(ValueError, match="leaves"):
        pc_prepare.within(destination, "../official/settings")


@pytest.mark.parametrize("field,value", [("profile", "C:/official"), ("executable", "other.exe"),
                                        ("application_version", "9.0"), ("runtime_isolation_verified", True),
                                        ("identity", {"company": "official", "product": "game"})])
def test_manifest_cannot_change_launch_or_isolation_contract(prepared_case, field, value):
    _, destination, release, _ = prepared_case
    manifest = pc_prepare.prepare(*prepared_case)
    manifest[field] = value
    pc_prepare.write_json(destination / "local-client.json", manifest)
    with pytest.raises(ValueError, match="identity, executable or profile"):
        pc_prepare.verify_client(destination, release)


def test_profile_seed_and_physical_context_are_verified(prepared_case):
    source, destination, release, profile = prepared_case
    seed = source.parent / "catalog.bin"
    seed.write_bytes(b"catalog")
    release["profile_seeds"] = [{"source": str(seed), "relative": "cache/catalog.bin", "sha256": pc_prepare.digest(seed)}]
    manifest = pc_prepare.prepare(*prepared_case)
    manifest["profile_physical_path"] = str(source)
    pc_prepare.write_json(destination / "local-client.json", manifest)
    with pytest.raises(ValueError, match="storage context"):
        pc_prepare.verify_client(destination, release)
    manifest["profile_physical_path"] = str(profile.resolve())
    pc_prepare.write_json(destination / "local-client.json", manifest)
    (profile / "cache/catalog.bin").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="seed changed"):
        pc_prepare.verify_client(destination, release)


def test_interrupted_manifest_commit_can_resume(prepared_case):
    _, destination, release, _ = prepared_case
    destination.mkdir()
    (destination / "local-client.json.partial").write_bytes(b'{"unfinished":')
    pc_prepare.prepare(*prepared_case)
    assert pc_prepare.verify_client(destination, release)["release"] == "test"
    assert not (destination / "local-client.json.partial").exists()


@pytest.fixture
def seeded_case(prepared_case):
    source, _, release, _ = prepared_case
    for index, relative in enumerate(("DownloadCache/master.dat", "com.unity.addressables/catalog.bin",
                                      "com.unity.addressables/catalog.hash")):
        origin = source.parent / f"seed-{index}"
        origin.write_bytes(f"seed content {index}".encode())
        release["profile_seeds"].append({"source": str(origin), "relative": relative, "sha256": pc_prepare.digest(origin)})
    return prepared_case


@pytest.mark.parametrize("seed_index", range(3))
def test_runtime_cache_eviction_does_not_invalidate_prepared_client(seeded_case, seed_index):
    _, destination, release, profile = seeded_case
    manifest = pc_prepare.prepare(*seeded_case)
    seed = profile / release["profile_seeds"][seed_index]["relative"]
    seed.unlink()
    assert pc_prepare.verify_client(destination, release) == manifest
    assert pc_prepare.prepare(*seeded_case) == manifest
    assert not seed.exists()


@pytest.mark.parametrize("seed_index", range(3))
def test_present_runtime_seed_still_requires_original_content(seeded_case, seed_index):
    _, destination, release, profile = seeded_case
    pc_prepare.prepare(*seeded_case)
    (profile / release["profile_seeds"][seed_index]["relative"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="seed changed"):
        pc_prepare.verify_client(destination, release)


@pytest.mark.parametrize("relative", ("settings", "app.info"))
@pytest.mark.parametrize("change,error", (("missing", FileNotFoundError), ("corrupt", ValueError)))
def test_immutable_client_integrity_is_required_after_cache_eviction(seeded_case, relative, change, error):
    _, destination, release, profile = seeded_case
    pc_prepare.prepare(*seeded_case)
    for item in release["profile_seeds"]:
        (profile / item["relative"]).unlink()
    if change == "missing":
        (destination / relative).unlink()
    else:
        (destination / relative).write_bytes(b"changed client")
    with pytest.raises(error):
        pc_prepare.verify_client(destination, release)


def test_evicted_seed_cannot_change_bound_storage_path(seeded_case):
    source, destination, release, profile = seeded_case
    manifest = pc_prepare.prepare(*seeded_case)
    relative = release["profile_seeds"][0]["relative"]
    (profile / relative).unlink()
    manifest["profile_seed_paths"][relative] = str(source / "missing.dat")
    pc_prepare.write_json(destination / "local-client.json", manifest)
    with pytest.raises(ValueError, match="cache storage context"):
        pc_prepare.verify_client(destination, release)


@pytest.mark.parametrize("copy_result", ("missing", "corrupt"))
def test_initial_seed_copy_is_verified_before_manifest_commit(seeded_case, monkeypatch, copy_result):
    _, destination, release, _ = seeded_case
    seed_source = Path(release["profile_seeds"][0]["source"])
    copy2 = pc_prepare.shutil.copy2

    def damaged_copy(source, target):
        result = copy2(source, target)
        if Path(source) == seed_source:
            if copy_result == "missing":
                Path(target).unlink()
            else:
                Path(target).write_bytes(b"incomplete copy")
        return result

    monkeypatch.setattr(pc_prepare.shutil, "copy2", damaged_copy)
    with pytest.raises((FileNotFoundError, ValueError)):
        pc_prepare.prepare(*seeded_case)
    assert not (destination / "local-client.json").exists()


def test_initial_verification_requires_every_seed(seeded_case):
    _, destination, release, profile = seeded_case
    pc_prepare.prepare(*seeded_case)
    (profile / release["profile_seeds"][0]["relative"]).unlink()
    with pytest.raises(FileNotFoundError):
        pc_prepare.verify_client(destination, release, require_seeds=True)
