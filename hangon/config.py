"""Settings come from environment variables (optionally loaded from a .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


@dataclass
class Settings:
    db_path: str = "hangon.db"
    reports_dir: str = "reports"
    protected_phone: str = ""
    guardian_phone: str = ""

    # Call limits. Placeholder defaults: tune after trying real calls.
    max_turns: int = 10
    max_seconds: float = 180.0

    # Verdict thresholds (on the classifier's scam score, 0..1) with hysteresis.
    scam_enter: float = 0.75
    scam_exit: float = 0.40       # score must stay below this to leave SCAM...
    scam_exit_turns: int = 2      # ...for this many consecutive turns
    legit_enter: float = 0.25

    # Local classifier
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"
    ollama_timeout: float = 20.0

    # Higgs Realtime
    boson_api_key: str = ""
    boson_url: str = "wss://api.boson.ai/v1/realtime?model=higgs-realtime"

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        e = os.environ
        return cls(
            db_path=e.get("HANGON_DB", "hangon.db"),
            reports_dir=e.get("HANGON_REPORTS_DIR", "reports"),
            protected_phone=e.get("HANGON_PROTECTED_PHONE", ""),
            guardian_phone=e.get("HANGON_GUARDIAN_PHONE", ""),
            max_turns=int(e.get("HANGON_MAX_TURNS", 10)),
            max_seconds=float(e.get("HANGON_MAX_SECONDS", 180)),
            scam_enter=float(e.get("HANGON_SCAM_ENTER", 0.75)),
            scam_exit=float(e.get("HANGON_SCAM_EXIT", 0.40)),
            scam_exit_turns=int(e.get("HANGON_SCAM_EXIT_TURNS", 2)),
            legit_enter=float(e.get("HANGON_LEGIT_ENTER", 0.25)),
            ollama_url=e.get("OLLAMA_URL", "http://localhost:11434"),
            ollama_model=e.get("OLLAMA_MODEL", "llama3.2:3b"),
            ollama_timeout=float(e.get("OLLAMA_TIMEOUT", 20)),
            boson_api_key=e.get("BOSON_API_KEY", ""),
            boson_url=e.get("BOSON_REALTIME_URL", "wss://api.boson.ai/v1/realtime?model=higgs-realtime"),
        )
