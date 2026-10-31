import hashlib
import importlib.util
import io
import json
import zipfile
from pathlib import Path
from types import SimpleNamespace
from urllib.request import Request

import pytest

from dotabyss_ps.process_lock import ProcessLockBusy, exclusive_process_lock


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / "pc-translation" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


install = module("local_translation_install", "prepare_translation.py")
bootstrap = module("local_translation_bootstrap", "bootstrap_translation.py")


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def tree(path):
    return {item.relative_to(path).as_posix(): item.read_bytes() for item in path.rglob("*") if item.is_file()}


def package(path, translated):
    path.mkdir(parents=True)
    write_json(path / "cache/translations/names/zh_Hant.json", {"原文": translated})
    write_json(path / "cache/translations/manifest/zh_Hant.json", {})
    (path / "AbyssMod.dll").write_bytes(b"synthetic-plugin-" + translated.encode())
    report = {"format_version": 1, "counts": {"files": 2}, "files": {
        item.relative_to(path).as_posix(): install.digest(item) for item in path.rglob("*") if item.is_file()}}
    write_json(path / "integration-manifest.json", report)
    return report


@pytest.fixture
def case(tmp_path, monkeypatch):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    monkeypatch.setattr(install, "ARTIFACTS", artifacts)
    client = tmp_path / "client"
    write_json(client / "local-client.json", {"identity": {"company": "LocalGames", "product": "DotAbyssLocalPlayer"}, "executable": "local.exe"})
    (client / "local.exe").write_bytes(b"synthetic-client")
    target = client / "BepInEx/plugins/AbyssMod"
    package(target, "舊譯文")
    config = client / "BepInEx/config/AbyssMod.cfg"
    config.parent.mkdir()
    config.write_text("[General]\nDynamicMosaic = false\n[Custom]\nKeepMe = yes\n[Translation]\nEnabled = false\nLanguage = ja\n", encoding="utf-8")
    stage = artifacts / "prepared-test"
    report = package(stage, "新譯文")
    return SimpleNamespace(artifacts=artifacts, client=client, target=target, config=config, stage=stage, report=report)


def test_install_updates_cache_and_preserves_unrelated_configuration(case):
    backup = install.install(case.stage, case.client, case.report)
    assert json.loads((case.target / "cache/translations/names/zh_Hant.json").read_text(encoding="utf-8")) == {"原文": "新譯文"}
    config = case.config.read_text(encoding="utf-8")
    assert "DynamicMosaic = false" in config and "KeepMe = yes" in config
    assert "Enabled = true" in config and "Language = zh_Hant" in config
    assert (backup / "AbyssMod/AbyssMod.dll").read_bytes() == b"synthetic-plugin-" + "舊譯文".encode()
    saved = json.loads((case.artifacts / "installed.json").read_text(encoding="utf-8"))
    assert saved["config_sha256"] == install.digest(case.config)


def test_unknown_local_dictionary_is_retained_and_added_to_manifest(case):
    write_json(case.target / "cache/translations/other/local/zh_Hant.json", {"補充": "私有補充"})
    install.install(case.stage, case.client, case.report)
    assert (case.target / "cache/translations/other/local/zh_Hant.json").exists()
    manifest = json.loads((case.target / "cache/translations/manifest/zh_Hant.json").read_text(encoding="utf-8"))
    assert "local" in manifest["other"]


def test_conflicting_modified_dictionary_preserves_old_installation(case):
    write_json(case.target / "cache/translations/names/zh_Hant.json", {"原文": "我的修改"})
    before = tree(case.target)
    config = case.config.read_bytes()
    with pytest.raises(ValueError, match="conflicts"):
        install.install(case.stage, case.client, case.report)
    assert tree(case.target) == before and case.config.read_bytes() == config


@pytest.mark.parametrize("unexpected", [False, True])
def test_prepared_package_is_verified_before_replacing_installed_plugin(case, unexpected):
    (case.stage / ("extra.dll" if unexpected else "AbyssMod.dll")).write_bytes(b"unexpected payload")
    before = tree(case.target)
    with pytest.raises(ValueError, match="unexpected files|failed verification"):
        install.install(case.stage, case.client, case.report)
    assert tree(case.target) == before


@pytest.mark.parametrize("failure", ["configuration", "report"])
def test_failed_publication_restores_plugin_configuration_and_report(case, monkeypatch, failure):
    saved_report = case.artifacts / "installed.json"
    saved_report.write_bytes(b'{"old":true}')
    before = tree(case.target)
    config = case.config.read_bytes()
    original = Path.replace

    def replace(path, target):
        if (failure == "configuration" and Path(target) == case.config
                or failure == "report" and Path(target) == saved_report):
            raise OSError("synthetic write failure")
        return original(path, target)

    monkeypatch.setattr(Path, "replace", replace)
    with pytest.raises(OSError, match="synthetic"):
        install.install(case.stage, case.client, case.report)
    assert tree(case.target) == before
    assert case.config.read_bytes() == config
    assert saved_report.read_bytes() == b'{"old":true}'


def test_config_update_is_deterministic_and_retains_credentials():
    original = b"[General]\nDynamicMosaic = false\n[MachineTranslation]\nApiKey = local-custom-key\nEnabled = true\n"
    changed = install.translation_config(original)
    assert b"ApiKey = local-custom-key" in changed
    assert b"DynamicMosaic = false" in changed
    assert install.translation_config(changed) == changed


def test_run_holds_launcher_lock_and_rechecks_game_before_install(case, monkeypatch):
    events = []
    checks = []
    monkeypatch.setattr(install.shutil, "which", lambda _: "synthetic-sdk")

    def stopped(client):
        checks.append(client)
        with pytest.raises(ProcessLockBusy):
            with exclusive_process_lock(client / ".local-launcher.lock"):
                pytest.fail("lock must be held")

    monkeypatch.setattr(install, "require_game_stopped", stopped)
    monkeypatch.setattr(install, "prepare", lambda *args, **kwargs: (case.stage, case.report))
    result = install.run(case.client, apply=True, notify=lambda *args: events.append(args))
    assert result["installed"] and checks == [case.client, case.client]
    assert events[-1][0] == "complete" and events[-1][2] == 100


def test_run_rejects_game_appearing_during_preparation(case, monkeypatch):
    calls = []
    monkeypatch.setattr(install.shutil, "which", lambda _: "synthetic-sdk")

    def stopped(client):
        calls.append(client)
        if len(calls) == 2:
            raise ValueError("game appeared")

    monkeypatch.setattr(install, "require_game_stopped", stopped)
    monkeypatch.setattr(install, "prepare", lambda *args, **kwargs: (case.stage, case.report))
    before = tree(case.target)
    with pytest.raises(ValueError, match="game appeared"):
        install.run(case.client, apply=True, notify=lambda *args: None)
    assert tree(case.target) == before


def test_run_rejects_another_launcher_operation(case):
    with exclusive_process_lock(case.client / ".local-launcher.lock"):
        with pytest.raises(ValueError, match="busy"):
            install.run(case.client, apply=True)


def test_plugin_with_only_required_patch_is_reused(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    patch = tmp_path / "compat.patch"
    patch.write_text("diff --git a/a.cs b/a.cs\nindex 1..2 100644\n-old\n+new\n")
    calls = []
    monkeypatch.setattr(bootstrap, "verified_repository", lambda *args: None)

    def git(directory, *args):
        calls.append(args)
        if args[0] == "rev-parse": return bootstrap.PLUGIN_COMMIT
        if args[0] == "status": return " M a.cs"
        if args[0] == "diff": return patch.read_text().replace("index 1..2", "index aaa..bbb")
        return ""

    monkeypatch.setattr(bootstrap, "git", git)
    bootstrap.ensure_plugin(source, patch)
    assert ("apply", "--reverse", "--check", str(patch.resolve())) in calls
    assert not any(args[:2] == ("apply", str(patch.resolve())) for args in calls)


def test_additional_plugin_changes_are_never_overwritten(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    patch = tmp_path / "compat.patch"
    patch.write_text("expected patch")
    calls = []
    monkeypatch.setattr(bootstrap, "verified_repository", lambda *args: None)

    def git(directory, *args):
        calls.append(args)
        return bootstrap.PLUGIN_COMMIT if args[0] == "rev-parse" else "user changes"

    monkeypatch.setattr(bootstrap, "git", git)
    with pytest.raises(ValueError, match="local changes"):
        bootstrap.ensure_plugin(source, patch)
    assert not any(args[0] in ("apply", "checkout", "fetch") for args in calls)


def test_missing_plugin_checkout_uses_pinned_source_and_applies_patch(tmp_path, monkeypatch):
    source = tmp_path / "plugin"
    patch = tmp_path / "compat.patch"
    patch.write_text("diff --git a/a.cs b/a.cs\n-old\n+new\n")
    calls, patched = [], [False]

    def git(directory, *args):
        calls.append(args)
        if args[0] == "clone":
            (source / ".git").mkdir(parents=True)
        if args == ("rev-parse", "--show-toplevel"): return str(source)
        if args == ("rev-parse", "HEAD"): return bootstrap.PLUGIN_COMMIT
        if args[0] == "remote": return bootstrap.PLUGIN_URL
        if args[0] == "status": return " M a.cs" if patched[0] else ""
        if args[0] == "diff": return patch.read_text() if patched[0] else ""
        if args == ("apply", str(patch.resolve())): patched[0] = True
        return ""

    monkeypatch.setattr(bootstrap, "git", git)
    bootstrap.ensure_plugin(source, patch)
    assert ("clone", "--no-checkout", bootstrap.PLUGIN_URL, str(source)) in calls
    assert ("checkout", "--detach", bootstrap.PLUGIN_COMMIT) in calls
    assert patched[0]


def test_missing_translation_checkout_clones_only_fixed_main_source(tmp_path, monkeypatch):
    source = tmp_path / "translations"
    calls = []

    def git(directory, *args):
        calls.append(args)
        if args[0] == "clone": (source / ".git").mkdir(parents=True)
        if args == ("rev-parse", "--show-toplevel"): return str(source)
        if args[0] == "remote": return bootstrap.TRANSLATION_URL
        return ""

    monkeypatch.setattr(bootstrap, "git", git)
    bootstrap.ensure_translations(source, False)
    assert calls[0] == ("clone", "--branch", "main", "--single-branch", bootstrap.TRANSLATION_URL, str(source))


def test_translation_fetch_refuses_dirty_worktree(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(bootstrap, "verified_repository", lambda *args: None)

    def git(directory, *args):
        calls.append(args)
        return " M dictionary.json"

    monkeypatch.setattr(bootstrap, "git", git)
    with pytest.raises(ValueError, match="local changes"):
        bootstrap.ensure_translations(tmp_path, True)
    assert calls == [("status", "--porcelain", "--untracked-files=all")]


def test_clean_translation_update_is_fast_forward_only(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(bootstrap, "verified_repository", lambda *args: None)

    def git(directory, *args):
        calls.append(args)
        return "main" if args[0] == "branch" else ""

    monkeypatch.setattr(bootstrap, "git", git)
    bootstrap.ensure_translations(tmp_path, True)
    assert ("fetch", "origin", "main") in calls
    assert ("merge", "--ff-only", "origin/main") in calls


def fake_release(tmp_path, monkeypatch, *, duplicate=False):
    payload = b"verified synthetic dependency"
    expected = hashlib.sha256(payload).hexdigest()
    archive = tmp_path / "AbyssMod-v1.2.0.zip"
    member = "BepInEx/plugins/AbyssMod/Utility.dll"
    with zipfile.ZipFile(archive, "w") as data:
        data.writestr(member, payload)
        data.writestr("../../outside.dll", b"not extracted")
        if duplicate:
            with pytest.warns(UserWarning): data.writestr(member, payload)
    monkeypatch.setattr(bootstrap, "DEPENDENCIES", {"Utility.dll": (member, expected)})
    monkeypatch.setattr(bootstrap, "RELEASE_SIZE", archive.stat().st_size)
    monkeypatch.setattr(bootstrap, "RELEASE_SHA256", bootstrap.digest(archive))
    return archive, payload


def test_only_fixed_dependency_members_are_extracted(tmp_path, monkeypatch):
    _, payload = fake_release(tmp_path, monkeypatch)
    monkeypatch.setattr(bootstrap, "download_release", lambda *_: pytest.fail("verified cache should be reused"))
    bootstrap.ensure_dependencies(tmp_path)
    assert (tmp_path / "dependencies/Utility.dll").read_bytes() == payload
    assert not (tmp_path.parent / "outside.dll").exists()
    bootstrap.ensure_dependencies(tmp_path)


def test_duplicate_fixed_dependency_is_rejected(tmp_path, monkeypatch):
    fake_release(tmp_path, monkeypatch, duplicate=True)
    with pytest.raises(ValueError, match="duplicated"):
        bootstrap.ensure_dependencies(tmp_path)
    assert not (tmp_path / "dependencies").exists()


def test_release_download_checks_size_and_hash_before_publication(tmp_path, monkeypatch):
    payload = b"synthetic release"
    monkeypatch.setattr(bootstrap, "RELEASE_SIZE", len(payload))
    monkeypatch.setattr(bootstrap, "RELEASE_SHA256", hashlib.sha256(payload).hexdigest())
    monkeypatch.setattr(bootstrap, "build_opener", lambda *_: SimpleNamespace(open=lambda *args, **kwargs: io.BytesIO(payload)))
    target = tmp_path / "release.zip"
    bootstrap.download_release(target)
    assert target.read_bytes() == payload
    monkeypatch.setattr(bootstrap, "RELEASE_SHA256", "0" * 64)
    with pytest.raises(ValueError, match="fingerprint"):
        bootstrap.download_release(target)
    assert target.read_bytes() == payload
    assert not list(tmp_path.glob("*.partial"))


def test_release_download_rejects_external_or_plaintext_redirects():
    redirect = bootstrap.ReleaseRedirect()
    for url in ("http://github.com/release", "https://attacker.example/payload", "https://github.com@attacker.example/file"):
        with pytest.raises(ValueError, match="redirect"):
            redirect.redirect_request(Request(bootstrap.RELEASE_URL), None, 302, "", {}, url)
