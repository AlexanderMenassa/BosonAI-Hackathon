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
            columns = {row[1] for row in c.execute("PRAGMA table_info(calls)")}
            if "public_call_id" not in columns:
                c.execute("ALTER TABLE calls ADD COLUMN public_call_id TEXT")
            if "request_id" not in columns:
                c.execute("ALTER TABLE calls ADD COLUMN request_id TEXT")
            if "screening_ended_at" not in columns:
                c.execute("ALTER TABLE calls ADD COLUMN screening_ended_at REAL")
            if "screening_result" not in columns:
                c.execute("ALTER TABLE calls ADD COLUMN screening_result TEXT")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS calls_public_call_id ON calls(public_call_id)")
            c.execute("CREATE UNIQUE INDEX IF NOT EXISTS calls_request_id ON calls(request_id)")

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
    def begin_call(self, public_call_id: str, request_id: str, number: str, started_at: float) -> int:
        """Reserve the browser call's durable identity before routing."""
        with closing(self._conn()) as c, c:
            cur = c.execute(
                "INSERT INTO calls(public_call_id, request_id, number, started_at) VALUES (?,?,?,?)",
                (public_call_id, request_id, normalize_number(number), started_at),
            )
            return int(cur.lastrowid)

    def call_by_request(self, request_id: str) -> dict | None:
        with closing(self._conn()) as c:
            row = c.execute("SELECT * FROM calls WHERE request_id=?", (request_id,)).fetchone()
            return dict(row) if row else None

    def calls(self) -> list[dict]:
        """Return call records for local inspection and verification."""
        with closing(self._conn()) as c:
            return [dict(row) for row in c.execute("SELECT * FROM calls ORDER BY id")]

    def complete_screening(self, public_call_id: str, ended_at: float, result: str) -> None:
        """Mark screening once; the overall call remains open through human audio."""
        with closing(self._conn()) as c, c:
            c.execute("UPDATE calls SET screening_ended_at=?, screening_result=? "
                      "WHERE public_call_id=? AND screening_ended_at IS NULL AND ended_at IS NULL",
                      (ended_at, result, public_call_id))

    def complete_call(self, public_call_id: str, ended_at: float, outcome: str,
                      verdict: str, evidence: dict, transcript: list[tuple[str, str]], reason: str) -> int:
        """Complete an arrival once; duplicate completion leaves the first result intact."""
        with closing(self._conn()) as c, c:
            cur = c.execute(
                "UPDATE calls SET ended_at=?, outcome=?, verdict=?, evidence=?, transcript=?, reason=? "
                "WHERE public_call_id=? AND ended_at IS NULL",
                (ended_at, outcome, verdict, json.dumps(evidence), json.dumps(transcript),
                 reason, public_call_id),
            )
            if cur.rowcount == 0:
                row = c.execute("SELECT id, ended_at FROM calls WHERE public_call_id=?", (public_call_id,)).fetchone()
                if row is None:
                    raise KeyError(public_call_id)
                return int(row["id"])
            row = c.execute("SELECT id FROM calls WHERE public_call_id=?", (public_call_id,)).fetchone()
            return int(row["id"])

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
