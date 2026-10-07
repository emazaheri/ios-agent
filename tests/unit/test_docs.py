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


# -- the safety pages, which a reader copies from ------------------------------

_THREAT_MODEL = _ROOT / "docs" / "threat-model.md"
_ANY_LINK = re.compile(r"\]\(([^)#:]+)(?:#[^)]*)?\)")


def test_the_threat_model_is_linked_from_both_safety_pages() -> None:
    """A security document nobody is pointed at is one nobody reads."""
    for page in ("SAFETY.md", "SECURITY.md"):
        assert "docs/threat-model.md" in (_ROOT / page).read_text(), (
            f"{page} does not link the threat model"
        )


def test_every_relative_link_in_the_threat_model_resolves() -> None:
    for target in _ANY_LINK.findall(_THREAT_MODEL.read_text()):
        path = (_THREAT_MODEL.parent / target).resolve()
        assert path.exists(), f"the threat model links {target}, which does not exist"


def test_the_safety_config_example_only_names_real_settings() -> None:
    """The example is the thing people copy, so every key in it must exist.

    `SAFETY.md` documented `redact_screenshots = false` long after the setting
    was removed. The configuration ignores unknown keys, so a reader who set it
    to true believing screenshots were redacted got no redaction and no error.
    """
    from ios_mcp.config import PolicySettings

    text = (_ROOT / "SAFETY.md").read_text()
    block = re.search(r"```toml\n\[policy\]\n(.*?)```", text, re.S)
    assert block, "SAFETY.md has no [policy] example to check"
    keys = {line.split("=", 1)[0].strip() for line in block.group(1).splitlines() if "=" in line}

    unknown = keys - set(PolicySettings.model_fields)
    assert not unknown, f"SAFETY.md documents settings that do not exist: {sorted(unknown)}"


# -- the command line reference -----------------------------------------------


def test_every_ios_mcp_command_and_flag_is_in_the_cli_reference() -> None:
    from cli_docs import missing_from_doc

    from ios_mcp.__main__ import build_parser

    assert missing_from_doc(build_parser()) == [], "docs/cli.md does not mention these"


def test_the_library_guide_imports_only_the_public_surface() -> None:
    """The guide is what an outside agent copies, so it may name only what is kept stable."""
    from tests.unit.test_layering import _PUBLIC_SURFACE

    guide = (_ROOT / "docs" / "library.md").read_text()
    imported = set(re.findall(r"^from (ios_mcp[\w.]*) import", guide, flags=re.MULTILINE))
    assert imported, "the guide no longer shows any imports to check"
    assert imported <= _PUBLIC_SURFACE, f"not public: {sorted(imported - _PUBLIC_SURFACE)}"
