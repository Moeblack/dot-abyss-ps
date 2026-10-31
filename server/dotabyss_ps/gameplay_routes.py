from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Callable

from .gameplay_catalog import GameplayError, number


@dataclass(frozen=True)
class Route:
    method: str
    fields: frozenset[str]
    handler: Callable
    path_fields: tuple[str, ...] = ()

    def arguments(self, body: dict, path_values: list[str]) -> dict:
        if set(body) != self.fields - set(self.path_fields):
            raise GameplayError("Request fields do not match the endpoint", 400, 4007)
        result = dict(body)
        if len(path_values) != len(self.path_fields):
            raise GameplayError("Invalid endpoint parameters", 400, 4007)
        for name, value in zip(self.path_fields, path_values):
            if not re.fullmatch(r"[0-9]{1,19}", value):
                raise GameplayError("Invalid numeric path argument", 400, 4007)
            result[name] = number(int(value), 0)
        return result


def route(method, fields, handler, *path_fields):
    return Route(method, frozenset(fields.split()), handler, path_fields)


def match_route(path, routes):
    if path in routes:
        return path, routes[path], []
    parts = path.split("/")
    for template, entry in routes.items():
        if not entry.path_fields:
            continue
        expected = template.split("/")
        if len(parts) != len(expected):
            continue
        values = []
        for actual, declared in zip(parts, expected):
            if re.fullmatch(r"\{\d+\}", declared):
                values.append(actual)
            elif actual != declared:
                break
        else:
            return template, entry, values
    return None
