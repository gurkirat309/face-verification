"""DEMO TOOL: tamper the ledger file itself to prove the hash chain catches it.

This edits one field inside the most recent evidence entry of the ledger JSON --
exactly what a malicious actor with disk access would try -- WITHOUT recomputing
the downstream hashes (they can't; they'd need to rewrite every following entry).
Running `python -m src.verify` afterwards then reports the chain break and names
the exact entry.

    python tools/tamper_ledger.py                 # tamper data/ledger.json
    python tools/tamper_ledger.py path/to/ledger.json

This is a demonstration aid, not part of the pipeline.
"""

from __future__ import annotations

import json
import os
import sys


def main(argv: list[str]) -> int:
    path = argv[1] if len(argv) > 1 else os.path.join("data", "ledger.json")
    if not os.path.exists(path):
        print(f"No ledger at {path}", file=sys.stderr)
        return 1
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    entries = data.get("entries", [])
    target = None
    for e in reversed(entries):
        if e.get("type") == "evidence":
            target = e
            break
    if target is None:
        print("No evidence entry to tamper.", file=sys.stderr)
        return 1

    old = target["payload"].get("source_url", "")
    new = "https://attacker.example.com/planted"
    target["payload"]["source_url"] = new
    # Note: we deliberately do NOT update entry_hash -> the chain will not verify.
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)

    print(f"Tampered ledger entry #{target['index']} in {path}")
    print(f"  source_url: {old!r}")
    print(f"          -> {new!r}")
    print("  (entry_hash left unchanged -> hash chain is now broken)")
    print("\nNow run:  python -m src.verify --evidence out/evidence_001.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
