#!/usr/bin/env python3
"""Turn an OpenAPI 3.0/3.1 JSON document into one compact CONTRACTS.md per module.

A module is the first path segment after the base prefix: with the prefix `/api/v1`,
`/api/v1/properties/{id}/` belongs to `properties`. Stdlib only; run with `--help` for the options.

    python3 openapi_to_contracts.py openapi.json --out .claude/skills/_draft
    python manage.py spectacular --format openapi-json | python3 openapi_to_contracts.py - --module properties
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote

EXIT_INPUT_ERROR = 2
PROG = "openapi_to_contracts"
CONTRACTS_FILENAME = "CONTRACTS.md"
ROOT_MODULE = "root"
STDIN_SOURCE = "-"
STDIN_PLACEHOLDER = "<OpenAPI JSON>"
JSON_CONTENT_TYPE = "application/json"
SCHEMA_REF_PREFIX = "#/components/schemas/"
LOCAL_REF_PREFIX = "#/"
SCRIPT_COMMAND = 'python3 "${CLAUDE_PLUGIN_ROOT}/scripts/openapi_to_contracts.py"'
METHOD_ORDER = ("get", "post", "put", "patch", "delete", "head", "options", "trace")
COMPOSITIONS = ("allOf", "oneOf", "anyOf")
PARAM_LOCATIONS = (("path", "Path"), ("query", "Query"))
MAX_ENUM_VALUES = 20
MAX_INLINE_DEPTH = 3
UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9_-]+")

HELP_EPILOG = """\
Prefix: by default the prefix is the run of leading path segments that every operation shares,
shortened until every path still has a literal (non-{param}) segment after it, so
/api/v1/properties/ and /api/v1/deals/{id}/ group as `properties` and `deals`. Pass
--strip-prefix / to strip nothing. A path outside an explicit prefix keeps its full path.
A path with no segment after the prefix goes to the `root` module.

Exit codes: 0 on success, 2 when the input isn't readable OpenAPI 3 JSON or --module matches nothing.
"""


class SpecError(Exception):
    """The input can't be turned into contracts; the message tells the user what to do."""


@dataclass(frozen=True)
class Operation:
    method: str
    display_path: str
    module: str
    spec: dict
    path_parameters: tuple


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description="Write compact per-module CONTRACTS.md files from an OpenAPI 3.0/3.1 JSON document.",
        epilog=HELP_EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("spec", help="OpenAPI JSON file, or - to read it from stdin")
    parser.add_argument(
        "--strip-prefix",
        metavar="PREFIX",
        help="path prefix to strip before grouping, e.g. /api/v1 (default: auto-detected, see below)",
    )
    parser.add_argument(
        "--out",
        metavar="DIR",
        type=Path,
        help=f"write DIR/<module>/{CONTRACTS_FILENAME} per module and print the written paths "
        "(default: one document on stdout)",
    )
    parser.add_argument(
        "--module",
        metavar="NAME",
        action="append",
        default=[],
        help="only this module; repeat for several, which stdout joins into one document (default: every module)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        renderer = Renderer(load_spec(args.spec), args.strip_prefix)
        selected = renderer.select(args.module)
    except SpecError as error:
        print(f"{PROG}: {error}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    if args.out is None:
        command = regenerate_command(args.spec, renderer.prefix, sorted(set(args.module)))
        sys.stdout.write(renderer.document(selected, command))
        return 0
    for module in selected:
        target = args.out / module / CONTRACTS_FILENAME
        target.parent.mkdir(parents=True, exist_ok=True)
        command = regenerate_command(args.spec, renderer.prefix, [module])
        target.write_text(renderer.document([module], command), encoding="utf-8")
        print(target)
    return 0


def load_spec(source: str) -> dict:
    label = "stdin" if source == STDIN_SOURCE else source
    try:
        text = sys.stdin.read() if source == STDIN_SOURCE else Path(source).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise SpecError(
            f"can't read {label}: {error}. Pass the path of an OpenAPI JSON file, or - for stdin."
        ) from error
    try:
        spec = json.loads(text)
    except json.JSONDecodeError as error:
        raise SpecError(
            f"{label} isn't valid JSON ({error.msg}, line {error.lineno}). "
            "Pass OpenAPI as JSON; convert YAML first, e.g. with `yq -o=json`."
        ) from error
    return validate_spec(spec, label)


def validate_spec(spec: object, label: str) -> dict:
    if not isinstance(spec, dict):
        raise SpecError(f"{label} isn't an OpenAPI document: the top level must be a JSON object.")
    if "swagger" in spec:
        raise SpecError(f"{label} is Swagger 2.0; convert it to OpenAPI 3 first.")
    version = spec.get("openapi")
    if not isinstance(version, str) or not version.startswith("3."):
        raise SpecError(f"{label} isn't an OpenAPI 3.x document: it needs an `openapi` field such as 3.0.3.")
    if not isinstance(spec.get("paths"), dict):
        raise SpecError(f"{label} has no `paths` object, so there are no operations to describe.")
    return spec


def regenerate_command(source: str, prefix: list[str], modules: list[str]) -> str:
    module_args = "".join(f" --module {module}" for module in modules)
    prefix_arg = "/" + "/".join(prefix)
    piped = f"{STDIN_PLACEHOLDER} | " if source == STDIN_SOURCE else ""
    script_args = f"{shlex.quote(source)} --strip-prefix {prefix_arg}{module_args}"
    return f"{piped}{SCRIPT_COMMAND} {script_args} > {CONTRACTS_FILENAME}"


def split_path(path: str) -> list[str]:
    return [segment for segment in path.split("/") if segment]


def is_param(segment: str) -> bool:
    return segment.startswith("{") and segment.endswith("}")


def auto_prefix(paths: list[str]) -> list[str]:
    split = [split_path(path) for path in paths]
    common: list[str] = []
    for segments in zip(*split, strict=False):
        first = segments[0]
        if is_param(first) or any(segment != first for segment in segments):
            break
        common.append(first)
    while common and any(len(segments) <= len(common) or is_param(segments[len(common)]) for segments in split):
        common.pop()
    return common


def module_name(segment: str) -> str:
    return UNSAFE_NAME_CHARS.sub("-", segment).strip("-") or ROOT_MODULE


def collect_operations(paths: dict[str, dict], prefix: list[str]) -> Iterator[Operation]:
    for path, item in paths.items():
        segments = split_path(path)
        rest = segments[len(prefix) :] if segments[: len(prefix)] == prefix else segments
        module = module_name(rest[0]) if rest else ROOT_MODULE
        trailing = "/" if path.endswith("/") and rest else ""
        display_path = "/" + "/".join(rest) + trailing
        path_parameters = tuple(as_list(item.get("parameters")))
        for method in METHOD_ORDER:
            operation = item.get(method)
            if isinstance(operation, dict):
                yield Operation(method, display_path, module, operation, path_parameters)


def as_dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def as_list(value: object) -> list:
    return value if isinstance(value, list) else []


def ref_name(ref: str) -> str:
    return unquote(ref.rsplit("/", 1)[-1])


class Resolver:
    """Follows local `$ref` pointers; an external or broken one resolves to nothing."""

    def __init__(self, spec: dict) -> None:
        self.spec = spec

    def lookup(self, ref: str) -> dict | None:
        if not ref.startswith(LOCAL_REF_PREFIX):
            return None
        node: object = self.spec
        for token in ref[len(LOCAL_REF_PREFIX) :].split("/"):
            key = unquote(token).replace("~1", "/").replace("~0", "~")
            if not isinstance(node, dict) or key not in node:
                return None
            node = node[key]
        return node if isinstance(node, dict) else None

    def deref(self, node: object) -> dict:
        """The object behind a chain of `$ref`s, or `{}` when the chain is broken or loops."""
        seen: set[str] = set()
        current = as_dict(node)
        while isinstance(current.get("$ref"), str):
            ref = current["$ref"]
            if ref in seen:
                return {}
            seen.add(ref)
            current = as_dict(self.lookup(ref))
        return current


class Renderer:
    """The spec's operations, grouped into modules under one base prefix, as CONTRACTS.md documents."""

    def __init__(self, spec: dict, strip_prefix: str | None) -> None:
        self.spec = spec
        self.resolver = Resolver(spec)
        self.schemes = as_dict(as_dict(spec.get("components")).get("securitySchemes"))
        paths = {path: self.resolver.deref(item) for path, item in spec["paths"].items() if isinstance(path, str)}
        self.prefix = auto_prefix(list(paths)) if strip_prefix is None else split_path(strip_prefix)
        self.operations = list(collect_operations(paths, self.prefix))
        self.modules = sorted({operation.module for operation in self.operations})

    def select(self, wanted: list[str]) -> list[str]:
        if not self.modules:
            raise SpecError("the spec declares no operations, so there are no contracts to write.")
        missing = sorted(set(wanted) - set(self.modules))
        if missing:
            available = ", ".join(self.modules)
            raise SpecError(f"no operations for module(s) {', '.join(missing)}. Modules in this spec: {available}.")
        return [module for module in self.modules if not wanted or module in wanted]

    def document(self, modules: list[str], command: str) -> str:
        operations = [operation for operation in self.operations if operation.module in modules]
        info = as_dict(self.spec.get("info"))
        api = " ".join(str(info[key]) for key in ("title", "version") if isinstance(info.get(key), (str, int, float)))
        lines = [
            f"<!-- GENERATED by openapi_to_contracts.py; do not edit. Regenerate: {command} -->",
            f"# Contracts: {', '.join(modules)}",
            "",
            f"Base path: `/{'/'.join(self.prefix)}`." + (f" API: {api}." if api else ""),
            "",
            "## Operations",
        ]
        for operation in sorted(operations, key=lambda op: (op.display_path, METHOD_ORDER.index(op.method))):
            lines += ["", *self.operation_lines(operation)]
        schema_refs = self.referenced_schemas(operations)
        if schema_refs:
            lines += ["", "## Schemas"]
            for name, ref in schema_refs:
                lines += ["", *self.schema_section(name, ref)]
        return "\n".join(lines) + "\n"

    def operation_lines(self, operation: Operation) -> list[str]:
        spec = operation.spec
        deprecated = " (deprecated)" if spec.get("deprecated") is True else ""
        lines = [f"### {operation.method.upper()} {operation.display_path}{deprecated}"]
        if isinstance(spec.get("operationId"), str):
            lines.append(f"- operationId: {spec['operationId']}")
        lines.append(f"- Auth: {self.auth(spec)}")
        lines += self.parameter_lines(operation)
        lines += self.body_lines(spec.get("requestBody"))
        lines += self.response_lines(as_dict(spec.get("responses")))
        return lines

    def auth(self, operation: dict) -> str:
        requirements = operation["security"] if "security" in operation else self.spec.get("security")
        options = [self.requirement_label(as_dict(requirement)) for requirement in as_list(requirements)]
        return " or ".join(options) or "none"

    def requirement_label(self, requirement: dict) -> str:
        if not requirement:
            return "none"
        return " + ".join(self.scheme_label(name, as_list(requirement[name])) for name in sorted(requirement))

    def scheme_label(self, name: str, scopes: list) -> str:
        scheme = self.resolver.deref(self.schemes.get(name))
        kind = scheme.get("type")
        if kind == "http":
            detail = f"http {scheme.get('scheme', '')}".strip()
        elif kind == "apiKey":
            detail = f"apiKey {scheme.get('in', '')} {scheme.get('name', '')}".strip()
        else:
            detail = str(kind) if kind else "undeclared scheme"
        scope_text = f" [{', '.join(str(scope) for scope in scopes)}]" if scopes else ""
        return f"{name} ({detail}){scope_text}"

    def parameter_lines(self, operation: Operation) -> list[str]:
        merged: dict[tuple[str, str], dict] = {}
        for raw in (*operation.path_parameters, *as_list(operation.spec.get("parameters"))):
            parameter = self.resolver.deref(raw)
            if isinstance(parameter.get("name"), str) and isinstance(parameter.get("in"), str):
                merged[(parameter["in"], parameter["name"])] = parameter
        lines = []
        for location, label in PARAM_LOCATIONS:
            entries = [self.parameter_text(p) for (where, _), p in merged.items() if where == location]
            if entries:
                lines.append(f"- {label}: {', '.join(entries)}")
        return lines

    def parameter_text(self, parameter: dict) -> str:
        schema = parameter.get("schema")
        if schema is None:
            content = as_dict(parameter.get("content"))
            schema = as_dict(content[sorted(content)[0]]).get("schema") if content else None
        flags = self.flags(as_dict(schema), parameter.get("required") is True or parameter.get("in") == "path")
        return f"{parameter['name']} ({', '.join([self.type_of(schema), *flags])})"

    def body_lines(self, raw_body: object) -> list[str]:
        if raw_body is None:
            return []
        body = self.resolver.deref(raw_body)
        by_type: dict[str, list[str]] = {}
        schemas: dict[str, object] = {}
        for content_type in sorted(as_dict(body.get("content"))):
            schema = as_dict(body["content"][content_type]).get("schema")
            type_text = self.type_of(schema)
            by_type.setdefault(type_text, []).append(content_type)
            schemas.setdefault(type_text, schema)
        required = "; required" if body.get("required") is True else ""
        if not by_type:
            return [f"- Body{required}: no content declared"]
        lines = []
        for type_text, content_types in by_type.items():
            lines.append(f"- Body ({', '.join(content_types)}{required}): {type_text}")
            lines += [f"  {line}" for line in self.field_lines(schemas[type_text])]
        return lines

    def response_lines(self, responses: dict) -> list[str]:
        if not responses:
            return ["- Responses: none declared"]
        lines = ["- Responses:"]
        for status in sorted(responses, key=status_sort_key):
            lines.append(f"  - {status}: {self.response_text(self.resolver.deref(responses[status]))}")
        return lines

    def response_text(self, response: dict) -> str:
        content = as_dict(response.get("content"))
        if not content:
            return "no body"
        grouped: dict[str, list[str]] = {}
        for content_type in sorted(content):
            grouped.setdefault(self.type_of(as_dict(content[content_type]).get("schema")), []).append(content_type)
        if len(grouped) == 1 and list(content) == [JSON_CONTENT_TYPE]:
            return next(iter(grouped))
        return "; ".join(f"({', '.join(types)}) {type_text}" for type_text, types in grouped.items())

    def type_of(self, raw: object, depth: int = 0) -> str:
        schema = as_dict(raw)
        if not schema:
            return "any"
        if isinstance(schema.get("$ref"), str):
            return self.ref_type(schema["$ref"])
        composed = self.composed_type(schema, depth)
        if composed:
            return composed
        types = [kind for kind in schema_types(schema) if kind != "null"]
        if "array" in types:
            return f"list[{self.type_of(schema.get('items'), depth)}]"
        if "object" in types or "properties" in schema:
            return self.object_type(schema, depth)
        base = "|".join(types) or ("enum" if "enum" in schema else "any")
        return f"{base}({schema['format']})" if isinstance(schema.get("format"), str) else base

    def composed_type(self, schema: dict, depth: int) -> str | None:
        """`allOf`/`oneOf`/`anyOf` as `keyword[A, B]`, a single non-null part as itself, or None without one."""
        for keyword in COMPOSITIONS:
            parts = [self.type_of(part, depth) for part in as_list(schema.get(keyword)) if not self.is_null(part)]
            if keyword == "allOf" and parts and as_dict(schema.get("properties")):
                parts.append(self.object_type(schema, depth))
            if parts:
                return parts[0] if len(parts) == 1 else f"{keyword}[{', '.join(parts)}]"
        return None

    def ref_type(self, ref: str) -> str:
        if not ref.startswith(LOCAL_REF_PREFIX):
            return f"external {ref}"
        return ref_name(ref) if self.resolver.lookup(ref) is not None else f"unresolved {ref}"

    def object_type(self, schema: dict, depth: int) -> str:
        properties = as_dict(schema.get("properties"))
        if properties:
            if depth >= MAX_INLINE_DEPTH:
                return "object{...}"
            inner = ", ".join(f"{name}: {self.type_of(value, depth + 1)}" for name, value in properties.items())
            return f"object{{{inner}}}"
        extra = schema.get("additionalProperties")
        if isinstance(extra, dict):
            return f"dict[str, {self.type_of(extra, depth + 1)}]"
        return "object"

    def is_null(self, raw: object) -> bool:
        schema = self.resolver.deref(raw)
        return schema_types(schema) == ["null"] or schema.get("enum") == [None]

    def is_nullable(self, schema: dict) -> bool:
        if schema.get("nullable") is True or "null" in schema_types(schema):
            return True
        return any(self.is_null(part) for keyword in COMPOSITIONS for part in as_list(schema.get(keyword)))

    def flags(self, schema: dict, required: bool) -> list[str]:
        flags = ["required"] if required else []
        flags += [name for name in ("readOnly", "writeOnly") if schema.get(name) is True]
        if self.is_nullable(schema):
            flags.append("nullable")
        if isinstance(schema.get("enum"), list):
            flags.append(f"enum: {enum_text(schema['enum'])}")
        return flags

    def field_lines(self, raw: object) -> list[str]:
        properties, required = self.collect_properties(raw, frozenset())
        lines = []
        for name, value in properties.items():
            schema = as_dict(value)
            text = ", ".join([self.type_of(schema), *self.flags(schema, name in required)])
            lines.append(f"- {name}: {text}")
        return lines

    def collect_properties(self, raw: object, seen: frozenset[str]) -> tuple[dict, set]:
        """Own and allOf-inherited properties with their required names; each `$ref` is followed once per branch."""
        schema = as_dict(raw)
        ref = schema.get("$ref")
        if isinstance(ref, str):
            if ref in seen:
                return {}, set()
            return self.collect_properties(self.resolver.lookup(ref), seen | {ref})
        properties: dict = {}
        required: set = set()
        for part in as_list(schema.get("allOf")):
            part_properties, part_required = self.collect_properties(part, seen)
            properties |= part_properties
            required |= part_required
        properties |= as_dict(schema.get("properties"))
        required |= {name for name in as_list(schema.get("required")) if isinstance(name, str)}
        return properties, required

    def referenced_schemas(self, operations: list[Operation]) -> list[tuple[str, str]]:
        """(name, ref) of every component schema the operations reach, directly or through other refs."""
        found: set[tuple[str, str]] = set()
        visited: set[str] = set()
        pending: list[object] = [op.spec for op in operations] + [p for op in operations for p in op.path_parameters]
        while pending:
            node = pending.pop()
            if isinstance(node, (list, tuple)):
                pending.extend(node)
            elif isinstance(node, dict):
                ref = node.get("$ref")
                if isinstance(ref, str) and ref not in visited:
                    visited.add(ref)
                    if ref.startswith(SCHEMA_REF_PREFIX) and self.resolver.lookup(ref) is not None:
                        found.add((ref_name(ref), ref))
                    pending.append(self.resolver.lookup(ref))
                pending.extend(value for key, value in node.items() if key != "$ref")
        return sorted(found)

    def schema_section(self, name: str, ref: str) -> list[str]:
        schema = as_dict(self.resolver.lookup(ref))
        lines = [f"### {name}"]
        bases = [
            ref_name(part["$ref"])
            for part in as_list(schema.get("allOf"))
            if isinstance(as_dict(part).get("$ref"), str)
        ]
        if bases:
            lines.append(f"allOf: {', '.join(bases)}")
        fields = self.field_lines(schema)
        if fields:
            return lines + fields
        summary = ", ".join([self.type_of(schema), *self.flags(schema, required=False)])
        return lines + [summary]


def schema_types(schema: dict) -> list[str]:
    kind = schema.get("type")
    if isinstance(kind, str):
        return [kind]
    return [item for item in as_list(kind) if isinstance(item, str)]


def enum_text(values: list) -> str:
    shown = [value if isinstance(value, str) else json.dumps(value) for value in values[:MAX_ENUM_VALUES]]
    hidden = len(values) - len(shown)
    return "|".join(shown) + (f" (+{hidden} more)" if hidden else "")


def status_sort_key(status: object) -> tuple[int, str]:
    text = str(status)
    return (0, text.zfill(3)) if text.isdigit() else (1, text)


if __name__ == "__main__":
    sys.exit(main())
