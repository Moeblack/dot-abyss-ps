import copy
import hashlib
import importlib.util
import json
import os
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest


spec = importlib.util.spec_from_file_location("observer_deploy", Path(__file__).parents[1] / "pc-observer/deploy_observer.py")
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


@pytest.fixture
def observer_case(tmp_path):
    workspace = tmp_path / "workspace"
    client = workspace / "official"
    client.mkdir(parents=True)
    originals = {"GameAssembly.dll": b"original assembly", "Game_Data/globalgamemanagers": b"original identity",
                 "Game.exe": b"original executable"}
    for name, value in originals.items():
        path = client / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)
    release = {"release": "synthetic-observer", "source_fingerprints": {name: hashlib.sha256(value).hexdigest() for name, value in originals.items()}}
    output = workspace / "captures"
    package = workspace / "package"
    sources = {name: ("observer fixture " + name).encode() for name in deploy.REQUIRED_FILES}
    sources[deploy.PLUGIN + "observer.json"] = deploy.encoded({"outputRoot": str(output), "captureRequests": True})
    document = deploy.stage_package(package, release, sources, output, workspace)
    return SimpleNamespace(workspace=workspace, client=client, package=package, output=output,
                           sources=sources, release=release, originals=originals, package_hash=deploy.package_hash(document))


def install(context, **kwargs):
    return deploy.install_package(context.package, context.client, context.release, context.workspace,
                                  expected_package_sha256=context.package_hash, **kwargs)


def remove(context):
    return deploy.remove_package(context.package, context.client, context.release, context.workspace,
                                 expected_package_sha256=context.package_hash)


def original_unchanged(context):
    assert {name: (context.client / name).read_bytes() for name in context.originals} == context.originals


def ownership(context):
    document = deploy.verify_package(context.package, context.release, context.workspace)
    return deploy._record(context.package, context.client, document)


def test_stage_does_not_touch_official_client_and_installs_only_observer(observer_case):
    context = observer_case
    original_unchanged(context)
    assert set(deploy._inventory(context.client)) == set(context.originals)
    assert not context.output.exists()
    document = deploy.verify_package(context.package, context.release, context.workspace)
    assert set(document["files"]) == set(context.sources)
    assert all("Local.Pc" not in name for name in document["files"])
    assert install(context)["status"] == "verified"
    original_unchanged(context)
    assert deploy.verify_installation(context.package, context.client, context.release, context.workspace)["original_verified"]
    assert remove(context)["status"] == "removed"
    original_unchanged(context)
    assert set(deploy._inventory(context.client)) == set(context.originals)


def test_loader_is_installed_last_and_manifest_precedes_every_payload_write(observer_case, monkeypatch):
    context = observer_case
    calls = []
    original = deploy._copy_verified

    def track(source, destination, expected, temporary):
        record, receipt = ownership(context)
        assert deploy._load(receipt) == record
        assert deploy._load(context.client / deploy.INSTALL_RECORD) == record
        calls.append(destination.relative_to(context.client).as_posix())
        original(source, destination, expected, temporary)

    monkeypatch.setattr(deploy, "_copy_verified", track)
    install(context)
    assert calls[-1] == "winhttp.dll"
    assert set(calls) == set(context.sources)


@pytest.mark.parametrize("conflict", ["winhttp.dll", "WINHTTP.dll", "version.dll", "doorstop_config.ini", ".doorstop_version", "BepInEx", "dotnet"])
def test_existing_loader_or_runtime_is_never_overwritten(observer_case, conflict):
    context = observer_case
    target = context.client / conflict
    if conflict in ("BepInEx", "dotnet"):
        target.mkdir()
    else:
        target.write_bytes(b"user-owned")
    with pytest.raises(deploy.DeploymentError, match="conflicting"):
        install(context)
    assert not (context.client / deploy.INSTALL_RECORD).exists()
    assert target.is_dir() if conflict in ("BepInEx", "dotnet") else target.read_bytes() == b"user-owned"
    original_unchanged(context)


def test_changed_original_rejects_installation_before_record_or_loader(observer_case):
    context = observer_case
    (context.client / "GameAssembly.dll").write_bytes(b"different game build")
    with pytest.raises(deploy.DeploymentError, match="Original client fingerprint"):
        install(context)
    assert not (context.client / deploy.INSTALL_RECORD).exists()
    assert not (context.client / "winhttp.dll").exists()


def test_changed_staged_payload_cannot_be_blessed_by_installation_record(observer_case):
    context = observer_case
    (context.package / "payload" / deploy.PLUGIN / "DotAbyss.Observer.Pc.dll").write_bytes(b"tampered")
    with pytest.raises(deploy.DeploymentError, match="fingerprint"):
        install(context)
    assert not (context.client / deploy.INSTALL_RECORD).exists()
    original_unchanged(context)


@pytest.mark.parametrize("path", ["../outside", "/outside", "C:/outside", "BepInEx/../GameAssembly.dll",
                                   "BepInEx\\core\\extra.dll", "BepInEx/core/file:stream", "dotnet/CON", "dotnet/name."])
def test_manifest_path_escapes_are_rejected_before_any_client_write(observer_case, path):
    context = observer_case
    document = deploy._load(context.package / deploy.MANIFEST)
    document["files"][path] = "0" * 64
    (context.package / deploy.MANIFEST).write_bytes(deploy.encoded(document))
    with pytest.raises(deploy.DeploymentError):
        install(context)
    assert not (context.client / deploy.INSTALL_RECORD).exists()
    original_unchanged(context)


def test_payload_cannot_claim_original_or_local_adapter_paths(observer_case):
    context = observer_case
    document = deploy._load(context.package / deploy.MANIFEST)
    for path in ("GameAssembly.dll", "BepInEx/plugins/DotAbyss.Local.Pc/DotAbyss.Local.Pc.dll"):
        forged = copy.deepcopy(document)
        forged["files"][path] = "0" * 64
        (context.package / deploy.MANIFEST).write_bytes(deploy.encoded(forged))
        with pytest.raises(deploy.DeploymentError, match="exclusively owned"):
            install(context)
    original_unchanged(context)


@pytest.mark.parametrize("forge_receipt", [False, True])
def test_forged_ownership_cannot_delete_original_files(observer_case, forge_receipt):
    context = observer_case
    install(context)
    record, receipt = ownership(context)
    record["package"]["files"]["GameAssembly.dll"] = context.release["source_fingerprints"]["GameAssembly.dll"]
    (context.client / deploy.INSTALL_RECORD).write_bytes(deploy.encoded(record))
    if forge_receipt:
        receipt.write_bytes(deploy.encoded(record))
    with pytest.raises(deploy.DeploymentError, match="ownership"):
        remove(context)
    original_unchanged(context)
    assert (context.client / "winhttp.dll").exists()


def test_modified_owned_file_is_preserved_while_verified_loader_is_disabled(observer_case):
    context = observer_case
    install(context)
    target = context.client / deploy.PLUGIN / "DotAbyss.Observer.Pc.dll"
    target.write_bytes(b"modified by user")
    result = remove(context)
    assert result["status"] == "manual_review" and result["loader_disabled"]
    assert result["changed"] == [deploy.PLUGIN + "DotAbyss.Observer.Pc.dll"]
    assert target.read_bytes() == b"modified by user"
    assert not (context.client / "winhttp.dll").exists()
    assert (context.client / deploy.INSTALL_RECORD).is_file()
    original_unchanged(context)


def test_changed_loader_requires_manual_review_without_deleting_it(observer_case):
    context = observer_case
    install(context)
    (context.client / "winhttp.dll").write_bytes(b"another loader")
    result = remove(context)
    assert result["status"] == "manual_review" and not result["loader_disabled"]
    assert (context.client / "winhttp.dll").read_bytes() == b"another loader"
    assert (context.client / deploy.PLUGIN / "DotAbyss.Observer.Pc.dll").exists()


def test_runtime_logs_and_generated_configuration_survive_removal(observer_case):
    context = observer_case
    install(context)
    extras = {"BepInEx/LogOutput.log": b"run log", "BepInEx/config/generated.cfg": b"extra config",
              "BepInEx/config/BepInEx.cfg": b"expanded runtime config"}
    for name, value in extras.items():
        (context.client / name).write_bytes(value)
    result = remove(context)
    assert result["loader_disabled"] and result["status"] == "manual_review"
    assert {name: (context.client / name).read_bytes() for name in extras} == extras
    original_unchanged(context)


@pytest.mark.parametrize("failure", ["before_loader", "after_loader"])
def test_failed_install_rolls_back_and_leaves_no_active_loader(observer_case, monkeypatch, failure):
    context = observer_case
    original = deploy._copy_verified

    def fail(source, destination, expected, temporary):
        if destination.name == "winhttp.dll":
            if failure == "after_loader":
                original(source, destination, expected, temporary)
            raise OSError("Synthetic install failure")
        original(source, destination, expected, temporary)

    monkeypatch.setattr(deploy, "_copy_verified", fail)
    with pytest.raises(deploy.DeploymentError, match="cleanup=removed"):
        install(context)
    assert not (context.client / "winhttp.dll").exists()
    assert set(deploy._inventory(context.client)) == set(context.originals)
    original_unchanged(context)


def test_corrupt_partial_copy_is_left_for_manual_review_with_loader_inactive(observer_case, monkeypatch):
    context = observer_case
    partials = []

    def fail(source, destination, expected, temporary):
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(b"partial bytes")
        partials.append(temporary)
        raise OSError("Interrupted copy")

    monkeypatch.setattr(deploy, "_copy_verified", fail)
    with pytest.raises(deploy.DeploymentError, match="cleanup=manual_review"):
        install(context)
    assert not (context.client / "winhttp.dll").exists()
    assert partials[0].read_bytes() == b"partial bytes"
    assert (context.client / deploy.INSTALL_RECORD).exists()
    original_unchanged(context)


def test_repair_resumes_only_recorded_matching_payload(observer_case):
    context = observer_case
    record, receipt = ownership(context)
    deploy._write_once(receipt, deploy.encoded(record))
    deploy._write_once(context.client / deploy.INSTALL_RECORD, deploy.encoded(record))
    name = "BepInEx/core/BepInEx.Unity.IL2CPP.dll"
    deploy._copy_verified(context.package / "payload" / name, context.client / name,
                          record["package"]["files"][name], context.client / deploy._temporary(name))
    assert not (context.client / "winhttp.dll").exists()
    with pytest.raises(deploy.DeploymentError, match="incomplete"):
        deploy.verify_installation(context.package, context.client, context.release, context.workspace)
    assert install(context, repair=True)["status"] == "verified"
    original_unchanged(context)


def test_partial_deployment_cannot_load_an_unlisted_plugin(observer_case):
    context = observer_case
    record, receipt = ownership(context)
    deploy._write_once(receipt, deploy.encoded(record))
    deploy._write_once(context.client / deploy.INSTALL_RECORD, deploy.encoded(record))
    foreign = context.client / "BepInEx/plugins/other.dll"
    foreign.parent.mkdir(parents=True)
    foreign.write_bytes(b"another plugin")
    with pytest.raises(deploy.DeploymentError, match="Unexpected file"):
        install(context, repair=True)
    assert foreign.read_bytes() == b"another plugin"
    assert not (context.client / "winhttp.dll").exists()


def test_capture_output_and_stage_reject_client_private_profile_and_workspace_escape(observer_case):
    context = observer_case
    for output in (context.workspace.parent / "outside", context.client / "captures", Path("relative-captures")):
        with pytest.raises(deploy.DeploymentError):
            deploy.output_path(output, context.workspace, client=context.client)
    private = context.workspace / "private-profile"
    with pytest.raises(deploy.DeploymentError):
        deploy.output_path(private / "captures", context.workspace, protected=(private,))
    with pytest.raises(deploy.DeploymentError):
        deploy.stage_package(context.client / "stage", context.release, context.sources, context.output,
                             context.workspace, protected=(context.client,))
    original_unchanged(context)


def test_capture_output_inside_client_is_rejected_even_with_valid_package(observer_case):
    context = observer_case
    package = context.workspace / "bad-output-package"
    output = context.client / "captures"
    sources = dict(context.sources)
    sources[deploy.PLUGIN + "observer.json"] = deploy.encoded({"outputRoot": str(output), "captureRequests": True})
    document = deploy.stage_package(package, context.release, sources, output, context.workspace)
    with pytest.raises(deploy.DeploymentError, match="separate"):
        deploy.install_package(package, context.client, context.release, context.workspace,
                               expected_package_sha256=deploy.package_hash(document))
    assert not output.exists()
    original_unchanged(context)


def test_reparse_point_ancestor_is_rejected_without_following_it(observer_case, monkeypatch):
    context = observer_case
    target = context.client / "BepInEx"
    target.mkdir()
    original = Path.lstat

    def marked(path, *args, **kwargs):
        if path == target:
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "lstat", marked)
    with pytest.raises(deploy.DeploymentError, match="junction"):
        deploy.within(context.client, "BepInEx/core/runtime.dll")


def test_actual_symlink_payload_is_rejected(observer_case):
    context = observer_case
    target = context.package / "payload" / deploy.PLUGIN / "DotAbyss.Observer.Pc.dll"
    original = target.read_bytes()
    target.unlink()
    outside = context.workspace.parent / "outside.dll"
    outside.write_bytes(original)
    try:
        target.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks is not permitted on this host")
    with pytest.raises(deploy.DeploymentError, match="junction"):
        install(context)
    assert outside.read_bytes() == original
    assert not (context.client / "winhttp.dll").exists()


def test_self_consistent_forged_package_does_not_match_reviewed_hash(observer_case):
    context = observer_case
    name = deploy.PLUGIN + "DotAbyss.Observer.Pc.dll"
    path = context.package / "payload" / name
    path.write_bytes(b"different payload")
    document = deploy._load(context.package / deploy.MANIFEST)
    document["files"][name] = deploy.digest(path)
    (context.package / deploy.MANIFEST).write_bytes(deploy.encoded(document))
    with pytest.raises(deploy.DeploymentError, match="reviewed SHA"):
        install(context)
    assert not (context.client / deploy.INSTALL_RECORD).exists()
    original_unchanged(context)


def test_package_tampering_after_loader_copy_still_disables_failed_install(observer_case, monkeypatch):
    context = observer_case
    original = deploy._copy_verified

    def change_after_activation(source, destination, expected, temporary):
        original(source, destination, expected, temporary)
        if destination.name == "winhttp.dll":
            (context.package / "payload" / deploy.PLUGIN / "DotAbyss.Observer.Pc.dll").write_bytes(b"changed staging file")

    monkeypatch.setattr(deploy, "_copy_verified", change_after_activation)
    with pytest.raises(deploy.DeploymentError, match="cleanup=removed"):
        install(context)
    assert not (context.client / "winhttp.dll").exists()
    original_unchanged(context)


def test_concurrent_install_cannot_rollback_another_operation(observer_case, monkeypatch):
    context = observer_case
    entered, release = threading.Event(), threading.Event()
    original = deploy._copy_verified

    def pause(*args):
        if not entered.is_set():
            entered.set()
            assert release.wait(timeout=5)
        original(*args)

    monkeypatch.setattr(deploy, "_copy_verified", pause)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(install, context)
        assert entered.wait(timeout=5)
        try:
            with pytest.raises(deploy.DeploymentError, match="operation or recovery lock"):
                install(context)
        finally:
            release.set()
        assert first.result(timeout=5)["status"] == "verified"
    original_unchanged(context)
