from __future__ import annotations

import hashlib
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

IMAGE_BASE = 0x180000000
PRIMITIVE_TYPES = {"System.Void": "void", "System.Boolean": "bool", "System.Byte": "unsigned char", "System.SByte": "signed char",
                   "System.Int16": "short", "System.UInt16": "unsigned short", "System.Int32": "int", "System.UInt32": "unsigned int",
                   "System.Int64": "__int64", "System.UInt64": "unsigned __int64", "System.Single": "float", "System.Double": "double",
                   "System.Char": "unsigned short", "System.IntPtr": "void *", "System.UIntPtr": "void *", "System.String": "void *", "System.Object": "void *"}


def native_type(name, shapes):
    if name in PRIMITIVE_TYPES:
        return PRIMITIVE_TYPES[name]
    if name.endswith(("&", "[]", "*")):
        return "void *"
    info = shapes.get(name)
    if info is None and "<" in name and name.endswith(">") and "/" not in name[name.index("<"):]:
        info = shapes.get(name.split("<", 1)[0])
    if info and info["kind"] == "enum":
        return native_type(info["underlying"], shapes)
    if info and info["kind"] == "reference":
        return "void *"
    raise ValueError("Unverified by-value or generic native ABI: " + name)


def native_declaration(method, shapes):
    result = native_type(method["return_type"], shapes)
    arguments = [] if method["static"] else ["void *"]
    arguments.extend(native_type(p["type"], shapes) for p in method["parameters"])
    arguments.append("const void *")
    return result + " __fastcall native_function(" + ", ".join(f"{kind} a{i + 1}" for i, kind in enumerate(arguments)) + ");"


def apply_verified_prototypes(db, root, targets):
    root = Path(root)
    validate_inputs(root)
    binary = (root / "GameAssembly.dll").read_bytes()
    index = read_json(root / "native-method-index.json")
    grouped = defaultdict(list)
    for method in index["methods"]:
        grouped[method["address"]["RVA"]].append(method)
    results = []
    for rva in sorted({m["address"]["RVA"] for m in targets}, key=lambda v: int(v, 16)):
        methods = grouped[rva]
        row = {"rva": rva, "signatures": [m["signature"] for m in methods]}
        try:
            declarations = {native_declaration(m, index["type_shapes"]) for m in methods}
            if len(declarations) != 1:
                raise ValueError("Shared native address has incompatible declarations")
            declaration = declarations.pop()
            method = methods[0]
            offset, length = int(method["address"]["Offset"], 16), int(method["address"]["Length"], 16)
            ea = IMAGE_BASE + int(rva, 16)
            if db.bytes.get_bytes_at(ea, length) != binary[offset:offset + length]:
                raise ValueError("Native bytes differ from pinned input")
            func = db.functions.get_at(ea)
            if func is None or func.start_ea != ea:
                raise ValueError("No exact function start")
            if not db.functions.apply_declaration(func, declaration):
                raise ValueError("IDA rejected declaration")
            row["declaration"] = declaration
        except Exception as error:
            row["skipped_reason"] = str(error)
        results.append(row)
    write_json(root / "applied-native-prototypes.json", results)
    return {"applied": sum("declaration" in r for r in results),
            "skipped": [{"rva": r["rva"], "alias_count": len(r["signatures"]), "reason": r["skipped_reason"]} for r in results if "skipped_reason" in r]}


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_inputs(root):
    for source in read_json(root / "inputs.json"):
        path = Path(source["copy"])
        if hashlib.sha256(path.read_bytes()).hexdigest() != source["sha256"]:
            raise ValueError("Analysis input fingerprint mismatch: " + path.name)


def literal_index(encoded):
    if type(encoded) is not int or encoded & 1 != 1 or encoded >> 29 != 5:
        raise ValueError("Getter does not reference an encoded string literal")
    return (encoded >> 1) & 0xFFFFFFF


def constant_result(pseudocode):
    matches = re.findall(r"\breturn\s+(0x[0-9a-fA-F]+|[0-9]+)(?:u|U|LL|LLu|i64)?\s*;", pseudocode)
    if len(matches) != 1 or len(re.findall(r"\breturn\b", pseudocode)) != 1:
        raise ValueError("HTTP method getter is not a verified constant return")
    return int(matches[0], 0)


def returned_literal(pseudocode):
    matches = re.findall(r"\breturn\s+qword_([0-9a-fA-F]+)\s*;", pseudocode)
    if len(matches) != 1 or len(re.findall(r"\breturn\b", pseudocode)) != 1:
        raise ValueError("Route getter needs dynamic path analysis")
    return int(matches[0], 16)


def format_field_arguments(pseudocode, count):
    values = {}
    for line in pseudocode.splitlines():
        load = re.search(r"(v\d+) = \*\(_(?:QWORD|DWORD) \*\)\(a1 \+ (\d+)\);", line)
        box = re.search(r"(v\d+) = j_il2cpp_value_box\(a1: [^,]+, a2: &(v\d+)\);", line)
        alias = re.fullmatch(r"\s*(v\d+) = (v\d+);", line)
        if load:
            values[load[1]] = int(load[2])
        elif box and box[2] in values:
            values[box[1]] = values[box[2]]
        elif alias and alias[2] in values:
            values[alias[1]] = values[alias[2]]
        if line.strip().startswith("return sub_"):
            args = dict(re.findall(r"a(\d+): (v\d+)", line))
            return [values[args[str(i + 2)]] for i in range(count)]
    raise ValueError("Formatting argument dataflow is not recognized")


def route_binding(db, request, evidence, types, literals, cache, binary, formatters, reviewed):
    code = evidence["get_ApiName"]["pseudocode"]

    def decode(ea):
        encoded = db.bytes.get_qword_at(ea)
        index = literal_index(encoded)
        return literals[index], {"literal_address": hex(ea), "encoded_literal": hex(encoded), "literal_index": index}

    try:
        ea = returned_literal(code)
    except ValueError:
        ea = None
    if ea is not None:
        route, proof = decode(ea)
        return {"route": route, "route_kind": "constant", **proof}
    call = re.search(r"return sub_([0-9A-Fa-f]+)\(a1: qword_([0-9A-Fa-f]+),", code)
    if call and int(call[1], 16) in formatters:
        helper = formatters[int(call[1], 16)]
        route, proof = decode(int(call[2], 16))
        offsets = format_field_arguments(code, helper["argument_count"])
        fields = {int(f["offset"], 16): f for f in types[request["type"]]["fields"] if f["offset"] is not None}
        parameters = [{"placeholder": "{" + str(i) + "}", "field": fields[offset]["name"], "type": fields[offset]["type"]}
                      for i, offset in enumerate(offsets)]
        if set(re.findall(r"\{\d+\}", route)) != {p["placeholder"] for p in parameters}:
            raise ValueError("Format placeholders do not match native arguments")
        return {"route": route, "route_kind": "format", "path_parameters": parameters,
                "formatter": helper["signature"], **proof}
    field = re.search(r"return \*\(_QWORD \*\)\(a1 \+ (\d+)\);", code)
    if field:
        offset = int(field[1])
        constructor = next((m for m in types[request["type"]]["methods"] if m["name"] == ".ctor" and not m["parameters"]), None)
        if constructor is not None:
            address = constructor["address"]
            key = address["RVA"]
            if key not in cache:
                cache[key] = function_evidence(db, address, binary)
            assigned = re.findall(r"\*\(_QWORD \*\)\(a1 \+ " + str(offset) + r"\) = qword_([0-9a-fA-F]+);", cache[key]["pseudocode"])
            if len(assigned) == 1:
                route, proof = decode(int(assigned[0], 16))
                return {"route": route, "route_kind": "constructor_constant", "constructor": cache[key]["address"], **proof}
        raise ValueError("Route is supplied through an instance field at offset " + hex(offset))
    if request["type"] in reviewed:
        binding = reviewed[request["type"]]
        getter = next(g for g in request["getters"] if g["method"] == "get_ApiName")["address"]
        ea = IMAGE_BASE + int(getter["RVA"], 16)
        if hex(ea) != binding["getter_address"] or hashlib.sha256(db.bytes.get_bytes_at(ea, int(getter["Length"], 16))).hexdigest() != binding["getter_sha256"]:
            raise ValueError("Reviewed route getter changed")
        for literal in binding["literal_evidence"]:
            if decode(int(literal["address"], 16))[0] != literal["value"]:
                raise ValueError("Reviewed route literal changed")
        fields = {f["name"] for f in types[request["type"]]["fields"]}
        if any(p["field"] not in fields for p in binding.get("query_parameters", [])):
            raise ValueError("Reviewed query field is missing")
        return dict(binding)
    raise ValueError("Route getter needs static-field or query-expression analysis")


def collect_fields(name, types):
    fields, seen = [], set()
    while name in types and name not in seen:
        seen.add(name)
        current = types[name]
        fields.extend({"name": f["name"], "type": f["type"], "declaring_type": name}
                      for f in current["fields"] if f["public"] and not f["static"])
        name = current["parent"]
    return fields


def function_evidence(db, address, binary):
    rva, offset, length = (int(address[field], 16) for field in ("RVA", "Offset", "Length"))
    ea = IMAGE_BASE + rva
    if length < 1 or offset + length > len(binary):
        raise ValueError("Native method bounds are invalid")
    if db.bytes.get_bytes_at(ea, length) != binary[offset:offset + length]:
        raise ValueError("IDA method bytes differ from the pinned input")
    func = db.functions.get_at(ea)
    if func is None or func.start_ea != ea:
        raise ValueError("Native address is not an exact IDA function start")
    return {"address": hex(ea), "size": length,
            "disassembly": db.functions.get_disassembly(func),
            "pseudocode": str(db.functions.get_pseudocode(func))}


def export_api(db, root):
    root = Path(root)
    validate_inputs(root)
    sys.path.insert(0, str(root.parents[2]))
    from tools.il2cpp_metadata import read_literals

    literals = read_literals((root / "global-metadata.dat").read_bytes())
    binary = (root / "GameAssembly.dll").read_bytes()
    reviewed = read_json(root / "reviewed-route-bindings.json")
    if reviewed["source_sha256"] != hashlib.sha256(binary).hexdigest():
        raise ValueError("Reviewed routes require a matching client fingerprint")
    getters = read_json(root / "request-getter-index.json")
    types = {t["type"]: t for t in read_json(root / "api-type-index.json")["types"]}
    methods = {entry["value"]: entry["name"] for entry in getters["api_method_enum"]}
    formatters = {}
    for helper in read_json(root / "system-string-methods.json"):
        for count in (1, 2):
            signature = "System.String System.String::Format(System.String" + ",System.Object" * count + ")"
            if helper["signature"] == signature:
                function_evidence(db, helper["address"], binary)
                formatters[IMAGE_BASE + int(helper["address"]["RVA"], 16)] = {**helper, "argument_count": count}
    cache, results = {}, []
    for request in sorted(getters["request_types"], key=lambda r: r["type"]):
        row = {"request_type": request["type"], "request_fields": collect_fields(request["type"], types), "verified": False}
        evidence = {}
        try:
            for getter in request["getters"]:
                address = getter["address"]
                key = address["RVA"]
                if key not in cache:
                    cache[key] = function_evidence(db, address, binary)
                evidence[getter["method"]] = cache[key]
            row["method"] = methods[constant_result(evidence["get_Method"]["pseudocode"])]
            binding = route_binding(db, request, evidence, types, literals, cache, binary, formatters, reviewed["bindings"])
            if not re.fullmatch(r"[A-Za-z0-9_/{}/.?=&%\-]+", binding["route"]):
                raise ValueError("Decoded literal is not a supported route template")
            row.update(**binding, verified=True)
        except Exception as error:
            row["unresolved_reason"] = str(error)
        row["getter_evidence"] = {name: value["address"] for name, value in evidence.items()}
        candidate = request["type"].removesuffix("RequestEntity") + "ResponseEntity"
        if candidate in types:
            row["response_candidate"] = {"type": candidate, "fields": collect_fields(candidate, types),
                                         "binding": "name convention; verify the call site before treating as authoritative"}
        results.append(row)
    report = {"client": getters["client"], "image_base": hex(IMAGE_BASE), "string_usage_decoder": "sub_180819270 case 5",
              "request_types": len(results), "verified": sum(r["verified"] for r in results), "requests": results,
              "functions": cache, "scope": "Client route getters and declaration shapes; server behavior and wire formatter keys are separate evidence."}
    write_json(root / "api-contracts.json", report)
    return {"request_types": len(results), "verified": report["verified"], "unique_routes": len({r["route"] for r in results if r["verified"]}),
            "unresolved": [{"type": r["request_type"], "reason": r["unresolved_reason"]} for r in results if not r["verified"]]}


def export_functions(db, root, targets, label, resume=False):
    root = Path(root)
    validate_inputs(root)
    binary = (root / "GameAssembly.dll").read_bytes()
    method_index = read_json(root / "native-method-index.json")["methods"]
    aliases = defaultdict(list)
    for method in method_index:
        aliases[IMAGE_BASE + int(method["address"]["RVA"], 16)].append(method["signature"])
    output = root / label
    output.mkdir(exist_ok=True)
    results = read_json(output / "index.json") if resume and (output / "index.json").exists() else []
    seen = {int(r["rva"], 16) for r in results}
    processed = 0
    for method in targets:
        address = method["address"]
        if not address.get("RVA") or int(address["RVA"], 16) in seen:
            continue
        seen.add(int(address["RVA"], 16))
        row = {"type": method["type"], "method": method["name"], "rva": address["RVA"], "signature": method.get("signature")}
        try:
            proof = function_evidence(db, address, binary)
            ea = int(proof["address"], 16)
            func = db.functions.get_at(ea)
            row.update(address=proof["address"], aliases=aliases[ea], size=proof["size"],
                       callers=[{"address": hex(f.start_ea), "names": aliases.get(f.start_ea, [])} for f in db.functions.get_callers(func)],
                       callees=[{"address": hex(f.start_ea), "names": aliases.get(f.start_ea, [])} for f in db.functions.get_callees(func)])
            filename = address["RVA"] + ".c"
            (output / filename).write_text(proof["pseudocode"], encoding="utf-8")
            row.update(pseudocode=filename, lines=len(proof["pseudocode"].splitlines()))
            write_json(output / (address["RVA"] + ".json"), {**row, "disassembly": proof["disassembly"]})
        except Exception as error:
            row["error"] = str(error)
        results.append(row)
        processed += 1
        if processed % 10 == 0:
            write_json(output / "index.json", results)
    write_json(output / "index.json", results)
    return {"targets": len(results), "processed_in_call": processed, "decompiled": sum("error" not in r for r in results),
            "errors": [r for r in results if "error" in r]}
