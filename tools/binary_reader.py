from __future__ import annotations

import struct


class BinaryFormatError(ValueError):
    pass


class BinaryReader:
    def __init__(self, data: bytes, label: str = "binary") -> None:
        self.data = data
        self.label = label

    def require(self, offset: int, size: int) -> None:
        if offset < 0 or size < 0 or offset > len(self.data) - size:
            raise BinaryFormatError(f"{self.label}: invalid range {offset}+{size}")

    def take(self, offset: int, size: int) -> bytes:
        self.require(offset, size)
        return self.data[offset : offset + size]

    def unpack(self, fmt: str, offset: int) -> tuple:
        self.require(offset, struct.calcsize(fmt))
        return struct.unpack_from(fmt, self.data, offset)

    def uint(self, offset: int, width: int = 4) -> int:
        return int.from_bytes(self.take(offset, width), "little")

    def cstring(self, offset: int, limit: int | None = None) -> str:
        stop = len(self.data) if limit is None else limit
        self.require(offset, stop - offset)
        end = self.data.find(b"\0", offset, stop)
        if end == -1:
            raise BinaryFormatError(f"{self.label}: unterminated string at {offset}")
        try:
            return self.data[offset:end].decode("utf-8")
        except UnicodeDecodeError as error:
            raise BinaryFormatError(f"{self.label}: invalid UTF-8 at {offset}") from error


def compressed_uint(reader: BinaryReader, offset: int) -> tuple[int, int]:
    first = reader.uint(offset, 1)
    if first < 0x80:
        return first, offset + 1
    if first < 0xC0:
        return ((first & 0x3F) << 8) | reader.uint(offset + 1, 1), offset + 2
    if first < 0xE0:
        tail = int.from_bytes(reader.take(offset + 1, 3), "big")
        return ((first & 0x1F) << 24) | tail, offset + 4
    raise BinaryFormatError(f"{reader.label}: invalid compressed integer at {offset}")
