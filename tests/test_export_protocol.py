from __future__ import annotations

import contextlib
import io
import struct
import tempfile
import unittest
from pathlib import Path

from tools.binary_reader import BinaryFormatError, BinaryReader, compressed_uint
from tools.export_protocol import export_protocol, main, select_protocol_literals
from tools.il2cpp_metadata import read_literals, read_manifest_identity
from tools.managed_metadata import ManagedMetadata


def metadata_fixture(values: list[str]) -> bytes:
    encoded = [value.encode("utf-8") for value in values]
    offsets = [0]
    for value in encoded:
        offsets.append(offsets[-1] + len(value))
    index = struct.pack(f"<{len(offsets)}I", *offsets)
    header = struct.pack("<8I", 0xFAB11BAF, 39, 32, len(index), len(offsets), 32 + len(index), offsets[-1], len(values))
    return header + index + b"".join(encoded)


def managed_fixture() -> bytes:
    strings = bytearray(b"\0")
    indices = {}
    for value in ["BaseResponse", "HomeResponse", "Il2CppProject.Api", "NativeFieldInfoPtr_status", "get_status", "Send", "payload"]:
        indices[value] = len(strings)
        strings.extend(value.encode() + b"\0")
    blobs = bytearray(b"\0")

    def blob(value: bytes) -> int:
        index = len(blobs)
        blobs.extend(bytes([len(value)]) + value)
        return index

    field_signature = blob(b"\x06\x18")
    getter_signature = blob(b"\x20\x00\x0e")
    send_signature = blob(b"\x20\x01\x02\x0e")
    valid = (1 << 2) | (1 << 4) | (1 << 6) | (1 << 8)
    tables = bytearray(struct.pack("<IBBBBQQ", 0, 2, 0, 0, 1, valid, 0))
    tables.extend(struct.pack("<4I", 2, 1, 2, 1))
    tables.extend(struct.pack("<I5H", 1, indices["BaseResponse"], indices["Il2CppProject.Api"], 0, 1, 1))
    tables.extend(struct.pack("<I5H", 1, indices["HomeResponse"], indices["Il2CppProject.Api"], 4, 2, 2))
    tables.extend(struct.pack("<3H", 0x10, indices["NativeFieldInfoPtr_status"], field_signature))
    tables.extend(struct.pack("<I5H", 0, 0, 6, indices["get_status"], getter_signature, 1))
    tables.extend(struct.pack("<I5H", 0, 0, 6, indices["Send"], send_signature, 1))
    tables.extend(struct.pack("<3H", 0, 1, indices["payload"]))
    version = b"v4.0.30319\0\0"
    root = bytearray(struct.pack("<IHHII", 0x424A5342, 1, 1, 0, len(version)) + version)
    root.extend(struct.pack("<HH", 0, 3))
    stream_start = len(root) + 12 + 20 + 16
    payload = bytearray()
    for name, content in [("#~", tables), ("#Strings", strings), ("#Blob", blobs)]:
        root.extend(struct.pack("<II", stream_start + len(payload), len(content)))
        stream_name = name.encode() + b"\0"
        root.extend(stream_name + b"\0" * (-len(stream_name) % 4))
        payload.extend(content)
    root.extend(payload)
    image = bytearray(0x300 + len(root))
    image[:2] = b"MZ"
    struct.pack_into("<I", image, 0x3C, 0x80)
    image[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", image, 0x86, 1)
    struct.pack_into("<H", image, 0x94, 224)
    struct.pack_into("<H", image, 0x98, 0x10B)
    struct.pack_into("<II", image, 0x98 + 96 + 14 * 8, 0x2000, 72)
    struct.pack_into("<4I", image, 0x98 + 224 + 8, len(image) - 0x200, 0x2000, len(image) - 0x200, 0x200)
    struct.pack_into("<II", image, 0x208, 0x2100, len(root))
    image[0x300:] = root
    return bytes(image)


class LiteralMetadataTests(unittest.TestCase):
    def test_adjacent_offsets_preserve_empty_unicode_and_embedded_null(self):
        expected = ["", "api/home", "简体中文", "a\0b"]
        self.assertEqual(read_literals(metadata_fixture(expected)), expected)

    def test_descending_offsets_are_rejected(self):
        fixture = bytearray(metadata_fixture(["abcd", "ef"]))
        struct.pack_into("<I", fixture, 36, 7)
        with self.assertRaisesRegex(BinaryFormatError, "not ordered"):
            read_literals(bytes(fixture))

    def test_literal_data_outside_file_is_rejected(self):
        fixture = bytearray(metadata_fixture(["api/home"]))
        struct.pack_into("<I", fixture, 20, len(fixture) + 1)
        with self.assertRaisesRegex(BinaryFormatError, "invalid range"):
            read_literals(bytes(fixture))

    def test_overlapping_sections_are_rejected(self):
        fixture = bytearray(metadata_fixture(["api/home"]))
        struct.pack_into("<I", fixture, 20, 32)
        with self.assertRaisesRegex(BinaryFormatError, "overlapping"):
            read_literals(bytes(fixture))

    def test_inconsistent_counts_and_version_are_rejected(self):
        for offset, value in [(16, 999), (28, 999), (4, 38)]:
            with self.subTest(offset=offset):
                fixture = bytearray(metadata_fixture(["api/home"]))
                struct.pack_into("<I", fixture, offset, value)
                with self.assertRaises(BinaryFormatError):
                    read_literals(bytes(fixture))

    def test_invalid_utf8_is_rejected(self):
        fixture = bytearray(metadata_fixture(["x"]))
        fixture[-1] = 0xFF
        with self.assertRaisesRegex(BinaryFormatError, "UTF-8"):
            read_literals(bytes(fixture))


class ManagedMetadataTests(unittest.TestCase):
    def test_inheritance_and_interop_getter_types(self):
        metadata = ManagedMetadata(managed_fixture())
        types = metadata.types(("Il2CppProject.Api",))
        self.assertEqual(types[0]["fields"], [{"name": "status", "type": "string", "evidence": "interop_field_getter"}])
        self.assertEqual(types[1]["base_type"], "Il2CppProject.Api.BaseResponse")
        self.assertEqual(types[1]["methods"][0]["return_type"], "bool")
        self.assertEqual(types[1]["methods"][0]["parameters"], [{"name": "payload", "type": "string"}])

    def test_missing_or_invalid_pe_sections_are_rejected(self):
        for fixture in [b"", b"MZ", managed_fixture()[:0x310]]:
            with self.subTest(size=len(fixture)), self.assertRaises(BinaryFormatError):
                ManagedMetadata(fixture)

    def test_out_of_range_table_index_is_rejected(self):
        metadata = ManagedMetadata(managed_fixture())
        with self.assertRaises(BinaryFormatError):
            metadata.row(2, 3)

    def test_compressed_integer_cannot_overread(self):
        for fixture in [b"\x80", b"\xc0\x01", b"\xff"]:
            with self.subTest(fixture=fixture), self.assertRaises(BinaryFormatError):
                compressed_uint(BinaryReader(fixture), 0)

    def test_excessive_array_rank_is_rejected(self):
        metadata = ManagedMetadata(managed_fixture())
        with self.assertRaisesRegex(BinaryFormatError, "array rank"):
            metadata.signature_type(BinaryReader(b"\x14\x08\x7f\x00\x00"), 0)


class ExportCommandTests(unittest.TestCase):
    def test_only_protocol_literal_names_are_exported(self):
        result = select_protocol_literals([
            "api/home", "api/home", "api/chapter/{0}", "api/home?token=private",
            "x-olg-session", "x-olg-session: private", "private-signing-key",
            "application/x-msgpack", "login", "https://private.example/api/home",
        ])
        self.assertEqual(result["routes"], ["api/chapter/{0}", "api/home"])
        self.assertEqual(result["headers"], ["x-olg-session"])
        self.assertEqual(result["content_types"], ["application/x-msgpack"])

    def test_missing_input_does_not_create_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with contextlib.redirect_stderr(io.StringIO()):
                result = main(["--unpacked", str(root / "absent"), "--output", str(root / "out.json")])
            self.assertEqual(result, 1)
            self.assertFalse((root / "out.json").exists())

    def test_missing_required_file_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(FileNotFoundError, "global-metadata.dat"):
                export_protocol(Path(temporary))

    def test_truncated_manifest_is_rejected(self):
        with self.assertRaises(BinaryFormatError):
            read_manifest_identity(b"\x03\x00\x08\x00")


if __name__ == "__main__":
    unittest.main()
