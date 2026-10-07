"""The docs layout's naming rules, shared by the docs location guard and docs-check.py: which docs directories have
a naming rule of their own, and what marks a name as scratch, versioned or dated. Stdlib only; docs-check.py imports
it from its own directory, where templates/common/scripts/docnames.py links here."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

KEBAB_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\.mdx?")
DECISION_NAME = re.compile(r"\d{4}(?:-[a-z0-9]+)+\.md")
DECISIONS_DIR = "docs/decisions"
DOCS_DIR_RULES = {
    "docs/architecture": KEBAB_NAME,
    "docs/api": KEBAB_NAME,
    "docs/api/handoffs": KEBAB_NAME,
    "docs/partners": KEBAB_NAME,
    "docs/runbooks": KEBAB_NAME,
    "docs/guides": KEBAB_NAME,
    DECISIONS_DIR: DECISION_NAME,
}
SCRATCH_WORDS = frozenset(
    {"summary", "report", "notes", "plan", "fix", "status", "progress", "todo", "remaining", "findings"}
    | {"analysis", "final", "latest", "reply"}
)
_VERSION_PART = re.compile(r"(?:v|round)\d+")
_DATE = re.compile(r"(?:19|20)\d{2}-?\d{2}-?\d{2}")
_WORD_SEPARATORS = re.compile(r"[-_.\s]+")


@dataclass(frozen=True)
class ScratchMarks:
    words: tuple[str, ...]  # Scratch words and `v2`/`round3` parts, sorted.
    dated: bool

    def __bool__(self) -> bool:
        return bool(self.words) or self.dated


def docs_dir_rule(rel: str) -> re.Pattern | None:
    """The naming rule of the docs directory that holds `rel` directly, or None outside those directories."""
    return DOCS_DIR_RULES.get(PurePosixPath(rel).parent.as_posix())


def scratch_marks(rel: str) -> ScratchMarks:
    """What marks `rel`'s name as a scratch, versioned or dated file. A version part or a date always counts. A
    scratch word counts only outside the named docs directories, whose own naming rule decides there, so a runbook
    may say "fix" and a decision "plan"."""
    stem = PurePosixPath(rel).stem
    scratch_words_count = docs_dir_rule(rel) is None
    words = {
        word
        for word in _WORD_SEPARATORS.split(stem.lower())
        if _VERSION_PART.fullmatch(word) or (scratch_words_count and word in SCRATCH_WORDS)
    }
    return ScratchMarks(tuple(sorted(words)), bool(_DATE.search(stem)))
