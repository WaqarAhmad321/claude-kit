"""The MERN, Next.js and React stack rules' `paths` globs don't cross into each other's files."""

from pathlib import Path

import kitskills
import pytest

RULES_DIR = Path(__file__).resolve().parents[3] / "home" / "rules"


def rule_globs(name: str) -> tuple[str, ...]:
    text = (RULES_DIR / f"{name}.md").read_text(encoding="utf-8")
    return tuple(kitskills.parse_frontmatter(text).get("paths", []))


def matches(name: str, rel_path: str) -> bool:
    return any(kitskills.glob_matches(glob, rel_path) for glob in rule_globs(name))


@pytest.mark.parametrize(
    "rel_path",
    ["server/src/routes/x.ts", "server/src/models/x.ts", "server/src/app.ts"],
)
def test_node_express_loads_for_mern_server_code(rel_path):
    assert matches("node-express", rel_path)


@pytest.mark.parametrize(
    "rel_path",
    [
        "src/lib/supabase/server.ts",
        "src/routes/x.ts",
        "src/validation/user.schema.ts",
        "src/db/client.ts",
    ],
)
def test_node_express_does_not_load_for_other_stacks(rel_path):
    assert not matches("node-express", rel_path)


def test_mongodb_loads_for_mern_server_models():
    assert matches("mongodb", "server/src/models/x.ts")


@pytest.mark.parametrize(
    "rel_path",
    [
        "src/lib/supabase/server.ts",
        "src/routes/x.ts",
        "src/validation/user.schema.ts",
        "src/db/client.ts",
    ],
)
def test_mongodb_does_not_load_for_other_stacks(rel_path):
    assert not matches("mongodb", rel_path)


def test_react_loads_for_plain_client_code():
    assert matches("react", "src/routes/x.ts")


@pytest.mark.parametrize(
    "rel_path",
    ["server/src/app.ts", "server/src/routes/x.ts", "server/src/models/x.ts"],
)
def test_react_does_not_load_for_mern_server_code(rel_path):
    assert not matches("react", rel_path)
