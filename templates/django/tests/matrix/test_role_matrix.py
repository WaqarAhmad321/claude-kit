"""Run the role matrix in roles.yaml over real HTTP against a running stack.

Every operation is called as every role, and the status must be the one the matrix expects; a 5xx always fails.
The role-matrix service in compose.test.yml runs it, with MATRIX_FILE and MATRIX_BASE_URL saying what and where.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import pytest
import yaml

MATRIX_FILE = Path(os.environ.get("MATRIX_FILE", str(Path(__file__).with_name("roles.yaml"))))
BASE_URL = os.environ.get("MATRIX_BASE_URL", "http://localhost:8000")
TIMEOUT_SECONDS = 10
SERVER_ERROR = 500
SIGNUP_KIND = "user"
JSON_TYPE = "application/json"
PLACEHOLDER = re.compile(r"\{(\w+)\.(\w+)\}")


@dataclass(frozen=True)
class Response:
    status: int
    body: Any


def load_matrix() -> dict[str, Any]:
    matrix = yaml.safe_load(MATRIX_FILE.read_text(encoding="utf-8"))
    if not isinstance(matrix, dict) or not isinstance(matrix.get("operations"), list):
        raise TypeError(f"{MATRIX_FILE} must hold a mapping with an operations list.")
    return matrix


MATRIX = load_matrix()
CELLS = [(index, role) for index, operation in enumerate(MATRIX["operations"]) for role in operation["expect"]]


def call(
    method: str, path: str, *, token: str | None = None, body: object = None, content_type: str = JSON_TYPE
) -> Response:
    base = urlsplit(BASE_URL)
    connection_class = http.client.HTTPSConnection if base.scheme == "https" else http.client.HTTPConnection
    connection = connection_class(base.netloc, timeout=TIMEOUT_SECONDS)
    headers = {"Accept": JSON_TYPE}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = content_type
    try:
        connection.request(method, base.path.rstrip("/") + path, body=payload, headers=headers)
        response = connection.getresponse()
        return Response(response.status, parse_json(response.read()))
    finally:
        connection.close()


def parse_json(raw: bytes) -> Any:
    try:
        return json.loads(raw) if raw else None
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


def credentials_for(kind: str) -> dict[str, str]:
    auth = MATRIX["auth"]
    if kind != SIGNUP_KIND:
        return {"email": auth[kind]["email"], "password": auth[kind]["password"]}
    email, password = f"matrix-{uuid.uuid4().hex}@example.test", f"pw-{uuid.uuid4().hex}"
    signup = call("POST", auth["signup"], body={"email": email, "password": password, "display_name": "Matrix"})
    assert signup.status == 201, f"signup answered {signup.status}: {signup.body}"
    return {"email": email, "password": password}


@pytest.fixture(scope="session")
def tokens() -> dict[str, str | None]:
    auth = MATRIX["auth"]
    tokens: dict[str, str | None] = {}
    for role, kind in MATRIX["roles"].items():
        if kind is None:
            tokens[role] = None
            continue
        login = call("POST", auth["login"], body=credentials_for(kind))
        assert login.status == 200, f"login as {role} answered {login.status}: {login.body}"
        tokens[role] = login.body[auth["token"]]
    return tokens


def resolve(path: str, tokens: dict[str, str | None]) -> str:
    """Fill `{fixture.field}` with a fixture created for this cell alone."""
    created: dict[str, Any] = {}

    def field(match: re.Match[str]) -> str:
        name, attribute = match.groups()
        if name not in created:
            spec = MATRIX["fixtures"][name]
            method, fixture_path = spec["request"].split(" ", 1)
            response = call(method, fixture_path, token=tokens[spec["as"]], body=spec.get("body"))
            assert response.status == 201, f"fixture {name} answered {response.status}: {response.body}"
            created[name] = response.body
        return str(created[name][attribute])

    return PLACEHOLDER.sub(field, path)


@pytest.mark.parametrize(
    ("index", "role"), CELLS, ids=[f"{MATRIX['operations'][index]['request']} as {role}" for index, role in CELLS]
)
def test_role_matrix(index: int, role: str, tokens: dict[str, str | None]) -> None:
    operation = MATRIX["operations"][index]
    method, path = operation["request"].split(" ", 1)
    response = call(
        method,
        resolve(path, tokens),
        token=tokens[role],
        body=operation.get("body"),
        content_type=operation.get("content_type", JSON_TYPE),
    )
    assert response.status < SERVER_ERROR, f"server error {response.status}: {response.body}"
    assert response.status == operation["expect"][role], f"answered {response.status}: {response.body}"
