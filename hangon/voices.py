"""Give the persona an older person's voice: register reference audio as a reusable Higgs voice.

None of Boson's preset voices sound elderly, so make one from a sample. Download an older man's (or woman's) MP3 and its
transcript from the hackathon voice library (boson-hackathon-voices.pages.dev), then:

    python -m hangon.voices create old_man.mp3 --text "the exact words spoken in the clip"
    python -m hangon.voices create old_man.mp3 --text-file old_man.txt --title "Walter"
    python -m hangon.voices list

`create` prints a voice_id; put it in .env as HIGGS_VOICE=<voice_id>. Creating the same audio twice returns the
same id, so it is safe to repeat. Use 5 to 30 seconds of clean speech, and only audio you have the right to use.
Uses Boson's Voices API (POST/GET /v1/audio/voices).
"""
from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.request
from pathlib import Path

from .classifier import post_json
from .config import Settings

API = "https://api.boson.ai/v1"


def create_voice(api_key: str, audio_path: str, text: str, title: str, api: str = API) -> dict:
    audio = base64.b64encode(Path(audio_path).read_bytes()).decode()
    return post_json(f"{api}/audio/voices", {"ref_audio": audio, "ref_text": text, "title": title},
                     {"Authorization": f"Bearer {api_key}"}, timeout=60)


def list_voices(api_key: str, api: str = API) -> list[dict]:
    req = urllib.request.Request(f"{api}/audio/voices", headers={"Authorization": f"Bearer {api_key}"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        payload = json.loads(resp.read())
    return payload.get("data", payload.get("voices", [])) if isinstance(payload, dict) else payload


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("create", help="register an audio sample as a reusable voice")
    c.add_argument("audio", help="wav, mp3, opus, pcm or flac; 5-30 s of clean speech")
    words = c.add_mutually_exclusive_group(required=True)
    words.add_argument("--text", help="exact transcript of the clip")
    words.add_argument("--text-file", help="file holding the exact transcript")
    c.add_argument("--title", default="Walter")
    sub.add_parser("list", help="show your registered voices")
    args = ap.parse_args()

    s = Settings.from_env()
    if not s.boson_api_key:
        raise SystemExit("BOSON_API_KEY is not set (put it in .env)")
    try:
        if args.command == "list":
            for v in list_voices(s.boson_api_key):
                print(f"{v.get('voice_id', v.get('id', '?'))}   {v.get('title', '')}")
            return
        text = args.text if args.text is not None else Path(args.text_file).read_text(encoding="utf-8").strip()
        created = create_voice(s.boson_api_key, args.audio, text, args.title)
    except Exception as e:
        raise SystemExit(f"voice API error: {e}")
    print(f"voice_id: {created.get('voice_id')}   title: {created.get('title')}")
    print(f"\nPut this in .env:  HIGGS_VOICE={created.get('voice_id')}")


if __name__ == "__main__":
    main()
