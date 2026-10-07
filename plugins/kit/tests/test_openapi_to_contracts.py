import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "openapi_to_contracts.py"
BEARER = {"jwtAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}}
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def run(*args, stdin: str | None = None, cwd: Path | None = None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=60,
    )


def ref(name: str) -> dict:
    return {"$ref": f"#/components/schemas/{name}"}


def ok(schema: dict | None = None, status: str = "200") -> dict:
    response = {"description": "ok"}
    if schema is not None:
        response["content"] = {"application/json": {"schema": schema}}
    return {"responses": {status: response}}


def openapi(paths: dict, schemas: dict | None = None, **top) -> dict:
    return {
        "openapi": "3.0.3",
        "info": {"title": "Demo", "version": "1.0.0"},
        "paths": paths,
        "components": {"schemas": schemas or {}, "securitySchemes": BEARER},
        **top,
    }


def contracts(tmp_path: Path, document: dict, *args: str) -> str:
    (tmp_path / "openapi.json").write_text(json.dumps(document))
    result = run("openapi.json", *args, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    return result.stdout


def section(text: str, heading: str) -> str:
    """The lines under one `### heading`, up to the next heading."""
    after = text.split(f"### {heading}\n", 1)[1]
    return re.split(r"\n#{2,3} ", after, maxsplit=1)[0]


def test_operations_group_by_the_first_segment_after_the_auto_detected_prefix(tmp_path):
    document = openapi(
        {
            "/api/v1/properties/": {"get": ok(), "post": ok()},
            "/api/v1/properties/{id}/": {"get": ok()},
            "/api/v1/deals/": {"get": ok()},
        }
    )
    (tmp_path / "openapi.json").write_text(json.dumps(document))
    result = run("openapi.json", "--out", "drafts", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == ["drafts/deals/CONTRACTS.md", "drafts/properties/CONTRACTS.md"]
    properties = (tmp_path / "drafts/properties/CONTRACTS.md").read_text()
    assert "# Contracts: properties" in properties
    assert "Base path: `/api/v1`." in properties
    headings = [line for line in properties.splitlines() if line.startswith("### ")]
    assert headings == ["### GET /properties/", "### POST /properties/", "### GET /properties/{id}/"]
    assert "--module properties > CONTRACTS.md" in properties.splitlines()[0]
    assert "deals" not in properties


def test_auto_prefix_keeps_the_module_segment_when_every_path_shares_it(tmp_path):
    one_module = openapi({"/api/v1/properties/{id}/": {"get": ok()}, "/api/v1/properties/{id}/photos/": {"get": ok()}})
    text = contracts(tmp_path, one_module)
    assert "# Contracts: properties" in text
    assert "### GET /properties/{id}/photos/" in text


def test_an_explicit_prefix_wins_and_a_slash_strips_nothing(tmp_path):
    document = openapi({"/api/v1/properties/": {"get": ok()}, "/health/": {"get": ok()}})
    stripped = contracts(tmp_path, document, "--strip-prefix", "/api/v1")
    assert "# Contracts: health, properties" in stripped
    assert "### GET /health/" in stripped
    assert "### GET /properties/" in stripped
    unstripped = contracts(tmp_path, document, "--strip-prefix", "/")
    assert "# Contracts: api, health" in unstripped
    assert "### GET /api/v1/properties/" in unstripped
    assert "--strip-prefix / " in unstripped.splitlines()[0]


def test_refs_resolve_to_schema_names_and_every_reachable_schema_is_listed(tmp_path):
    schemas = {
        "Property": {
            "type": "object",
            "required": ["id"],
            "properties": {"id": {"type": "integer", "readOnly": True}, "owner": ref("User")},
        },
        "User": {"type": "object", "properties": {"email": {"type": "string", "format": "email"}}},
        "Unused": {"type": "object", "properties": {"x": {"type": "string"}}},
    }
    text = contracts(tmp_path, openapi({"/api/properties/{id}/": {"get": ok(ref("Property"))}}, schemas))
    assert "  - 200: Property" in text
    schema_headings = [line for line in text.split("## Schemas", 1)[1].splitlines() if line.startswith("### ")]
    assert schema_headings == ["### Property", "### User"]
    assert "- id: integer, required, readOnly" in section(text, "Property")
    assert "- owner: User" in section(text, "Property")
    assert "- email: string(email)" in section(text, "User")


def test_cyclic_refs_render_by_name_and_terminate(tmp_path):
    schemas = {
        "Node": {
            "type": "object",
            "properties": {"parent": ref("Node"), "children": {"type": "array", "items": ref("Node")}},
        },
        "Left": {"allOf": [ref("Right")]},
        "Right": {"allOf": [ref("Left")], "properties": {"side": {"type": "string"}}},
    }
    paths = {"/api/nodes/": {"get": ok(ref("Node"))}, "/api/sides/": {"get": ok(ref("Left"))}}
    text = contracts(tmp_path, openapi(paths, schemas))
    assert "- parent: Node" in section(text, "Node")
    assert "- children: list[Node]" in section(text, "Node")
    assert "- side: string" in section(text, "Left")
    assert "allOf: Left" in section(text, "Right")


def test_allof_merges_inherited_fields_and_names_its_bases(tmp_path):
    schemas = {
        "Base": {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}},
        "Pet": {
            "allOf": [
                ref("Base"),
                {"type": "object", "required": ["name"], "properties": {"name": {"type": "string"}}},
            ]
        },
    }
    text = contracts(tmp_path, openapi({"/api/pets/": {"get": ok(ref("Pet"))}}, schemas))
    pet = section(text, "Pet")
    assert "allOf: Base" in pet
    assert "- id: integer, required" in pet
    assert "- name: string, required" in pet


def test_arrays_render_as_list_of_their_items(tmp_path):
    schemas = {
        "Grid": {
            "type": "object",
            "properties": {
                "tags": {"type": "array", "items": {"type": "string"}},
                "cells": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}},
            },
        }
    }
    text = contracts(tmp_path, openapi({"/api/grids/": {"get": ok({"type": "array", "items": ref("Grid")})}}, schemas))
    assert "  - 200: list[Grid]" in text
    assert "- tags: list[string]" in section(text, "Grid")
    assert "- cells: list[list[integer]]" in section(text, "Grid")


def test_auth_is_none_for_public_operations_and_names_the_bearer_scheme(tmp_path):
    paths = {
        "/api/deals/": {
            "get": ok(),
            "post": {**ok(), "security": []},
            "put": {**ok(), "security": [{"jwtAuth": []}, {}]},
        }
    }
    text = contracts(tmp_path, openapi(paths, security=[{"jwtAuth": []}]))
    assert "- Auth: jwtAuth (http bearer)" in section(text, "GET /deals/")
    assert "- Auth: none" in section(text, "POST /deals/")
    assert "- Auth: jwtAuth (http bearer) or none" in section(text, "PUT /deals/")
    no_global = contracts(tmp_path, openapi({"/api/deals/": {"get": ok()}}))
    assert "- Auth: none" in no_global


def test_parameters_body_and_responses_are_compact(tmp_path):
    body_schema = {
        "type": "object",
        "required": ["title"],
        "properties": {
            "title": {"type": "string"},
            "status": {"type": "string", "enum": ["draft", "live"]},
            "note": {"type": ["string", "null"]},
            "password": {"type": "string", "writeOnly": True},
        },
    }
    operation = {
        "operationId": "deals_update",
        "parameters": [{"name": "expand", "in": "query", "schema": {"type": "boolean"}}],
        "requestBody": {"required": True, "content": {"application/json": {"schema": body_schema}}},
        "responses": {
            "204": {"description": "done"},
            "400": {
                "description": "bad",
                "content": {
                    "application/json": {"schema": {"type": "object", "properties": {"detail": {"type": "string"}}}}
                },
            },
        },
    }
    path_item = {"parameters": [{"name": "id", "in": "path", "required": True, "schema": {"type": "integer"}}]}
    document = openapi({"/api/deals/{id}/": {**path_item, "patch": operation}})
    document["openapi"] = "3.1.0"
    text = section(contracts(tmp_path, document), "PATCH /deals/{id}/")
    assert "- operationId: deals_update" in text
    assert "- Path: id (integer, required)" in text
    assert "- Query: expand (boolean)" in text
    assert (
        "- Body (application/json; required): object{title: string, status: string, note: string, password: string}"
        in text
    )
    assert "  - title: string, required" in text
    assert "  - status: string, enum: draft|live" in text
    assert "  - note: string, nullable" in text
    assert "  - password: string, writeOnly" in text
    assert "  - 204: no body" in text
    assert "  - 400: object{detail: string}" in text


def test_output_is_deterministic_and_starts_with_the_generated_header(tmp_path):
    paths = {"/api/b/": {"get": ok(ref("B"))}, "/api/a/": {"delete": ok(), "get": ok(ref("A"))}}
    schemas = {"B": {"type": "object", "properties": {"x": ref("A")}}, "A": {"type": "string"}}
    first = contracts(tmp_path, openapi(paths, schemas))
    assert first == contracts(tmp_path, openapi(paths, schemas))
    header = first.splitlines()[0]
    assert header.startswith("<!-- GENERATED by openapi_to_contracts.py; do not edit. Regenerate: ")
    assert '"${CLAUDE_PLUGIN_ROOT}/scripts/openapi_to_contracts.py" openapi.json --strip-prefix /api >' in header
    assert not DATE.search(first)
    headings = [line for line in first.splitlines() if line.startswith("### ")]
    assert headings == ["### GET /a/", "### DELETE /a/", "### GET /b/", "### A", "### B"]


def test_selected_modules_join_into_one_document_on_stdout(tmp_path):
    paths = {"/api/a/": {"get": ok()}, "/api/b/": {"get": ok()}, "/api/c/": {"get": ok()}}
    text = contracts(tmp_path, openapi(paths), "--module", "c", "--module", "a")
    assert text.count("<!-- GENERATED") == 1
    assert "# Contracts: a, c" in text
    assert "--module a --module c > CONTRACTS.md" in text.splitlines()[0]
    assert "/b/" not in text


def test_the_spec_is_read_from_stdin_with_a_dash(tmp_path):
    document = json.dumps(openapi({"/api/deals/": {"get": ok()}}))
    result = run("-", stdin=document, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "### GET /deals/" in result.stdout
    assert (
        '<OpenAPI JSON> | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/openapi_to_contracts.py" - --strip-prefix /api >'
        in (result.stdout.splitlines()[0])
    )


def test_an_unknown_module_exits_2_and_lists_the_modules(tmp_path):
    (tmp_path / "openapi.json").write_text(json.dumps(openapi({"/api/deals/": {"get": ok()}})))
    result = run("openapi.json", "--module", "nope", cwd=tmp_path)
    assert (result.returncode, result.stdout) == (2, "")
    assert "no operations for module(s) nope. Modules in this spec: deals." in result.stderr


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("{not json", "isn't valid JSON"),
        ("[1, 2]", "the top level must be a JSON object"),
        ('{"swagger": "2.0", "paths": {}}', "Swagger 2.0"),
        ('{"info": {}, "paths": {}}', "isn't an OpenAPI 3.x document"),
        ('{"openapi": "3.0.3"}', "has no `paths` object"),
        ('{"openapi": "3.0.3", "paths": {}}', "declares no operations"),
    ],
)
def test_input_that_is_not_openapi_3_json_exits_2_with_a_message(tmp_path, content, message):
    (tmp_path / "spec.json").write_text(content)
    result = run("spec.json", cwd=tmp_path)
    assert (result.returncode, result.stdout) == (2, "")
    assert result.stderr.startswith("openapi_to_contracts: ")
    assert message in result.stderr


def test_invalid_json_on_stdin_and_a_missing_file_exit_2(tmp_path):
    from_stdin = run("-", stdin="nope", cwd=tmp_path)
    assert from_stdin.returncode == 2
    assert "stdin isn't valid JSON" in from_stdin.stderr
    missing = run("missing.json", cwd=tmp_path)
    assert missing.returncode == 2
    assert "can't read missing.json" in missing.stderr
