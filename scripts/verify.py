"""Run the offline baseline and capture a small, shareable verification summary."""
from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "artifacts" / "verification.json"


def run(command: list[str], *, cwd: Path = ROOT, env=None, timeout=120):
    return subprocess.run(command, cwd=cwd, env=env, check=True, timeout=timeout)


def isolated_env(folder: Path) -> dict[str, str]:
    """Keep process essentials, but remove provider and app configuration."""
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("HANGON_", "BOSON_", "OLLAMA_"))}
    env.update(PYTHONPATH=str(ROOT), HANGON_DB=str(folder / "calls.db"),
               HANGON_REPORTS_DIR=str(folder / "reports"))
    return env


def check_outcome(db: Path, expected: str, blocked: bool, number: str):
    with sqlite3.connect(db) as connection:
        row = connection.execute(
            "SELECT outcome FROM calls ORDER BY id DESC LIMIT 1").fetchone()
        exists = connection.execute(
            "SELECT 1 FROM blocklist WHERE number=?", (number,)).fetchone() is not None
    if row != (expected,) or exists != blocked:
        raise RuntimeError(f"Expected {expected}, blocked={blocked}; got {row}, blocked={exists}")


def scenarios():
    cases = [
        ("cra_scam", "+14165550111", "scam_ended", True),
        ("pharmacy_legit", "+14165550222", "forwarded", False),
        ("vague_unsure", "+14165550333", "message_taken", False),
        ("cra_scam", "+14165550111", "rejected", True),
    ]
    with tempfile.TemporaryDirectory(prefix="hangon-verify-") as temporary:
        folder = Path(temporary)
        env = isolated_env(folder)
        for scenario, number, outcome, blocked in cases:
            run([sys.executable, "-m", "hangon.sim", "--agent", "scripted",
                 "--classifier", "heuristic", "--scenario",
                 str(ROOT / "scenarios" / f"{scenario}.txt"), "--number", number],
                cwd=folder, env=env, timeout=30)
            check_outcome(folder / "calls.db", outcome, blocked, number)


def git_text(*args):
    result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                            text=True, timeout=10)
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def main() -> int:
    report = {"timestamp_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
              "revision": git_text("rev-parse", "HEAD"),
              "dirty": bool(git_text("status", "--porcelain")),
              "scope": "offline only; no live-provider or browser verification",
              "stages": [], "passed": False}
    stages = [("pytest", lambda: run([sys.executable, "-m", "pytest", "-q"])),
              ("isolated CLI scenarios", scenarios)]
    try:
        for name, check in stages:
            entry = {"name": name, "passed": False}
            report["stages"].append(entry)
            try:
                check()
                entry["passed"] = True
            except (subprocess.SubprocessError, OSError, RuntimeError, sqlite3.Error) as exc:
                entry["error"] = str(exc)
                print(f"FAIL: {name}: {exc}", file=sys.stderr)
                return 1
        report["passed"] = True
        return 0
    finally:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2) + "\n")
        print(f"Verification summary: {REPORT}")


if __name__ == "__main__":
    raise SystemExit(main())
