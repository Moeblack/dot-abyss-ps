from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import struct
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlsplit

from .masterdata import canonical_json
from .newgame import SUPPORTED_CLIENTS
from .storage import Store

MAX_ASSET_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 32 * 1024 * 1024
UNITY_PROVIDER = "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleProvider"
CRI_PROVIDER = "CriWare.Assets.CriResourceProvider"
NAMESPACES = {
    "com.exnoa.abyss": "resources/android_googleplay/normal/",
    "jp.co.fanzagames.dotabyss_x_a.mod": "resources/android/r18/",
    "dotabyss_x_cl": "resources/windows/r18/",
}


class AssetDataError(ValueError):
    pass


class MissingAssetError(AssetDataError):
    pass


def safe_relative(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 4096:
        raise AssetDataError("Invalid asset path")
    if any(part in ("", ".", "..") or not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in value.split("/")):
        raise AssetDataError("Invalid asset path")
    return value


def read_bounded(path: Path, limit: int) -> bytes:
    try:
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if not 0 < before.st_size <= limit:
                raise AssetDataError("Asset file is empty or exceeds its size limit: " + path.name)
            payload = stream.read(before.st_size + 1)
            after = os.fstat(stream.fileno())
    except OSError as exc:
        raise AssetDataError("Asset file is unavailable: " + path.name) from exc
    if len(payload) != before.st_size or (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        raise AssetDataError("Asset file changed while its snapshot was being read: " + path.name)
    return payload


def json_document(payload: bytes) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise AssetDataError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(_):
        raise AssetDataError("Non-finite JSON value")

    try:
        result = json.loads(payload, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise AssetDataError("Invalid asset JSON") from exc
    if not isinstance(result, dict):
        raise AssetDataError("Asset JSON must be an object")
    return result


def hexadecimal(value, length: int) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value) is not None


def validate_usm(payload: bytes) -> None:
    position = 0
    chunks = 0
    while position < len(payload):
        if position + 32 > len(payload):
            raise AssetDataError("Truncated USM chunk header")
        signature = payload[position:position + 4]
        allowed = (b"CRID",) if position == 0 else (b"@SFV", b"@SFA", b"@SBT", b"@ALP")
        if signature not in allowed:
            raise AssetDataError("Unsupported USM chunk signature")
        size = struct.unpack_from(">I", payload, position + 4)[0]
        offset = payload[position + 9]
        padding = struct.unpack_from(">H", payload, position + 10)[0]
        end = position + 8 + size
        if size < 24 or end > len(payload) or not 24 <= offset <= size or padding > size - offset:
            raise AssetDataError("USM chunk length, payload offset or padding leaves its boundary")
        position = end
        chunks += 1
        if chunks > 1000000:
            raise AssetDataError("USM chunk count exceeds its limit")
    if chunks < 2:
        raise AssetDataError("USM container has no stream chunks")


def validate_payload(payload: bytes, provider: str, expected_size: int) -> str:
    if type(expected_size) is not int or not 0 < expected_size <= MAX_ASSET_BYTES or len(payload) != expected_size:
        raise AssetDataError("Cached payload length does not match the catalog")
    if provider == UNITY_PROVIDER:
        position = 0

        def string():
            nonlocal position
            end = payload.find(b"\0", position, position + 128)
            if end < 0:
                raise AssetDataError("Truncated UnityFS string header")
            value = payload[position:end]
            if not value or any(byte < 32 or byte > 126 for byte in value):
                raise AssetDataError("Invalid UnityFS string header")
            position = end + 1
            return value

        if string() != b"UnityFS" or position + 4 > len(payload):
            raise AssetDataError("Expected a UnityFS payload")
        version = struct.unpack_from(">I", payload, position)[0]
        position += 4
        if version not in (6, 7, 8):
            raise AssetDataError("Unsupported UnityFS format version")
        string()
        string()
        if position + 20 > len(payload):
            raise AssetDataError("Truncated UnityFS size header")
        size, compressed, uncompressed, flags = struct.unpack_from(">QIII", payload, position)
        position += 20
        info_start = size - compressed if flags & 0x80 else ((position + 15) & ~15 if version >= 7 else position)
        if size != len(payload) or not 0 < compressed <= size or not 0 < uncompressed <= MAX_ASSET_BYTES or flags & 0x3F not in (0, 1, 2, 3):
            raise AssetDataError("Invalid UnityFS declared length or block info size")
        if info_start < position or info_start + compressed > size:
            raise AssetDataError("UnityFS block info leaves the payload")
        return "UnityFS"
    if provider == CRI_PROVIDER and payload[:4] == b"CRID":
        validate_usm(payload)
        return "USM"
    if provider != CRI_PROVIDER or len(payload) < 16 or payload[:4] != b"AFS2":
        raise AssetDataError("Expected a supported UnityFS, AFS2 or USM payload")
    version, offset_size = payload[4:6]
    id_stride, count, alignment = struct.unpack_from("<HiH", payload, 6)
    if version not in (1, 2) or offset_size not in (2, 4) or id_stride not in (2, 4) or not 0 < count <= 1000000 or alignment == 0:
        raise AssetDataError("Unsupported AFS2 table header")
    start = 16 + count * id_stride
    end = start + (count + 1) * offset_size
    if end > len(payload):
        raise AssetDataError("Truncated AFS2 offset table")
    offsets = [int.from_bytes(payload[index:index + offset_size], "little") for index in range(start, end, offset_size)]
    if offsets[0] < end or offsets[-1] != len(payload) or any(left > right for left, right in zip(offsets, offsets[1:])):
        raise AssetDataError("Invalid AFS2 offsets or final length")
    aligned = [((offset + alignment - 1) // alignment) * alignment if offset < len(payload) else offset for offset in offsets]
    if any(left > right or right > len(payload) for left, right in zip(aligned, aligned[1:])):
        raise AssetDataError("AFS2 aligned stream leaves the payload")
    return "AFS2"


def cache_filename(info: bytes, entry: dict) -> str:
    try:
        lines = info.decode("ascii").splitlines()
    except UnicodeError as exc:
        raise AssetDataError("Cache metadata must be ASCII") from exc
    if len(lines) != 4 or lines[0] != "-1" or lines[2] != "1" or not re.fullmatch(r"[0-9]{1,19}", lines[1]):
        raise AssetDataError("Unsupported cache metadata version or file count")
    name = safe_relative(lines[3])
    expected = "__data" if entry["provider"] == UNITY_PROVIDER else entry["relative_path"].rsplit("/", 1)[-1]
    if "/" in name or name != expected:
        raise AssetDataError("Cache metadata filename does not match its provider and catalog path")
    return name


class AssetCatalog:
    def __init__(self, data_dir: Path, store: Store):
        self.root = data_dir.resolve() / "assets"
        self.store = store
        self._cached_revision = None
        self._cached_manifest = None
        self._lock = threading.Lock()

    def _path(self, relative: str) -> Path:
        path = (self.root / safe_relative(relative)).resolve()
        if not path.is_relative_to(self.root):
            raise AssetDataError("Asset storage path leaves the data directory")
        return path

    @staticmethod
    def _snapshot_binding(record: dict | None) -> dict:
        if record is None or record.get("client") not in SUPPORTED_CLIENTS:
            raise AssetDataError("Assets require an active master for a verified client")
        return {key: record[key] for key in ("revision", "client", "sha256", "schema_sha256")}

    def _check_binding(self, manifest: dict) -> None:
        if self._snapshot_binding(self.store.master()) != manifest["master"]:
            raise AssetDataError("Active assets do not match the active master revision and client")

    @staticmethod
    def _atomic(path: Path, payload: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False, suffix=".partial") as stream:
                temporary = Path(stream.name)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()

    def _blob(self, payload: bytes) -> dict:
        digest = hashlib.sha256(payload).hexdigest()
        path = self._path("blobs/" + digest)
        if path.exists():
            if hashlib.sha256(read_bounded(path, MAX_ASSET_BYTES)).hexdigest() != digest:
                raise AssetDataError("Existing immutable asset blob is corrupt")
        else:
            self._atomic(path, payload)
        return {"sha256": digest, "bytes": len(payload)}

    def _read_blob(self, entry: dict) -> bytes:
        if not isinstance(entry, dict) or not hexadecimal(entry.get("sha256"), 64) or type(entry.get("bytes")) is not int or not 0 < entry["bytes"] <= MAX_ASSET_BYTES:
            raise AssetDataError("Invalid asset blob descriptor")
        payload = read_bounded(self._path("blobs/" + entry["sha256"]), entry["bytes"])
        if len(payload) != entry["bytes"] or hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise AssetDataError("Asset blob integrity check failed")
        return payload

    def stage(self, index_path: Path, catalog_path: Path, hash_path: Path, cache_root: Path, source: str, *,
              allow_partial: bool = False, catalog_relative_path: str | None = None, catalog_hash_relative_path: str | None = None) -> dict:
        if not isinstance(source, str) or not source.strip() or len(source) > 2048:
            raise AssetDataError("A bounded asset source description is required")
        source.encode("utf-8", errors="strict")
        master = self._snapshot_binding(self.store.master())
        index_bytes = read_bounded(index_path, MAX_MANIFEST_BYTES)
        index = json_document(index_bytes)
        catalog = read_bounded(catalog_path, MAX_ASSET_BYTES)
        catalog_hash = read_bounded(hash_path, 128)
        try:
            hash_value = catalog_hash.decode("ascii").strip()
        except UnicodeError as exc:
            raise AssetDataError("Invalid catalog hash file") from exc
        if not hexadecimal(hash_value, 32) or not hexadecimal(index.get("catalog_build_result_hash"), 32):
            raise AssetDataError("Invalid catalog version hash or build-result hash")
        if "catalog_version_hash" in index and hash_value != index["catalog_version_hash"]:
            raise AssetDataError("Catalog hash file does not match the observed version in the index")
        if hashlib.sha256(catalog).hexdigest() != index.get("catalog_sha256"):
            raise AssetDataError("Catalog bytes do not match the decoded index")
        binding = index.get("binding")
        if not isinstance(binding, dict) or not isinstance(binding.get("remote_base"), str):
            raise AssetDataError("The resource index requires an explicit remote base binding")
        remote = urlsplit(binding["remote_base"])
        prefix = safe_relative(remote.path.lstrip("/").rstrip("/"))
        namespace = NAMESPACES[master["client"]["package"]]
        if remote.scheme != "https" or not remote.hostname or remote.username is not None or remote.password is not None or remote.query or remote.fragment:
            raise AssetDataError("Invalid remote base URL")
        if not prefix.startswith(namespace + "aas/") or not prefix.endswith("/aa"):
            raise AssetDataError("Asset namespace does not match the active master client")
        if bool(catalog_relative_path) != bool(catalog_hash_relative_path):
            raise AssetDataError("Both catalog and hash URL paths must be explicitly supplied")
        catalog_paths = [value for value in (catalog_relative_path, catalog_hash_relative_path) if value is not None]
        for value in catalog_paths:
            if not safe_relative(value).startswith(prefix + "/"):
                raise AssetDataError("Catalog URL must stay inside the bound asset prefix")
        if len(set(catalog_paths)) != len(catalog_paths):
            raise AssetDataError("Catalog and hash URLs must be distinct")
        records = index.get("records")
        if not isinstance(records, list) or not 0 < len(records) <= 50000:
            raise AssetDataError("The resource index requires a bounded record list")
        indexed, tuples = {}, {}
        for record in records:
            if not isinstance(record, dict):
                raise AssetDataError("Invalid resource index record")
            path = safe_relative(record.get("relative_path"))
            key, version, provider, size = (record.get(name) for name in ("cache_key", "hash128", "provider", "bundle_size"))
            if not isinstance(key, str) or "/" in safe_relative(key) or len(key) > 255 or not hexadecimal(version, 32):
                raise AssetDataError("Invalid cache bucket or version")
            if provider not in (UNITY_PROVIDER, CRI_PROVIDER) or type(size) is not int or not 0 < size <= MAX_ASSET_BYTES:
                raise AssetDataError("Unsupported asset provider or length")
            expected_url = remote._replace(path="/" + path, query="", fragment="").geturl()
            if not path.startswith(prefix + "/") or record.get("resolved_url") != expected_url or path in catalog_paths:
                raise AssetDataError("Indexed URL leaves its exact binding or collides with the catalog")
            entry = {"relative_path": path, "cache_key": key, "hash128": version, "provider": provider, "bundle_size": size}
            cache_tuple = (key.lower(), version)
            if (path in indexed and indexed[path] != entry) or (cache_tuple in tuples and tuples[cache_tuple] != entry):
                raise AssetDataError("Conflicting duplicate asset path or cache tuple")
            indexed[path], tuples[cache_tuple] = entry, entry
        cache_root = cache_root.resolve()
        if not cache_root.is_dir():
            raise AssetDataError("Cache root is not a directory")
        entries, missing = {}, []
        for path, entry in sorted(indexed.items()):
            bucket = (cache_root / entry["cache_key"] / entry["hash128"]).resolve()
            if not bucket.is_relative_to(cache_root):
                raise AssetDataError("Cache bucket leaves the cache root")
            info_path = (bucket / "__info").resolve()
            if not info_path.is_relative_to(bucket):
                raise AssetDataError("Cache metadata leaves its bucket")
            if not info_path.is_file():
                missing.append({**entry, "reason": "missing cache metadata"})
                continue
            filename = cache_filename(read_bounded(info_path, 4096), entry)
            payload_path = (bucket / filename).resolve()
            if not payload_path.is_relative_to(bucket):
                raise AssetDataError("Cache payload leaves its bucket")
            if not payload_path.is_file():
                missing.append({**entry, "reason": "missing cache payload"})
                continue
            payload = read_bounded(payload_path, MAX_ASSET_BYTES)
            kind = validate_payload(payload, entry["provider"], entry["bundle_size"])
            name = path.rsplit("/", 1)[-1]
            versioned = name.endswith(("_" + entry["hash128"], "_" + entry["hash128"] + ".bundle"))
            entries[path] = {**entry, **self._blob(payload), "format": kind, "media_type": "application/octet-stream",
                             "cache_control": "public, max-age=31536000, immutable" if versioned else "no-cache"}
        if missing and not allow_partial:
            raise AssetDataError(f"Missing {len(missing)} indexed assets; use explicit partial staging to retain verified files")
        catalog_entry = {**self._blob(catalog), "relative_path": catalog_relative_path, "media_type": "application/octet-stream", "cache_control": "no-cache"}
        hash_entry = {**self._blob(catalog_hash), "relative_path": catalog_hash_relative_path, "media_type": "text/plain", "cache_control": "no-cache"}
        for entry in (catalog_entry, hash_entry):
            if entry["relative_path"] is not None:
                entries[entry["relative_path"]] = entry
        manifest = {"format_version": 1, "source": source, "master": master, "remote_base": binding["remote_base"],
                    "prefix": prefix, "index_sha256": hashlib.sha256(index_bytes).hexdigest(),
                    "catalog": catalog_entry, "catalog_hash": hash_entry, "catalog_hash_value": hash_value,
                    "catalog_build_result_hash": index["catalog_build_result_hash"],
                    "indexed_count": len(indexed), "verified_count": len(indexed) - len(missing),
                    "complete": not missing, "missing": missing, "entries": entries}
        encoded = canonical_json(manifest)
        if len(encoded) > MAX_MANIFEST_BYTES:
            raise AssetDataError("Asset manifest exceeds its size limit")
        revision = "assets-" + hashlib.sha256(encoded).hexdigest()
        destination = self._path("manifests/" + revision + ".json")
        if destination.exists() and read_bounded(destination, MAX_MANIFEST_BYTES) != encoded:
            raise AssetDataError("Existing immutable asset manifest is corrupt")
        if not destination.exists():
            self._atomic(destination, encoded)
        return {"revision": revision, **manifest}

    def manifest(self, revision: str) -> dict:
        if not isinstance(revision, str) or not re.fullmatch(r"assets-[0-9a-f]{64}", revision):
            raise AssetDataError("Invalid asset revision")
        with self._lock:
            if self._cached_revision != revision:
                payload = read_bounded(self._path("manifests/" + revision + ".json"), MAX_MANIFEST_BYTES)
                if hashlib.sha256(payload).hexdigest() != revision.removeprefix("assets-"):
                    raise AssetDataError("Asset manifest integrity check failed")
                manifest = json_document(payload)
                if manifest.get("format_version") != 1 or not isinstance(manifest.get("entries"), dict) or not isinstance(manifest.get("master"), dict):
                    raise AssetDataError("Unsupported asset manifest")
                safe_relative(manifest.get("prefix"))
                self._cached_revision, self._cached_manifest = revision, {"revision": revision, **manifest}
            return self._cached_manifest

    def activate(self, revision: str) -> dict:
        manifest = self.manifest(revision)
        for entry in [manifest["catalog"], manifest["catalog_hash"], *manifest["entries"].values()]:
            self._read_blob(entry)
        with self.store.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._check_binding(manifest)
            self._atomic(self._path("active.json"), canonical_json({"revision": revision}))
        return self.summary(manifest)

    def active(self) -> dict | None:
        pointer = self._path("active.json")
        if not pointer.is_file():
            return None
        document = json_document(read_bounded(pointer, 256))
        if set(document) != {"revision"}:
            raise AssetDataError("Invalid active asset pointer")
        return self.manifest(document["revision"])

    def read(self, path: str) -> tuple[dict, bytes, str] | None:
        safe_relative(path)
        manifest = self.active()
        if manifest is None or not path.startswith(manifest["prefix"] + "/"):
            return None
        self._check_binding(manifest)
        entry = manifest["entries"].get(path)
        if entry is None:
            raise MissingAssetError("Missing indexed asset: " + path[:180])
        return entry, self._read_blob(entry), manifest["revision"]

    @staticmethod
    def summary(manifest: dict) -> dict:
        return {key: manifest[key] for key in ("revision", "master", "prefix", "indexed_count", "verified_count", "complete")} | {
            "missing_count": len(manifest["missing"]), "catalog_served": manifest["catalog"]["relative_path"] is not None,
        }

    def status(self) -> dict:
        try:
            manifest = self.active()
            if manifest is None:
                return {"active": None, "error": ""}
            self._check_binding(manifest)
            return {"active": self.summary(manifest), "error": ""}
        except AssetDataError as exc:
            return {"active": None, "error": str(exc)}


def main():
    parser = argparse.ArgumentParser(description="Stage and activate verified local Android assets")
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    commands = parser.add_subparsers(dest="command", required=True)
    stage = commands.add_parser("stage")
    for name in ("index", "catalog", "catalog-hash", "cache-root"):
        stage.add_argument("--" + name, type=Path, required=True)
    stage.add_argument("--source", required=True)
    stage.add_argument("--allow-partial", action="store_true")
    stage.add_argument("--catalog-relative-path")
    stage.add_argument("--catalog-hash-relative-path")
    activate = commands.add_parser("activate")
    activate.add_argument("revision")
    commands.add_parser("status")
    args = parser.parse_args()
    catalog = AssetCatalog(args.data_dir, Store(args.data_dir / "local.sqlite3"))
    try:
        if args.command == "stage":
            result = catalog.stage(args.index, args.catalog, args.catalog_hash, args.cache_root, args.source,
                                   allow_partial=args.allow_partial, catalog_relative_path=args.catalog_relative_path,
                                   catalog_hash_relative_path=args.catalog_hash_relative_path)
            output = catalog.summary(result)
        elif args.command == "activate":
            output = catalog.activate(args.revision)
        else:
            output = catalog.status()
    except (AssetDataError, OSError) as exc:
        parser.exit(1, str(exc) + "\n")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
