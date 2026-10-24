"""List the models your Boson API key can use (OpenAI-compatible GET /v1/models).

    python -m hangon.list_models
    python -m hangon.list_models --raw        # print the full JSON reply

Then put the realtime model you want in .env:  HIGGS_MODEL=<id>
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

from .config import Settings

URLS = ("https://api.boson.ai/v1/models", "https://api.boson.ai/models")


def fetch(url: str, key: str) -> dict:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


def model_ids(payload) -> list[str]:
    items = payload.get("data", payload.get("models", [])) if isinstance(payload, dict) else payload
    ids = []
    for m in items or []:
        ids.append(m.get("id") or m.get("name") or json.dumps(m) if isinstance(m, dict) else str(m))
    return ids


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--raw", action="store_true")
    args = ap.parse_args()
    s = Settings.from_env()
    if not s.boson_api_key:
        raise SystemExit("BOSON_API_KEY is not set (put it in .env)")
    for url in URLS:
        try:
            payload = fetch(url, s.boson_api_key)
        except urllib.error.HTTPError as e:
            print(f"{url} -> HTTP {e.code} {e.reason}", file=sys.stderr)
            continue
        except Exception as e:
            print(f"{url} -> {type(e).__name__}: {e}", file=sys.stderr)
            continue
        if args.raw:
            print(json.dumps(payload, indent=2))
        ids = model_ids(payload)
        print(f"\n{len(ids)} models from {url}:")
        for i in ids:
            print(("  * " if "realtime" in i.lower() else "    ") + i)
        print("\n(* = realtime models.)  Currently using HIGGS_MODEL =", s.higgs_model)
        return
    print("\nNo models endpoint answered. Ask the organizers for the exact model id, then set HIGGS_MODEL in .env.")


if __name__ == "__main__":
    main()
