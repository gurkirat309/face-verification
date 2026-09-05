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

    # ----- [1/3] FACE ----------------------------------------------------- #
    print("[1/3] FACE   : detecting and encoding face ...")
    try:
        face = encode_face(image_path, salt=salt)
    except (FileNotFoundError, FaceError) as exc:
        print(f"        FAILED: {exc}", file=sys.stderr)
        return 2
    print(f"        subject_hash = {face.subject_hash}")
    print(f"        quality = {face.quality:.3f}  faces = {face.num_faces_detected}")

    # ----- GATE (consent) ------------------------------------------------- #
    try:
        check_consent_or_refuse(face.subject_hash, ledger, operator=operator, audit=audit)
    except ConsentRefused as exc:
        print("        " + str(exc).replace("\n", "\n        "), file=sys.stderr)
        return 5
    print("        consent OK -> search allowed")

    # ----- [2/3] SEARCH --------------------------------------------------- #
    print(f"[2/3] SEARCH : two-pass web search ({'LIVE' if refresh else 'cache if available'}) ...")
    try:
        client = SerpApiLensClient(api_key)
        outcome = search_and_verify(
            image_path, face.embedding, client=client,
            threshold=threshold, max_candidates=max_candidates, refresh=refresh, salt=salt,
        )
    except SearchError as exc:
        print(f"        FAILED: {exc}", file=sys.stderr)
        return 6
    src_label = "CACHED (0 searches)" if outcome.was_cached else "LIVE (1 search spent)"
    print(f"        source = {src_label}")
    print(f"        candidates = {outcome.total_candidates} returned, {outcome.checked} face-checked")

    if not outcome.matched:
        audit.log(face.subject_hash, "no_match",
                  f"best_sim={outcome.best_similarity:.3f} < {threshold}", operator=operator)
        print(f"        NO MATCH (best similarity {outcome.best_similarity:.3f} < {threshold})")
        print("\nRESULT: no matching post found. Nothing anchored. (clean exit)")
        return 0

    best = outcome.matches[0]
    print(f"        VERIFIED MATCH: {best.similarity:.3f}  {best.candidate.source}")
    print(f"        {best.candidate.page_link}")
    audit.log(face.subject_hash, "match_found",
              f"sim={best.similarity:.3f} src={best.candidate.source}", operator=operator)

    # ----- [3/3] CHAIN ---------------------------------------------------- #
    print("[3/3] CHAIN  : building evidence bundle and anchoring ...")
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
    paths = write_evidence_bundle(record, matched_image_path=best.image_local_path,
                                  post_text=post_text, out_dir=out_dir)
    receipt = ledger.anchor_evidence(record)

    print(f"        record_id    = {record.record_id}")
    print(f"        bundle_hash  = {record.bundle_hash}")
    print(f"        image_sha256 = {record.image_sha256}")
    print(f"        image_pHash  = {record.image_phash}")
    print(f"        anchor       = {receipt.status.value} on '{receipt.backend}'  tx={receipt.tx_ref[:16]}...")
    print(f"        evidence     = {paths.record_json}")
    print("\nRESULT: evidence anchored. Verify it with:")
    print(f"    python -m src.verify --evidence {paths.record_json}")
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
