from __future__ import annotations

from .binary_reader import BinaryFormatError, BinaryReader


def read_literals(data: bytes) -> list[str]:
    reader = BinaryReader(data, "IL2CPP metadata")
    magic, version = reader.unpack("<II", 0)
    if magic != 0xFAB11BAF:
        raise BinaryFormatError("IL2CPP metadata: invalid magic")
    if version != 39:
        raise BinaryFormatError(f"IL2CPP metadata: expected version 39, found {version}")
    offset, size, count, data_offset, data_size, literal_count = reader.unpack("<6I", 8)
    if count < 1 or size != count * 4 or literal_count != count - 1:
        raise BinaryFormatError("IL2CPP metadata: inconsistent literal index counts")
    if offset < 32 or data_offset < offset + size:
        raise BinaryFormatError("IL2CPP metadata: overlapping literal sections")
    reader.require(offset, size)
    reader.require(data_offset, data_size)
    offsets = reader.unpack(f"<{count}I", offset)
    if offsets[0] != 0 or offsets[-1] != data_size:
        raise BinaryFormatError("IL2CPP metadata: invalid literal data boundaries")
    if any(left > right for left, right in zip(offsets, offsets[1:])):
        raise BinaryFormatError("IL2CPP metadata: literal offsets are not ordered")
    try:
        return [
            reader.take(data_offset + left, right - left).decode("utf-8")
            for left, right in zip(offsets, offsets[1:])
        ]
    except UnicodeDecodeError as error:
        raise BinaryFormatError("IL2CPP metadata: invalid UTF-8 literal") from error


def read_manifest_identity(data: bytes) -> dict:
    reader = BinaryReader(data, "Android manifest")
    kind, header_size, total_size = reader.unpack("<HHI", 0)
    if kind != 3 or header_size < 8 or total_size != len(data):
        raise BinaryFormatError("Android manifest: invalid XML header")
    strings: list[str] = []
    offset = header_size
    while offset < total_size:
        kind, size, length = reader.unpack("<HHI", offset)
        if size < 8 or length < size:
            raise BinaryFormatError("Android manifest: invalid chunk size")
        reader.require(offset, length)
        if kind == 1:
            strings = _read_string_pool(reader, offset, size, length)
        elif kind == 0x102:
            extension = offset + 16
            name_index = reader.uint(extension + 4)
            name = _string(strings, name_index)
            if name == "manifest":
                start, attribute_size, count = reader.unpack("<HHH", extension + 8)
                if attribute_size < 20:
                    raise BinaryFormatError("Android manifest: invalid attribute size")
                result = {}
                for index in range(count):
                    position = extension + start + index * attribute_size
                    if position + 20 > offset + length:
                        raise BinaryFormatError("Android manifest: attribute exceeds chunk")
                    key = _string(strings, reader.uint(position + 4))
                    if key not in {"package", "versionCode", "versionName"}:
                        continue
                    raw = reader.uint(position + 8)
                    data_type = reader.uint(position + 15, 1)
                    value = reader.uint(position + 16)
                    result[key] = (
                        _string(strings, raw)
                        if raw != 0xFFFFFFFF
                        else _string(strings, value) if data_type == 3 else value
                    )
                return result
        offset += length
    raise BinaryFormatError("Android manifest: manifest element missing")


def _string(strings: list[str], index: int) -> str:
    if index >= len(strings):
        raise BinaryFormatError(f"Android manifest: invalid string index {index}")
    return strings[index]


def _read_string_pool(reader: BinaryReader, offset: int, header: int, size: int) -> list[str]:
    if header < 28:
        raise BinaryFormatError("Android manifest: truncated string pool header")
    count = reader.uint(offset + 8)
    utf8 = bool(reader.uint(offset + 16) & 0x100)
    start = reader.uint(offset + 20)
    if header + count * 4 > size or not header + count * 4 <= start <= size:
        raise BinaryFormatError("Android manifest: invalid string pool")

    def length(position: int) -> tuple[int, int]:
        width, mask = (1, 0x80) if utf8 else (2, 0x8000)
        value = reader.uint(position, width)
        position += width
        if value & mask:
            value = ((value & (mask - 1)) << (width * 8)) | reader.uint(position, width)
            position += width
        return value, position

    result = []
    for index in range(count):
        position = offset + start + reader.uint(offset + header + index * 4)
        if utf8:
            _, position = length(position)
        count_bytes, position = length(position)
        count_bytes *= 1 if utf8 else 2
        if position + count_bytes > offset + size:
            raise BinaryFormatError("Android manifest: string exceeds pool")
        try:
            result.append(reader.take(position, count_bytes).decode("utf-8" if utf8 else "utf-16le"))
        except UnicodeDecodeError as error:
            raise BinaryFormatError("Android manifest: invalid string encoding") from error
    return result
