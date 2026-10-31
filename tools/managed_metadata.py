from __future__ import annotations

from functools import lru_cache

from .binary_reader import BinaryFormatError, BinaryReader, compressed_uint


CODED = {
    "ResolutionScope": (2, [0, 26, 35, 1]),
    "TypeDefOrRef": (2, [2, 1, 27]),
    "HasConstant": (2, [4, 8, 23]),
    "HasCustomAttribute": (5, [6, 4, 1, 2, 8, 9, 10, 0, 14, 23, 20, 17, 26, 27, 32, 35, 38, 39, 40, 42, 44, 43]),
    "HasFieldMarshal": (1, [4, 8]),
    "HasDeclSecurity": (2, [2, 6, 32]),
    "MemberRefParent": (3, [2, 1, 26, 6, 27]),
    "HasSemantics": (1, [20, 23]),
    "MethodDefOrRef": (1, [6, 10]),
    "MemberForwarded": (1, [4, 6]),
    "Implementation": (2, [38, 35, 39]),
    "CustomAttributeType": (3, [-1, -1, 6, 10, -1]),
    "TypeOrMethodDef": (1, [2, 6]),
}

TABLES = {
    0: ("u2", "str", "guid", "guid", "guid"),
    1: ("ResolutionScope", "str", "str"),
    2: ("u4", "str", "str", "TypeDefOrRef", 4, 6),
    3: (4,), 4: ("u2", "str", "blob"), 5: (6,),
    6: ("u4", "u2", "u2", "str", "blob", 8),
    7: (8,), 8: ("u2", "u2", "str"), 9: (2, "TypeDefOrRef"),
    10: ("MemberRefParent", "str", "blob"),
    11: ("u2", "HasConstant", "blob"),
    12: ("HasCustomAttribute", "CustomAttributeType", "blob"),
    13: ("HasFieldMarshal", "blob"), 14: ("u2", "HasDeclSecurity", "blob"),
    15: ("u2", "u4", 2), 16: ("u4", 4), 17: ("blob",),
    18: (2, 20), 19: (20,), 20: ("u2", "str", "TypeDefOrRef"),
    21: (2, 23), 22: (23,), 23: ("u2", "str", "blob"),
    24: ("u2", 6, "HasSemantics"), 25: (2, "MethodDefOrRef", "MethodDefOrRef"),
    26: ("str",), 27: ("blob",), 28: ("u2", "MemberForwarded", "str", 26),
    29: ("u4", 4), 30: ("u4", "u4"), 31: ("u4",),
    32: ("u4", "u2", "u2", "u2", "u2", "u4", "blob", "str", "str"),
    33: ("u4",), 34: ("u4", "u4", "u4"),
    35: ("u2", "u2", "u2", "u2", "u4", "blob", "str", "str", "blob"),
    36: ("u4", 35), 37: ("u4", "u4", "u4", 35),
    38: ("u4", "str", "blob"), 39: ("u4", "u4", "str", "str", "Implementation"),
    40: ("u4", "u4", "str", "Implementation"), 41: (2, 2),
    42: ("u2", "u2", "TypeOrMethodDef", "str"),
    43: ("MethodDefOrRef", "blob"), 44: (42, "TypeDefOrRef"),
}

PRIMITIVES = {
    0x01: "void", 0x02: "bool", 0x03: "char", 0x04: "sbyte", 0x05: "byte",
    0x06: "short", 0x07: "ushort", 0x08: "int", 0x09: "uint", 0x0A: "long",
    0x0B: "ulong", 0x0C: "float", 0x0D: "double", 0x0E: "string",
    0x16: "TypedReference", 0x18: "IntPtr", 0x19: "UIntPtr", 0x1C: "object",
}


class ManagedMetadata:
    def __init__(self, data: bytes, label: str = "assembly") -> None:
        self.reader = BinaryReader(data, label)
        self.streams = self._read_streams()
        self.rows: dict[int, int] = {}
        self.positions: dict[int, int] = {}
        self.widths: dict[int, list[int]] = {}
        self.row_sizes: dict[int, int] = {}
        self._read_tables()
        self.nested_types = {
            child: parent for child, parent in (self.row(41, index) for index in range(1, self.rows.get(41, 0) + 1))
        }

    def _read_streams(self) -> dict[str, tuple[int, int]]:
        reader = self.reader
        if reader.take(0, 2) != b"MZ":
            raise BinaryFormatError(f"{reader.label}: DOS signature missing")
        pe = reader.uint(0x3C)
        if reader.take(pe, 4) != b"PE\0\0":
            raise BinaryFormatError(f"{reader.label}: PE signature missing")
        sections = reader.uint(pe + 6, 2)
        optional_size = reader.uint(pe + 20, 2)
        optional = pe + 24
        magic = reader.uint(optional, 2)
        directory_offset = {0x10B: 96, 0x20B: 112}.get(magic)
        if directory_offset is None or optional_size < directory_offset + 15 * 8:
            raise BinaryFormatError(f"{reader.label}: CLR directory missing")
        section_start = optional + optional_size
        reader.require(section_start, sections * 40)

        def file_offset(rva: int, size: int = 1) -> int:
            for index in range(sections):
                section = section_start + index * 40
                virtual_size, virtual_address, raw_size, raw_offset = reader.unpack("<4I", section + 8)
                if virtual_address <= rva < virtual_address + max(virtual_size, raw_size):
                    relative = rva - virtual_address
                    if relative > raw_size - size:
                        raise BinaryFormatError(f"{reader.label}: RVA exceeds section data")
                    reader.require(raw_offset + relative, size)
                    return raw_offset + relative
            raise BinaryFormatError(f"{reader.label}: unmapped RVA {rva:#x}")

        clr = file_offset(reader.uint(optional + directory_offset + 14 * 8), 16)
        metadata_rva, metadata_size = reader.unpack("<II", clr + 8)
        root = file_offset(metadata_rva, metadata_size)
        if metadata_size < 20:
            raise BinaryFormatError(f"{reader.label}: metadata header truncated")
        if reader.take(root, 4) != b"BSJB":
            raise BinaryFormatError(f"{reader.label}: CLR metadata signature missing")
        version_size = reader.uint(root + 12)
        if version_size > metadata_size - 16:
            raise BinaryFormatError(f"{reader.label}: version string exceeds metadata")
        position = (root + 16 + version_size + 3) & ~3
        if position + 4 > root + metadata_size:
            raise BinaryFormatError(f"{reader.label}: stream header truncated")
        count = reader.uint(position + 2, 2)
        position += 4
        streams = {}
        for _ in range(count):
            if position + 8 > root + metadata_size:
                raise BinaryFormatError(f"{reader.label}: stream directory truncated")
            offset, size = reader.unpack("<II", position)
            if offset > metadata_size - size:
                raise BinaryFormatError(f"{reader.label}: stream exceeds metadata")
            name = reader.cstring(position + 8, min(position + 40, root + metadata_size))
            streams[name] = (root + offset, size)
            position = (position + 8 + len(name) + 4) & ~3
        if not {"#Strings", "#Blob"}.issubset(streams) or not ({"#~", "#-"} & streams.keys()):
            raise BinaryFormatError(f"{reader.label}: required metadata stream missing")
        return streams

    def _width(self, column: str | int) -> int:
        if isinstance(column, int):
            return 4 if self.rows.get(column, 0) >= 65536 else 2
        if column in {"u2", "u4"}:
            return int(column[1:])
        if column in {"str", "guid", "blob"}:
            flag = {"str": 1, "guid": 2, "blob": 4}[column]
            return 4 if self.heap_flags & flag else 2
        bits, tables = CODED[column]
        return 4 if max(self.rows.get(table, 0) for table in tables) >= 1 << (16 - bits) else 2

    def _read_tables(self) -> None:
        start, size = self.streams.get("#~", self.streams.get("#-", (0, 0)))
        self.heap_flags = self.reader.uint(start + 6, 1)
        valid = self.reader.uint(start + 8, 8)
        position = start + 24
        for table in range(64):
            if valid & (1 << table):
                if table not in TABLES:
                    raise BinaryFormatError(f"{self.reader.label}: unsupported metadata table {table}")
                self.rows[table] = self.reader.uint(position)
                position += 4
        for table, count in self.rows.items():
            widths = [self._width(column) for column in TABLES[table]]
            row_size = sum(widths)
            self.positions[table] = position
            self.widths[table] = widths
            self.row_sizes[table] = row_size
            position += row_size * count
            if position > start + size:
                raise BinaryFormatError(f"{self.reader.label}: table {table} exceeds stream")

    def row(self, table: int, index: int) -> tuple[int, ...]:
        if index < 1 or index > self.rows.get(table, 0):
            raise BinaryFormatError(f"{self.reader.label}: invalid table {table} index {index}")
        position = self.positions[table] + (index - 1) * self.row_sizes[table]
        result = []
        for width in self.widths[table]:
            result.append(self.reader.uint(position, width))
            position += width
        return tuple(result)

    @lru_cache(maxsize=None)
    def string(self, index: int) -> str:
        start, size = self.streams["#Strings"]
        if index >= size:
            raise BinaryFormatError(f"{self.reader.label}: string index exceeds heap")
        return self.reader.cstring(start + index, start + size)

    def blob(self, index: int) -> bytes:
        start, size = self.streams["#Blob"]
        if index >= size:
            raise BinaryFormatError(f"{self.reader.label}: blob index exceeds heap")
        count, position = compressed_uint(self.reader, start + index)
        if position + count > start + size:
            raise BinaryFormatError(f"{self.reader.label}: blob exceeds heap")
        return self.reader.take(position, count)

    def type_name(self, index: int, depth: int = 0) -> str:
        if depth > 32:
            raise BinaryFormatError(f"{self.reader.label}: nested type cycle")
        row = self.row(2, index)
        name, namespace = self.string(row[1]), self.string(row[2])
        if index in self.nested_types:
            return self.type_name(self.nested_types[index], depth + 1) + "+" + name
        return f"{namespace}.{name}" if namespace else name

    def type_reference(self, coded: int, depth: int = 0) -> str | None:
        if coded == 0:
            return None
        if depth > 32:
            raise BinaryFormatError(f"{self.reader.label}: recursive type signature")
        tag, index = coded & 3, coded >> 2
        if tag == 0:
            return self.type_name(index)
        elif tag == 1:
            row = self.row(1, index)
            name, namespace = self.string(row[1]), self.string(row[2])
            if row[0] & 3 == 3:
                return str(self.type_reference(((row[0] >> 2) << 2) | 1, depth + 1)) + "+" + name
        elif tag == 2:
            return self.signature_type(BinaryReader(self.blob(self.row(27, index)[0]), "type signature"), 0, depth + 1)[0]
        else:
            raise BinaryFormatError(f"{self.reader.label}: invalid type reference tag")
        return f"{namespace}.{name}" if namespace else name

    def signature_type(self, reader: BinaryReader, offset: int, depth: int = 0) -> tuple[str, int]:
        if depth > 32:
            raise BinaryFormatError("type signature: nesting limit exceeded")
        element = reader.uint(offset, 1)
        offset += 1
        if element in PRIMITIVES:
            return PRIMITIVES[element], offset
        if element in {0x11, 0x12}:
            token, offset = compressed_uint(reader, offset)
            return self.type_reference(token, depth + 1) or "<null>", offset
        if element in {0x13, 0x1E}:
            index, offset = compressed_uint(reader, offset)
            return ("!" if element == 0x13 else "!!") + str(index), offset
        if element in {0x0F, 0x10, 0x1D, 0x45}:
            inner, offset = self.signature_type(reader, offset, depth + 1)
            return inner + {0x0F: "*", 0x10: "&", 0x1D: "[]", 0x45: " pinned"}[element], offset
        if element in {0x1F, 0x20}:
            token, offset = compressed_uint(reader, offset)
            inner, offset = self.signature_type(reader, offset, depth + 1)
            return inner + f" mod{'req' if element == 0x1F else 'opt'}({self.type_reference(token, depth + 1)})", offset
        if element == 0x15:
            generic, offset = self.signature_type(reader, offset, depth + 1)
            count, offset = compressed_uint(reader, offset)
            parameters = []
            for _ in range(count):
                value, offset = self.signature_type(reader, offset, depth + 1)
                parameters.append(value)
            return generic + "<" + ", ".join(parameters) + ">", offset
        if element == 0x14:
            inner, offset = self.signature_type(reader, offset, depth + 1)
            rank, offset = compressed_uint(reader, offset)
            if rank > 32:
                raise BinaryFormatError("type signature: invalid array rank")
            for _ in range(2):
                count, offset = compressed_uint(reader, offset)
                if count > rank:
                    raise BinaryFormatError("type signature: invalid array dimensions")
                for _ in range(count):
                    _, offset = compressed_uint(reader, offset)
            return inner + "[" + "," * max(0, rank - 1) + "]", offset
        if element == 0x1B:
            signature, offset = self.method_signature(reader, offset, depth + 1)
            return "fnptr(" + ", ".join(signature["parameter_types"]) + ")->" + signature["return_type"], offset
        raise BinaryFormatError(f"type signature: unsupported element {element:#x}")

    def method_signature(self, reader: BinaryReader, offset: int = 0, depth: int = 0) -> tuple[dict, int]:
        convention = reader.uint(offset, 1)
        offset += 1
        generic_count = 0
        if convention & 0x10:
            generic_count, offset = compressed_uint(reader, offset)
        count, offset = compressed_uint(reader, offset)
        return_type, offset = self.signature_type(reader, offset, depth + 1)
        parameters = []
        for _ in range(count):
            if reader.uint(offset, 1) == 0x41:
                offset += 1
            value, offset = self.signature_type(reader, offset, depth + 1)
            parameters.append(value)
        return {
            "return_type": return_type,
            "parameter_types": parameters,
            "generic_parameter_count": generic_count,
        }, offset

    def types(self, namespaces: tuple[str, ...]) -> list[dict]:
        result = []
        for index in range(1, self.rows.get(2, 0) + 1):
            _, name_index, namespace_index, parent, field_start, method_start = self.row(2, index)
            name, namespace = self.string(name_index), self.string(namespace_index)
            full_name = self.type_name(index)
            if not any(full_name.startswith(selected + ".") for selected in namespaces):
                continue
            if name.startswith(("<", "_")) or "Formatter" in name:
                continue
            if index < self.rows[2]:
                next_row = self.row(2, index + 1)
                field_end, method_end = next_row[4], next_row[5]
            else:
                field_end, method_end = self.rows.get(4, 0) + 1, self.rows.get(6, 0) + 1
            if not 1 <= field_start <= field_end <= self.rows.get(4, 0) + 1 or not 1 <= method_start <= method_end <= self.rows.get(6, 0) + 1:
                raise BinaryFormatError(f"{self.reader.label}: invalid member range")
            methods, getters, setters = [], {}, set()
            for method_index in range(method_start, method_end):
                _, _, flags, method_name_index, signature_index, param_start = self.row(6, method_index)
                method_name = self.string(method_name_index)
                signature, _ = self.method_signature(BinaryReader(self.blob(signature_index), "method signature"))
                if method_name.startswith("get_"):
                    getters[method_name[4:]] = signature["return_type"]
                if method_name.startswith("set_"):
                    setters.add(method_name[4:])
                if method_name.startswith(("get_", "set_")) or method_name in {".ctor", ".cctor"}:
                    continue
                param_end = self.row(6, method_index + 1)[5] if method_index < self.rows[6] else self.rows.get(8, 0) + 1
                if not 1 <= param_start <= param_end <= self.rows.get(8, 0) + 1:
                    raise BinaryFormatError(f"{self.reader.label}: invalid parameter range")
                parameter_names = {}
                for parameter_index in range(param_start, param_end):
                    _, sequence, parameter_name_index = self.row(8, parameter_index)
                    parameter_names[sequence] = self.string(parameter_name_index)
                signature["parameters"] = [
                    {"name": parameter_names.get(number, f"arg{number}"), "type": type_name}
                    for number, type_name in enumerate(signature.pop("parameter_types"), 1)
                ]
                methods.append({"name": method_name, "static": bool(flags & 0x10), **signature})
            fields = []
            for field_index in range(field_start, field_end):
                flags, field_name_index, signature_index = self.row(4, field_index)
                field_name = self.string(field_name_index)
                if field_name.startswith("NativeMethodInfoPtr_"):
                    continue
                if field_name.startswith("NativeFieldInfoPtr_"):
                    field_name = field_name.removeprefix("NativeFieldInfoPtr_")
                    fields.append({"name": field_name, "type": getters.get(field_name), "evidence": "interop_field_getter"})
                elif field_name != "NativeClassPtr":
                    field_reader = BinaryReader(self.blob(signature_index), "field signature")
                    if field_reader.uint(0, 1) != 6:
                        raise BinaryFormatError("field signature: expected FIELD prefix")
                    type_name, _ = self.signature_type(field_reader, 1)
                    fields.append({"name": field_name, "type": type_name, "static": bool(flags & 0x10), "evidence": "managed_field"})
            result.append({
                "namespace": namespace, "name": name, "full_name": full_name,
                "base_type": self.type_reference(parent), "fields": fields, "methods": methods,
                "properties": [
                    {"name": property_name, "type": property_type, "writable": property_name in setters}
                    for property_name, property_type in getters.items()
                ],
            })
        return result
