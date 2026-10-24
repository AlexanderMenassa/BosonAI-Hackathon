"""SQLite persistence: blocklist, trusted contacts, and a log of every call."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import closing


def normalize_number(raw: str) -> str:
    """Keep digits and a leading '+'. '(416) 555-0100' -> '4165550100'."""
    raw = raw.strip()
    plus = "+" if raw.startswith("+") else ""
    return plus + re.sub(r"\D", "", raw)


class Store:
    def __init__(self, path: str = "hangon.db"):
        self.path = path
        self._init()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self) -> None:
        with closing(self._conn()) as c, c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS blocklist (
                    number TEXT PRIMARY KEY, reason TEXT, added_at REAL);
                CREATE TABLE IF NOT EXISTS trusted (
                    number TEXT PRIMARY KEY, label TEXT);
                CREATE TABLE IF NOT EXISTS calls (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    number TEXT, started_at REAL, ended_at REAL,
                    outcome TEXT, verdict TEXT,
                    evidence TEXT, transcript TEXT, reason TEXT);
                """
            )

    # --- blocklist -------------------------------------------------------
    def is_blocked(self, number: str) -> bool:
        with closing(self._conn()) as c:
            row = c.execute("SELECT 1 FROM blocklist WHERE number=?", (normalize_number(number),)).fetchone()
        return row is not None

    def block(self, number: str, reason: str = "") -> None:
        with closing(self._conn()) as c, c:
            c.execute(
                "INSERT OR REPLACE INTO blocklist(number, reason, added_at) VALUES (?,?,?)",
                (normalize_number(number), reason, time.time()),
            )

    def unblock(self, number: str) -> bool:
        with closing(self._conn()) as c, c:
            cur = c.execute("DELETE FROM blocklist WHERE number=?", (normalize_number(number),))
        return cur.rowcount > 0

    def blocklist(self) -> list[dict]:
        with closing(self._conn()) as c:
            return [dict(r) for r in c.execute("SELECT * FROM blocklist ORDER BY added_at DESC")]

    # --- trusted contacts ------------------------------------------------
    def is_trusted(self, number: str) -> bool:
        with closing(self._conn()) as c:
            row = c.execute("SELECT 1 FROM trusted WHERE number=?", (normalize_number(number),)).fetchone()
        return row is not None

    def add_trusted(self, number: str, label: str = "") -> None:
        with closing(self._conn()) as c, c:
            c.execute(
                "INSERT OR REPLACE INTO trusted(number, label) VALUES (?,?)",
                (normalize_number(number), label),
            )


    # --- call log --------------------------------------------------------
    def save_call(self, number: str, started_at: float, ended_at: float, outcome: str,
                  verdict: str, evidence: dict, transcript: list[tuple[str, str]], reason: str) -> int:
        with closing(self._conn()) as c, c:
            cur = c.execute(
                "INSERT INTO calls(number, started_at, ended_at, outcome, verdict, evidence, transcript, reason)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (normalize_number(number), started_at, ended_at, outcome, verdict,
                 json.dumps(evidence), json.dumps(transcript), reason),
            )
            return int(cur.lastrowid)
