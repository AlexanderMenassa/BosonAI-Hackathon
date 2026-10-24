"""Settings come from environment variables (optionally loaded from a .env file)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str = ".env") -> None:
    p = Path(path)
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
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
    scam_enter: float = 0.85
    scam_exit: float = 0.6       # score must stay below this to leave SCAM...
    scam_exit_turns: int = 2      # ...for this many consecutive turns
    legit_enter: float = 0.15
    benign_turns: int = 4

    # Local classifier
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.2:3b"
    ollama_timeout: float = 30.0

    # Hosted classifier (Google Gemini API)
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_timeout: float = 30.0

    # Higgs Realtime
    boson_api_key: str = ""
    higgs_model: str = "higgs-realtime"  # run `python -m hangon.list_models` to see what your key can use
    # Margaret's voice: a preset (default, eleanor, nora, chloe, ...) or a voice_id from `python -m hangon.voices`
    higgs_voice: str = "default"
    boson_url: str = "wss://api.boson.ai/v1/realtime?model=higgs-realtime"

    # Voice: how sure Higgs's speech detector must be (0..1; server default 0.55), and the fewest words that count
    # as a real interruption while the agent is speaking (shorter is treated as noise and the agent carries on).
    # Outside interruptions every utterance counts, so one-word answers like "John" or "Yes" work.
    vad_threshold: float = 0.8
    min_interrupt_words: int = 2

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
            scam_enter=float(e.get("HANGON_SCAM_ENTER", 0.85)),
            scam_exit=float(e.get("HANGON_SCAM_EXIT", 0.7)),
            scam_exit_turns=int(e.get("HANGON_SCAM_EXIT_TURNS", 2)),
            legit_enter=float(e.get("HANGON_LEGIT_ENTER", 0.3)),
            benign_turns=int(e.get("HANGON_BENIGN_TURNS", 4)),
            ollama_url=e.get("OLLAMA_URL", "http://localhost:11434"),
            ollama_model=e.get("OLLAMA_MODEL", "llama3.2:3b"),
            ollama_timeout=float(e.get("OLLAMA_TIMEOUT", 20)),
            gemini_api_key=e.get("GEMINI_API_KEY", ""),
            gemini_model=e.get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
            gemini_timeout=float(e.get("GEMINI_TIMEOUT", 15)),
            boson_api_key=e.get("BOSON_API_KEY", ""),
            higgs_model=e.get("HIGGS_MODEL", "higgs-realtime"),
            higgs_voice=e.get("HIGGS_VOICE", "default"),
            boson_url=e.get("BOSON_REALTIME_URL",
                            f"wss://api.boson.ai/v1/realtime?model={e.get('HIGGS_MODEL', 'higgs-realtime')}"),
            vad_threshold=float(e.get("HANGON_VAD_THRESHOLD", 0.8)),
            min_interrupt_words=int(e.get("HANGON_MIN_INTERRUPT_WORDS", 2)),
        )
