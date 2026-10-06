"""A real 300-row list: Contacts on a simulator, seeded with generated people.

The other iOS agent tools fail here. mobile-mcp #288 hits `context deadline
exceeded` on a 300-row list, and agent-device #2552 crawls a long chat list's
tree for two to three minutes. A synthetic tree cannot say whether this stack
does better, because the cost is in what XCTest does with a real list, so the
list is a stock Apple app and the rows are real contacts.

Seeding goes through `simctl addmedia`, which has no undo and no dedupe, so the
address book is read first and only missing rows are added. The database is
opened read-only: the simulator owns it.
"""

from __future__ import annotations

import sqlite3
import subprocess
import tempfile
from pathlib import Path

CONTACTS = "com.apple.MobileAddressBook"
ROWS = 300
GIVEN = "Eval"


def row_name(i: int) -> str:
    """Display name of row ``i``: given name, then family name."""
    return f"{GIVEN} {_family(i)}"


def _family(i: int) -> str:
    return f"Row {i:03d}"


def _address_book(udid: str) -> Path:
    return (
        Path.home()
        / "Library/Developer/CoreSimulator/Devices"
        / udid
        / "data/Library/AddressBook/AddressBook.sqlitedb"
    )


def seeded_rows(udid: str) -> set[int]:
    """Which rows the simulator's address book already holds."""
    db = _address_book(udid)
    if not db.exists():
        return set()
    with sqlite3.connect(f"file:{db}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT Last FROM ABPerson WHERE First = ? AND Last LIKE 'Row %'", (GIVEN,)
        ).fetchall()
    return {int(last.split()[1]) for (last,) in rows if last.split()[1].isdigit()}


def seed(udid: str, rows: int = ROWS) -> int:
    """Add whichever of rows 1..``rows`` are missing. Returns how many were added."""
    missing = sorted(set(range(1, rows + 1)) - seeded_rows(udid))
    if not missing:
        return 0
    cards = "".join(
        f"BEGIN:VCARD\nVERSION:3.0\nN:{_family(i)};{GIVEN};;;\nFN:{row_name(i)}\nEND:VCARD\n"
        for i in missing
    )
    with tempfile.NamedTemporaryFile("w", suffix=".vcf", delete=False) as vcf:
        vcf.write(cards)
    try:
        subprocess.run(
            ["xcrun", "simctl", "addmedia", udid, vcf.name],
            check=True,
            capture_output=True,
            timeout=120,
        )
    finally:
        Path(vcf.name).unlink(missing_ok=True)
    return len(missing)
