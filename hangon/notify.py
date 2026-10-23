"""Notifications. The MVP prints to the console and appends to an outbox file.

Swap in a real SMS/email sender later by implementing the same `send` method.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Protocol


class Notifier(Protocol):
    def send(self, to: str, text: str, attachment: str | None = None) -> None: ...


class ConsoleNotifier:
    def __init__(self, outbox_dir: str = "reports", echo: bool = True):
        self.outbox = Path(outbox_dir) / "outbox.jsonl"
        self.outbox.parent.mkdir(parents=True, exist_ok=True)
        self.echo = echo

    def send(self, to: str, text: str, attachment: str | None = None) -> None:
        with self.outbox.open("a") as f:
            f.write(json.dumps({"ts": time.time(), "to": to, "text": text, "attachment": attachment}) + "\n")
        if self.echo:
            print(f"\n  SMS to {to or '(protected person)'}:\n    " + text.replace("\n", "\n    "))
            if attachment:
                print(f"    [attachment: {attachment}]")
