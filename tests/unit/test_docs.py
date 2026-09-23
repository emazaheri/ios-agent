"""The realities index, asserted against the directory it indexes.

`docs/realities/` is the most expensive knowledge in this repository and the
only thing pointing at it is a table. A file nobody links to is a file nobody
reads, and a link that no longer resolves is worse than no link, because it
reads as evidence the knowledge is still there. Neither failure shows up in a
diff.

The index lives in the directory's own README, so both halves of the check are
things a fresh clone actually has. An index kept anywhere else would be a test
whose subject can go missing.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_REALITIES = _ROOT / "docs" / "realities"
_INDEX = _REALITIES / "README.md"

_LINK = re.compile(r"\[[^\]]+\]\(([^):]+\.md)\)")


def _entries() -> list[Path]:
    return sorted(p for p in _REALITIES.glob("*.md") if p.name != "README.md")


def _linked_from_index() -> set[Path]:
    return {(_REALITIES / m).resolve() for m in _LINK.findall(_INDEX.read_text())}


def test_every_reality_file_is_in_the_index() -> None:
    linked = _linked_from_index()
    for path in _entries():
        assert path.resolve() in linked, (
            f"{path.name} is in docs/realities and the README does not link it"
        )


def test_every_link_in_the_index_resolves() -> None:
    for target in sorted(_linked_from_index()):
        assert target.exists(), f"the index links {target.name}, which does not exist"


@pytest.mark.parametrize("path", _entries(), ids=lambda p: p.name)
def test_every_reality_file_carries_entries(path: Path) -> None:
    """A heading and an introduction with nothing under them is not a split."""
    assert path.read_text().count("\n- **") >= 1, f"{path.name} holds no entries"
