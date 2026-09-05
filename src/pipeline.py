"""The end-to-end pipeline: FACE -> CONSENT -> SEARCH -> CHAIN.

    python -m src.pipeline --image samples/obama_a.jpg

Steps:
  [1/3] FACE   -- detect + embed + salted subject hash (Stage 1)
        GATE   -- refuse unless the subject has registered consent (Stage 2)
  [2/3] SEARCH -- two-pass reverse image search + face re-verification (Stage 3)
  [3/3] CHAIN  -- build an evidence bundle for the best match and anchor its
                  hash on the ledger (Stage 4); write the bundle to out/.

On a clean "no match", the pipeline exits without anchoring (that is a valid,
first-class outcome, logged to the audit trail).

The pipeline talks only to the LedgerAdapter via the factory, so it runs against
the local hash-chained ledger today and the teammate's EVM chain later with no
code change (just LEDGER_BACKEND).

Output uses rich panels when available, with a plain-text fallback, and wraps
external calls so a network blip degrades gracefully rather than killing a demo.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from src.audit import SearchAuditLog, current_operator
from src.config import get_ledger_adapter
from src.consent import ConsentRefused, check_consent_or_refuse
from src.evidence import build_evidence_record, write_evidence_bundle
from src.face import FaceError, encode_face
from src.search import SearchError, SerpApiLensClient, search_and_verify

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.rule import Rule

    _con: Optional["Console"] = Console()
except Exception:
    _con = None


def _rule(title: str, style: str = "cyan") -> None:
    if _con:
        _con.print(Rule(f"[bold {style}]{title}", style=style))
    else:
        print(f"\n=== {title} ===")


def _say(msg: str, style: str = "") -> None:
    if _con:
        _con.print(f"        {msg}", style=style)
    else:
        print(f"        {msg}")


def _panel(body: str, title: str, style: str) -> None:
    if _con:
        _con.print(Panel(body, title=title, border_style=style, expand=False))
    else:
        print(f"\n[{title}]\n{body}\n")


def run_pipeline(
    image_path: str,
    *,
    threshold: Optional[float] = None,
    max_candidates: int = 25,
    refresh: bool = False,
    salt: Optional[str] = None,
    out_dir: str = "out",
    operator: Optional[str] = None,
) -> int:
    """Execute the full pipeline. Returns a process exit code."""
    from src.config import _load_dotenv

    _load_dotenv()
    threshold = threshold if threshold is not None else float(os.environ.get("FACE_MATCH_THRESHOLD", "0.5"))
    api_key = os.environ.get("RIS_API_KEY", "")
    operator = operator or current_operator()
    audit = SearchAuditLog()
    ledger = get_ledger_adapter()
    backend = os.environ.get("LEDGER_BACKEND", "local")

    # ----- [1/3] FACE ----------------------------------------------------- #
    _rule("[1/3] FACE  -  detect, embed, hash")
    try:
        face = encode_face(image_path, salt=salt)
    except (FileNotFoundError, FaceError) as exc:
        _panel(str(exc), "FACE FAILED", "red")
        return 2
    _say(f"subject_hash = {face.subject_hash}")
    _say(f"quality = {face.quality:.3f}   faces detected = {face.num_faces_detected}")

    # ----- GATE (consent) ------------------------------------------------- #
    try:
        check_consent_or_refuse(face.subject_hash, ledger, operator=operator, audit=audit)
    except ConsentRefused as exc:
        _panel(str(exc), "CONSENT REFUSED", "red")
        return 5
    _say("consent registered -> search ALLOWED", style="green")

    # ----- [2/3] SEARCH --------------------------------------------------- #
    _rule("[2/3] SEARCH  -  reverse image search + face re-verify")
    try:
        client = SerpApiLensClient(api_key)
        outcome = search_and_verify(
            image_path, face.embedding, client=client,
            threshold=threshold, max_candidates=max_candidates, refresh=refresh, salt=salt,
        )
    except SearchError as exc:
        _panel(str(exc), "SEARCH FAILED", "red")
        return 6
    _say(f"source = {'CACHED (0 searches spent)' if outcome.was_cached else 'LIVE (1 search spent)'}")
    _say(f"candidates = {outcome.total_candidates} returned, {outcome.checked} face-checked")

    if not outcome.matched:
        audit.log(face.subject_hash, "no_match",
                  f"best_sim={outcome.best_similarity:.3f} < {threshold}", operator=operator)
        _panel(
            f"No matching post found.\nBest similarity seen: {outcome.best_similarity:.3f} "
            f"(below threshold {threshold}).\nNothing anchored -- this is a clean exit.",
            "NO MATCH", "yellow",
        )
        return 0

    best = outcome.matches[0]
    _say(f"VERIFIED MATCH  similarity={best.similarity:.3f}  source={best.candidate.source}", style="green")
    _say(f"{best.candidate.page_link}")
    audit.log(face.subject_hash, "match_found",
              f"sim={best.similarity:.3f} src={best.candidate.source}", operator=operator)

    # ----- [3/3] CHAIN ---------------------------------------------------- #
    _rule("[3/3] CHAIN  -  build evidence + anchor")
    post_text = best.candidate.title or ""
    record = build_evidence_record(
        subject_hash=face.subject_hash,
        source_url=best.candidate.page_link,
        platform=best.candidate.source,
        matched_image_path=best.image_local_path,
        match_confidence=best.similarity,
        match_threshold=threshold,
        post_text=post_text,
        notes="Verified via reverse-image search + pass-2 face re-match.",
    )
    try:
        paths = write_evidence_bundle(record, matched_image_path=best.image_local_path,
                                      post_text=post_text, out_dir=out_dir)
        receipt = ledger.anchor_evidence(record)
    except Exception as exc:
        _panel(f"Anchoring failed: {exc}", "CHAIN FAILED", "red")
        return 8

    body = (
        f"record_id    : {record.record_id}\n"
        f"bundle_hash  : {record.bundle_hash}\n"
        f"image_sha256 : {record.image_sha256}\n"
        f"image_pHash  : {record.image_phash}\n"
        f"confidence   : {record.match_confidence:.3f}  (threshold {record.match_threshold})\n"
        f"anchor       : {receipt.status.value} on '{receipt.backend}'  tx={receipt.tx_ref[:24]}...\n"
        f"evidence     : {paths.record_json}\n\n"
        f"Verify it:  python -m src.verify --evidence {paths.record_json}"
    )
    _panel(body, "EVIDENCE ANCHORED", "green")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.pipeline", description="Face -> search -> blockchain evidence pipeline.")
    p.add_argument("--image", required=True, help="local image of the (consented) subject")
    p.add_argument("--threshold", type=float, default=None, help="match threshold [0..1] (default FACE_MATCH_THRESHOLD)")
    p.add_argument("--max", type=int, default=25, help="max candidates to face-check")
    p.add_argument("--refresh", action="store_true", help="force a LIVE SerpApi search (spends 1)")
    p.add_argument("--salt", default=None, help="hashing salt (default from .env)")
    p.add_argument("--out", default="out", help="output directory for evidence bundles")
    p.add_argument("--operator", default=None, help="who is running this (audit trail)")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    return run_pipeline(
        args.image, threshold=args.threshold, max_candidates=args.max,
        refresh=args.refresh, salt=args.salt, out_dir=args.out, operator=args.operator,
    )


if __name__ == "__main__":
    raise SystemExit(main())
