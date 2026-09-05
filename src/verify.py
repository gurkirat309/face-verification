"""Re-verify an anchored evidence bundle -- the tamper test's centerpiece.

    python -m src.verify --evidence out/evidence_001.json

It runs four independent integrity checks and prints any mismatched hashes side
by side (far more convincing on camera than a lone green check):

  1. Ledger hash-chain intact?          (detects editing the ledger file itself)
  2. bundle_hash matches the anchor?    (detects editing fields in the JSON)
  3. Image SHA-256 matches the file?    (detects altering one byte of the image)
  4. Post-text SHA-256 matches the file?(detects altering one character of text)
  + pHash distance (informational: does the image still LOOK the same?)

Tamper modes to demo:
  A) content : `--tamper-image` / `--tamper-text` flip one byte/char of a sidecar
               before verifying, so checks 3/4 FAIL.
  B) ledger  : hand-edit data/ledger.json (or use tools/tamper_ledger.py), so
               check 1 FAILs and the exact broken entry is named.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from src.config import get_ledger_adapter
from src.evidence import (
    BundlePaths,
    load_evidence_record,
    phash_distance,
    phash_file,
    sha256_file,
)
from src.models import sha256_hex

try:
    from rich.console import Console
    from rich.table import Table

    _RICH = True
except Exception:  # rich is Phase-5 polish; degrade gracefully
    _RICH = False


def _row(name: str, ok: bool, expected: str, actual: str) -> tuple[str, str, str, str]:
    return (name, "PASS" if ok else "FAIL", expected, actual)


def verify_bundle(record_json_path: str, *, os_module=None) -> tuple[bool, list[tuple]]:
    """Return (overall_ok, rows). Each row: (check, status, expected, actual)."""
    import os

    rows: list[tuple] = []
    record = load_evidence_record(record_json_path)
    paths = BundlePaths.for_record_json(record_json_path)
    ledger = get_ledger_adapter()

    # 1 + 2: chain integrity and bundle-hash-vs-anchor come from the adapter.
    result = ledger.verify(record)
    chain_row = next((c for c in result.checks if c.name == "ledger_chain_intact"), None)
    if chain_row is not None:
        rows.append(_row("ledger_chain_intact", chain_row.passed, "intact", chain_row.detail))
    rows.append(_row(
        "bundle_hash_vs_anchor",
        result.expected_bundle_hash is not None and result.actual_bundle_hash == result.expected_bundle_hash,
        result.expected_bundle_hash or "(not anchored)",
        result.actual_bundle_hash or "",
    ))

    # 3: image byte-exact integrity (re-hash the actual file).
    if os.path.exists(paths.image):
        actual_img = sha256_file(paths.image)
        rows.append(_row("image_sha256", actual_img == record.image_sha256, record.image_sha256, actual_img))
        # pHash (informational): does it still LOOK the same?
        try:
            actual_ph = phash_file(paths.image)
            dist = phash_distance(record.image_phash, actual_ph)
            rows.append(_row(f"image_phash (dist={dist})", dist <= 8, record.image_phash, actual_ph))
        except Exception:
            rows.append(_row("image_phash", False, record.image_phash, "(image no longer decodes)"))
    else:
        rows.append(_row("image_sha256", False, record.image_sha256, "(image sidecar missing)"))

    # 4: post-text byte-exact integrity.
    if record.post_text_sha256 is not None:
        if os.path.exists(paths.post_text):
            with open(paths.post_text, "r", encoding="utf-8") as fh:
                actual_txt = sha256_hex(fh.read())
            rows.append(_row("post_text_sha256", actual_txt == record.post_text_sha256,
                             record.post_text_sha256, actual_txt))
        else:
            rows.append(_row("post_text_sha256", False, record.post_text_sha256, "(text sidecar missing)"))

    overall = all(r[1] == "PASS" for r in rows)
    return overall, rows


def _print_plain(record_json: str, overall: bool, rows: list[tuple]) -> None:
    print(f"VERIFY {record_json}")
    for name, status, expected, actual in rows:
        print(f"  [{status}] {name}")
        if status == "FAIL":
            print(f"        expected: {expected}")
            print(f"        actual  : {actual}")
    print()
    print("VERDICT:", "VERIFIED - evidence is intact and matches the chain."
          if overall else "TAMPERED - verification FAILED (see mismatches above).")


def _print_rich(record_json: str, overall: bool, rows: list[tuple]) -> None:
    console = Console()
    table = Table(title=f"Verify {record_json}", show_lines=False)
    table.add_column("Check", style="bold")
    table.add_column("Status")
    table.add_column("Expected", overflow="fold")
    table.add_column("Actual", overflow="fold")
    for name, status, expected, actual in rows:
        color = "green" if status == "PASS" else "red"
        exp = expected if status == "FAIL" else "-"
        act = actual if status == "FAIL" else "-"
        table.add_row(name, f"[{color}]{status}[/{color}]", exp, act)
    console.print(table)
    if overall:
        console.print("[bold green]VERDICT: VERIFIED[/bold green] - evidence is intact and matches the chain.")
    else:
        console.print("[bold red]VERDICT: TAMPERED[/bold red] - verification FAILED (mismatched hashes shown above).")


def main(argv: Optional[list[str]] = None) -> int:
    p = argparse.ArgumentParser(prog="python -m src.verify", description="Re-verify an anchored evidence bundle.")
    p.add_argument("--evidence", required=True, help="path to out/evidence_NNN.json")
    p.add_argument("--tamper-image", action="store_true", help="DEMO: flip one byte of the image sidecar first")
    p.add_argument("--tamper-text", action="store_true", help="DEMO: change one character of the post-text sidecar first")
    p.add_argument("--plain", action="store_true", help="plain output (no rich table)")
    args = p.parse_args(argv)

    # Optional in-place tamper of sidecars (demo convenience).
    paths = BundlePaths.for_record_json(args.evidence)
    if args.tamper_image:
        import os
        if os.path.exists(paths.image):
            with open(paths.image, "r+b") as fh:
                fh.seek(-1, os.SEEK_END)
                last = fh.read(1)
                fh.seek(-1, os.SEEK_END)
                fh.write(bytes([last[0] ^ 0x01]))  # flip one bit of the last byte
            print(f"(demo) flipped one byte of {paths.image}\n")
    if args.tamper_text:
        import os
        if os.path.exists(paths.post_text):
            with open(paths.post_text, "r", encoding="utf-8") as fh:
                txt = fh.read()
            with open(paths.post_text, "w", encoding="utf-8") as fh:
                fh.write((txt + "X") if txt else "X")
            print(f"(demo) changed one character of {paths.post_text}\n")

    try:
        overall, rows = verify_bundle(args.evidence)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if _RICH and not args.plain:
        _print_rich(args.evidence, overall, rows)
    else:
        _print_plain(args.evidence, overall, rows)
    return 0 if overall else 7


if __name__ == "__main__":
    raise SystemExit(main())
