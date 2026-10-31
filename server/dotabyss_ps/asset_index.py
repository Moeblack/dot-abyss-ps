from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import struct
import tempfile
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit

from .assets import AssetDataError, CRI_PROVIDER, MAX_ASSET_BYTES, MAX_MANIFEST_BYTES, UNITY_PROVIDER, hexadecimal, read_bounded, safe_relative

PC_CATALOG_PREFIX = "resources/windows/r18/aas/0.1.0/aa"
PC_CATALOG_PATH = PC_CATALOG_PREFIX + "/catalog_1.bin"
PC_CATALOG_HASH_PATH = PC_CATALOG_PREFIX + "/catalog_1.hash"
DEFAULT_REMOTE_BASE = "https://pc-local.invalid/" + PC_CATALOG_PREFIX
REMOTE_PROPERTY = "{Absf.Asset.AddressableAssets.AddressablesProfileDefine.RemoteLoadPath}"
LOCAL_PROPERTY = "{Absf.Asset.AddressableAssets.AddressablesProfileDefine.LocalLoadPath}"
BUNDLED_PROVIDER = "UnityEngine.ResourceManagement.ResourceProviders.BundledAssetProvider"
OPTIONS_TYPE = "UnityEngine.ResourceManagement.ResourceProviders.AssetBundleRequestOptions"
CATALOG_MAGIC = 0x0DE38942
NULL_OFFSET = 0xFFFFFFFF
UNICODE_FLAG = 0x80000000
DYNAMIC_FLAG = 0x40000000
OFFSET_MASK = 0x3FFFFFFF
MAX_OBJECTS = 200_000
MAX_REFERENCES = 2_000_000
MAX_STRING_CHARS = 65_536


class _CatalogReader:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.strings = {}
        self.types = {}
        self.string_chars = 0

    def take(self, offset: int, size: int) -> bytes:
        if offset < 0 or size < 0 or offset > len(self.payload) - size:
            raise AssetDataError("Catalog reference leaves the file")
        return self.payload[offset:offset + size]

    def unpack(self, layout: str, offset: int) -> tuple:
        return struct.unpack(layout, self.take(offset, struct.calcsize(layout)))

    def array(self, offset: int, width: int, *, nullable: bool = True) -> bytes:
        if nullable and offset == NULL_OFFSET:
            return b""
        size, = self.unpack("<I", offset - 4)
        if size % width or size // width > MAX_OBJECTS:
            raise AssetDataError("Catalog array has an invalid length")
        return self.take(offset, size)

    def offsets(self, offset: int) -> list[int]:
        return [item[0] for item in struct.iter_unpack("<I", self.array(offset, 4))]

    def string(self, offset: int, separator: str = "", *, nullable: bool = False) -> str | None:
        if nullable and offset == NULL_OFFSET:
            return None
        if offset == NULL_OFFSET:
            raise AssetDataError("Required catalog string is null")
        key = (offset, separator)
        if key in self.strings:
            return self.strings[key]
        if offset & DYNAMIC_FLAG:
            if not separator or offset & UNICODE_FLAG:
                raise AssetDataError("Catalog dynamic string has invalid flags or separator")
            pieces, seen, count = [], set(), 0
            current = offset
            while current != NULL_OFFSET:
                location = current & OFFSET_MASK
                if current & UNICODE_FLAG or location in seen or len(seen) >= 1024:
                    raise AssetDataError("Catalog dynamic string is cyclic or too deep")
                seen.add(location)
                part, current = self.unpack("<II", location)
                value = self.string(part)
                count += len(value) + bool(pieces)
                if count > MAX_STRING_CHARS:
                    raise AssetDataError("Catalog string exceeds its length limit")
                pieces.append(value)
            result = separator.join(reversed(pieces))
        else:
            location = offset & OFFSET_MASK
            size, = self.unpack("<I", location - 4)
            unicode = bool(offset & UNICODE_FLAG)
            if size > MAX_STRING_CHARS * (2 if unicode else 1) or unicode and size % 2:
                raise AssetDataError("Catalog string has an invalid length")
            try:
                result = self.take(location, size).decode("utf-16-le" if unicode else "ascii")
            except UnicodeError as error:
                raise AssetDataError("Catalog string has invalid encoding") from error
        self.string_chars += len(result)
        if self.string_chars > MAX_ASSET_BYTES:
            raise AssetDataError("Decoded catalog strings exceed their total size limit")
        self.strings[key] = result
        return result

    def type_name(self, offset: int) -> tuple[str, str]:
        if offset not in self.types:
            assembly, name = self.unpack("<II", offset)
            value = self.string(assembly, "."), self.string(name, ".")
            if not all(value):
                raise AssetDataError("Catalog type descriptor is empty")
            self.types[offset] = value
        return self.types[offset]

    def typed_object(self, offset: int) -> tuple[tuple[str, str], int]:
        descriptor, data = self.unpack("<II", offset)
        self.take(data, 1)
        return self.type_name(descriptor), data

    def key(self, offset: int) -> tuple[str, object]:
        (_, name), data = self.typed_object(offset)
        if name == "System.String":
            text, separator = self.unpack("<IH2x", data)
            value = self.string(text, chr(separator) if separator else "")
        elif name in ("System.Int32", "System.Int64"):
            value, = self.unpack("<i" if name == "System.Int32" else "<q", data)
        elif name == "System.Boolean":
            value, = self.unpack("<B", data)
            if value not in (0, 1):
                raise AssetDataError("Catalog boolean key is invalid")
        elif name == "UnityEngine.Hash128":
            value = self.take(data, 16).hex()
        else:
            raise AssetDataError("Unsupported catalog key type: " + name[:160])
        return name, value

    def initialization(self, offset: int) -> None:
        if offset == NULL_OFFSET:
            return
        name, descriptor, data = self.unpack("<III", offset)
        self.string(name)
        self.type_name(descriptor)
        self.string(data, nullable=True)

    def options(self, offset: int) -> dict | None:
        if offset == NULL_OFFSET:
            return None
        (assembly, name), data = self.typed_object(offset)
        if name != OPTIONS_TYPE or assembly.split(",", 1)[0] != "Unity.ResourceManager":
            raise AssetDataError("Unsupported catalog resource options type")
        digest, bundle, _, size, common = self.unpack("<IIIII", data)
        timeout, redirects, retries, flags = self.unpack("<hBBI", common)
        if timeout < 0 or redirects > 128 or retries > 128 or flags & ~31:
            raise AssetDataError("Catalog resource options are outside the supported format")
        key = self.string(bundle, "_")
        if not key or len(key) > 255 or "/" in safe_relative(key):
            raise AssetDataError("Catalog cache key is invalid")
        if not 0 < size <= MAX_ASSET_BYTES:
            raise AssetDataError("Catalog resource size is outside its limit")
        return {"cache_key": key, "hash128": self.take(digest, 16).hex(), "bundle_size": size}


def _remote_base(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 2048 or any(ord(char) < 33 or ord(char) > 126 for char in value):
        raise AssetDataError("PC resource base must be a URL")
    try:
        parsed = urlsplit(value)
        parsed.port
    except ValueError as error:
        raise AssetDataError("PC resource base is invalid") from error
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment
            or parsed.path.rstrip("/") != "/" + PC_CATALOG_PREFIX):
        raise AssetDataError("PC resource base does not match the supported catalog namespace")
    return parsed._replace(path="/" + PC_CATALOG_PREFIX).geturl()


def _decode(payload: bytes, remote_base: str) -> tuple[str, list[dict]]:
    reader = _CatalogReader(payload)
    magic, version, keys, locator, instance, scene, providers, build_hash = reader.unpack("<8I", 0)
    if magic != CATALOG_MAGIC or version != 2:
        raise AssetDataError("Unsupported Addressables catalog header or version")
    if reader.string(locator) != "AddressablesMainContentCatalog":
        raise AssetDataError("PC catalog locator does not match the supported client")
    result_hash = reader.string(build_hash)
    if not hexadecimal(result_hash, 32):
        raise AssetDataError("Catalog build-result hash is invalid")
    for offset in (instance, scene, *reader.offsets(providers)):
        reader.initialization(offset)
    key_data = reader.array(keys, 8, nullable=False)
    if not key_data:
        raise AssetDataError("Catalog has no keys")
    pending, seen_keys, references = deque(), set(), 0
    for key_id, set_id in struct.iter_unpack("<II", key_data):
        key = reader.key(key_id)
        if key in seen_keys:
            raise AssetDataError("Catalog contains duplicate keys")
        seen_keys.add(key)
        locations = reader.offsets(set_id)
        if not locations:
            raise AssetDataError("Catalog key has no resource locations")
        references += len(locations)
        if references > MAX_REFERENCES:
            raise AssetDataError("Catalog has too many resource references")
        pending.extend(locations)
    seen_locations, records, cache_tuples = set(), {}, {}
    while pending:
        offset = pending.popleft()
        if offset in seen_locations:
            continue
        seen_locations.add(offset)
        if len(seen_locations) > MAX_OBJECTS:
            raise AssetDataError("Catalog has too many resource locations")
        primary, internal, provider_id, dependencies, _, extra, resource_type = reader.unpack("<7I", offset)
        reader.string(primary, "/")
        internal_id = reader.string(internal, "/")
        provider = reader.string(provider_id, ".")
        reader.type_name(resource_type)
        options = reader.options(extra)
        related = reader.offsets(dependencies)
        references += len(related)
        if references > MAX_REFERENCES:
            raise AssetDataError("Catalog has too many resource references")
        pending.extend(related)
        if provider == BUNDLED_PROVIDER:
            if options is not None or not (internal_id.startswith("Assets/") or hexadecimal(internal_id, 32)):
                raise AssetDataError("Unsupported bundled catalog location")
            continue
        if provider not in (UNITY_PROVIDER, CRI_PROVIDER) or options is None:
            raise AssetDataError("Unsupported catalog resource provider or missing options")
        if internal_id.startswith((LOCAL_PROPERTY + "/", LOCAL_PROPERTY + "\\")):
            safe_relative(internal_id[len(LOCAL_PROPERTY) + 1:].replace("\\", "/"))
            continue
        if not internal_id.startswith(REMOTE_PROPERTY + "/"):
            raise AssetDataError("Catalog resource uses an unknown load path")
        relative = safe_relative(PC_CATALOG_PREFIX + internal_id[len(REMOTE_PROPERTY):])
        if relative in (PC_CATALOG_PATH, PC_CATALOG_HASH_PATH):
            raise AssetDataError("Catalog resource collides with a catalog URL")
        record = {"relative_path": relative, "resolved_url": remote_base + internal_id[len(REMOTE_PROPERTY):],
                  **options, "provider": provider}
        identity = (options["cache_key"].lower(), options["hash128"])
        if relative in records and records[relative] != record or identity in cache_tuples and cache_tuples[identity] != record:
            raise AssetDataError("Catalog contains conflicting resource paths or cache locations")
        records[relative] = cache_tuples[identity] = record
        if len(records) > 50_000:
            raise AssetDataError("Catalog remote resource count is outside its limit")
    if not records:
        raise AssetDataError("Catalog remote resource count is outside its limit")
    return result_hash, [records[path] for path in sorted(records)]


def build_index(catalog_path: Path, catalog_hash_path: Path, *, remote_base: str = DEFAULT_REMOTE_BASE) -> dict:
    remote_base = _remote_base(remote_base)
    payload = read_bounded(Path(catalog_path), MAX_ASSET_BYTES)
    try:
        version_hash = read_bounded(Path(catalog_hash_path), 128).decode("ascii").strip()
    except UnicodeError as error:
        raise AssetDataError("Catalog version hash is not ASCII") from error
    if not hexadecimal(version_hash, 32):
        raise AssetDataError("Catalog version hash is invalid")
    build_hash, records = _decode(payload, remote_base)
    return {"format_version": 1, "catalog_sha256": hashlib.sha256(payload).hexdigest(),
            "catalog_build_result_hash": build_hash, "catalog_version_hash": version_hash,
            "binding": {"remote_base": remote_base, "kind": "local_logical_namespace", "network_access_required": False},
            "records": records}


def _validate_output(path: Path, sources: tuple[Path, ...]) -> None:
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or getattr(ancestor, "is_junction", lambda: False)():
            raise AssetDataError("Index output must not use symbolic links or junctions")
    try:
        existing = path.stat()
    except FileNotFoundError:
        existing = None
    if existing is not None and (not stat.S_ISREG(existing.st_mode) or existing.st_nlink > 1):
        raise AssetDataError("Index output must be an ordinary file, not a directory or link")
    for source in sources:
        if path.resolve() == source.resolve() or existing is not None and path.samefile(source):
            raise AssetDataError("Index output must not overwrite its source catalog or hash")


def _write_index(path: Path, index: dict, sources: tuple[Path, ...]) -> None:
    _validate_output(path, sources)
    payload = (json.dumps(index, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(payload) > MAX_MANIFEST_BYTES:
        raise AssetDataError("Generated index exceeds its size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".asset-index-", suffix=".partial", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _validate_output(path, sources)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Generate a verified PC resource index from an Addressables v2 catalog")
    parser.add_argument("--catalog", type=Path, required=True)
    parser.add_argument("--catalog-hash", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--remote-base", default=DEFAULT_REMOTE_BASE)
    args = parser.parse_args(argv)
    output = args.output.absolute()
    sources = args.catalog, args.catalog_hash
    try:
        _validate_output(output, sources)
        index = build_index(*sources, remote_base=args.remote_base)
        _write_index(output, index, sources)
    except (AssetDataError, OSError) as error:
        parser.exit(1, "Resource index error: " + str(error) + "\n")
    print(json.dumps({"output": str(output), "records": len(index["records"]),
                      "catalog_sha256": index["catalog_sha256"], "catalog_version_hash": index["catalog_version_hash"],
                      "catalog_path": PC_CATALOG_PATH, "catalog_hash_path": PC_CATALOG_HASH_PATH}, ensure_ascii=True))


if __name__ == "__main__":
    main()
