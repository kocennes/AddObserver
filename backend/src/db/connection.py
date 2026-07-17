"""sqlite3 connection helper for the dependency-free DB skeleton (see package docstring)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .schema import SCHEMA_STATEMENTS


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    """Open a sqlite3 connection with the skeleton schema applied.

    ``path`` defaults to an in-memory database, used by the test suite. sqlite3 disables
    foreign key enforcement by default, so it is turned on explicitly here.
    """
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)
    conn.commit()
    return conn
