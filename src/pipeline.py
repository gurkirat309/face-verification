"""The end-to-end pipeline: FACE -> SEARCH -> CHAIN.

    python -m src.pipeline --image samples/obama_a.jpg

Steps:
  [1/3] FACE   -- detect + embed + salted subject hash (Stage 1)
  [2/3] SEARCH -- two-pass reverse image search + face re-verification (Stage 2)
  [3/3] CHAIN  -- build an evidence bundle for the best match and anchor its
                  hash on the ledger (Stage 3); write the bundle to out/.

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
from src.evidence import build_evidence_record, write_evidence_bundle
from src.face import FaceError, encode_face
from src.search import (
    SearchError,
    SerpApiLensClient,
    YandexClient,
    search_and_verify,
    yandex_search_and_verify,
)
from src.ui import (
    print_anchored_certificate,
    print_banner,
    print_face_card,
    print_matches_table,
    print_no_match_card,
)

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
    max_candidates: int = 50,
    refresh: bool = False,
    salt: Optional[str] = None,
    out_dir: str = "out",
    operator: Optional[str] = None,
    do_verify: bool = False,
    engine: str = "auto",
    image_url: Optional[str] = None,
) -> int:
    """Execute the full pipeline. Returns a process exit code.

    do_verify: after anchoring, immediately re-verify the produced bundle.
    """
    from src.config import _load_dotenv

    _load_dotenv()
    threshold = threshold if threshold is not None else float(os.environ.get("FACE_MATCH_THRESHOLD", "0.5"))
    api_key = os.environ.get("RIS_API_KEY", "")
    operator = operator or current_operator()
    audit = SearchAuditLog()
    ledger = get_ledger_adapter()
    backend = os.environ.get("LEDGER_BACKEND", "local")

    # ----- Header Banner -------------------------------------------------- #
    print_banner(engine=engine, ledger_backend=backend)

    # ----- [1/3] FACE ----------------------------------------------------- #
    try:
        if _con:
            with _con.status("[cyan]Analyzing facial features with ArcFace...", spinner="dots"):
                face = encode_face(image_path, salt=salt)
        else:
            face = encode_face(image_path, salt=salt)
        print_face_card(face)
    except (FileNotFoundError, FaceError) as exc:
        _panel(str(exc), "FACE FAILED", "red")
        return 2

    # ----- [2/3] SEARCH --------------------------------------------------- #
    _rule(f"[2/3] SEARCH  -  engine={engine.upper()}  (reverse image search + face re-verify)")

    def _run_lens():
        return search_and_verify(
            image_path, face.embedding, client=SerpApiLensClient(api_key),
            threshold=threshold, max_candidates=max_candidates, refresh=refresh, salt=salt,
        )

    def _run_yandex():
        from src.hosting import imgbb_upload  # may raise HostingError (subclass of RuntimeError)

        public_url = image_url or imgbb_upload(
            image_path, os.environ.get("IMGBB_API_KEY", ""), refresh=refresh
        )
        _say(f"hosted for Yandex: {public_url}")
        return yandex_search_and_verify(
            public_url, face.embedding, client=YandexClient(api_key),
            threshold=threshold, max_candidates=max_candidates, refresh=refresh, salt=salt,
        )

    used_engine = engine
    try:
        if engine == "yandex":
            outcome = _run_yandex()
        elif engine == "lens":
            outcome = _run_lens()
        else:  # auto: Google Lens first, fall back to Yandex only if no match
            _say("querying Google Lens reverse-image index ...")
            outcome = _run_lens()
            used_engine = "lens"
            if not outcome.matched:
                _say(f"Lens: no match (best {outcome.best_similarity:.1%}) -> trying Yandex fallback ...", style="yellow")
                try:
                    y = _run_yandex()
                    # keep Yandex if it matched, or if it at least looked closer
                    if y.matched or y.best_similarity > outcome.best_similarity:
                        outcome, used_engine = y, "yandex"
                except Exception as exc:  # HostingError / SearchError -> keep Lens result
                    _say(f"Yandex fallback unavailable: {exc}", style="yellow")
    except SearchError as exc:
        _panel(str(exc), "SEARCH FAILED", "red")
        return 6

    _say(f"engine used  : [bold]{used_engine.upper()}[/]")
    _say(f"query source : {'[dim cyan]CACHED (0 searches spent)[/]' if outcome.was_cached else '[bold green]LIVE (1 search spent)[/]'}")
    _say(f"coverage     : {outcome.total_candidates} hits returned, {outcome.checked} face-checked")

    if not outcome.matched:
        audit.log(face.subject_hash, "no_match",
                  f"best_sim={outcome.best_similarity:.3f} < {threshold}", operator=operator)
        print_no_match_card(outcome.best_similarity, threshold)
        return 0

    best = outcome.matches[0]
    print_matches_table(outcome.matches, threshold, outcome.checked)
    audit.log(face.subject_hash, "match_found",
              f"sim={best.similarity:.3f} matches={len(outcome.matches)} src={best.candidate.source}", operator=operator)

    # ----- [3/3] CHAIN ---------------------------------------------------- #
    _rule("[3/3] CHAIN  -  build evidence bundle & anchor to ledger")
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

    print_anchored_certificate(record, receipt, paths.record_json)

    # ----- optional immediate verification -------------------------------- #
    if do_verify:
        _rule("🔍 INTEGRITY VERIFICATION AUDIT", style="magenta")
        from src.verify import _RICH, _print_plain, _print_rich, verify_bundle

        ok, rows = verify_bundle(paths.record_json)
        if _RICH:
            _print_rich(paths.record_json, ok, rows)
        else:
            _print_plain(paths.record_json, ok, rows)
        return 0 if ok else 7

    return 0

    # ----- optional immediate verification -------------------------------- #
    if do_verify:
        _rule("[verify]  re-checking the anchored evidence", style="magenta")
        from src.verify import _RICH, _print_plain, _print_rich, verify_bundle

        ok, rows = verify_bundle(paths.record_json)
        if _RICH:
            _print_rich(paths.record_json, ok, rows)
        else:
            _print_plain(paths.record_json, ok, rows)
        return 0 if ok else 7

    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m src.pipeline", description="Face -> search -> blockchain evidence pipeline.")
    p.add_argument("--image", required=True, help="local image of the subject")
    p.add_argument("--threshold", type=float, default=None, help="match threshold [0..1] (default FACE_MATCH_THRESHOLD)")
    p.add_argument("--max", type=int, default=50, help="max candidates to face-check")
    p.add_argument("--refresh", action="store_true", help="force a LIVE SerpApi search (spends 1)")
    p.add_argument("--salt", default=None, help="hashing salt (default from .env)")
    p.add_argument("--out", default="out", help="output directory for evidence bundles")
    p.add_argument("--operator", default=None, help="who is running this (audit trail)")
    p.add_argument("--verify", action="store_true",
                   help="after anchoring, immediately re-verify the evidence bundle")
    p.add_argument("--engine", choices=["auto", "lens", "yandex"], default="auto",
                   help="auto (default: Google Lens, then Yandex if no match), lens only, or yandex only")
    p.add_argument("--image-url", default=None,
                   help="public image URL for yandex (skip imgbb upload; e.g. a GitHub raw URL)")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    return run_pipeline(
        args.image, threshold=args.threshold, max_candidates=args.max,
        refresh=args.refresh, salt=args.salt, out_dir=args.out, operator=args.operator,
        do_verify=args.verify,
        engine=args.engine, image_url=args.image_url,
    )


if __name__ == "__main__":
    raise SystemExit(main())
