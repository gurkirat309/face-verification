"""Stage 2 -- Consent registry + the gate that enforces it.

The registry itself lives behind the LedgerAdapter (register_consent /
is_consented), so it "moves on-chain for free" when the EVM adapter drops in.
This module adds:

  * the GATE: check_consent_or_refuse() -- the pipeline calls this BEFORE any
    search; it refuses unregistered/withdrawn subjects with a clear message and
    logs every attempt to the audit trail.
  * a CLI to manage consent from a face image:
        python -m src.consent register samples/obama_a.jpg
        python -m src.consent status   samples/obama_a.jpg
        python -m src.consent withdraw samples/obama_a.jpg

Consent is keyed by subject_hash (the salted, one-way face hash from Stage 1).
Registering consent with an image computes that image's subject_hash and records
a granted flag on the ledger. The pipeline later enrolls the SAME reference image
so the hashes line up (see the identity-vs-image note in src/face.py).
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional

from src.audit import SearchAuditLog, current_operator
from src.config import get_ledger_adapter
from src.face import encode_face
from src.ledger.base import LedgerAdapter


class ConsentRefused(RuntimeError):
    """Raised by the gate when a subject has not consented to being searched."""


def check_consent_or_refuse(
    subject_hash: str,
    ledger: LedgerAdapter,
    *,
    operator: Optional[str] = None,
    audit: Optional[SearchAuditLog] = None,
) -> None:
    """The consent gate. Call this before running any search.

    Logs the attempt, then either returns (allowed) or raises ConsentRefused.
    Every outcome is written to the audit trail, so a refusal is still recorded.
    """
    audit = audit or SearchAuditLog()
    op = operator or current_operator()
    audit.log(subject_hash, "search_attempt", "Consent gate check", operator=op)

    if ledger.is_consented(subject_hash):
        audit.log(subject_hash, "search_allowed", "Subject has granted consent", operator=op)
        return

    audit.log(subject_hash, "search_refused", "Subject not registered / consent withdrawn", operator=op)
    raise ConsentRefused(
        "REFUSED: this subject has not consented to being searched.\n"
        "        This tool only searches for people who have registered consent\n"
        "        for their OWN likeness. Register first:\n"
        "            python -m src.consent register <image_of_the_subject>"
    )


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def _subject_hash_for_image(image: str, salt: Optional[str]) -> str:
    # strict_single: consent must be unambiguous about whose face it is.
    res = encode_face(image, salt=salt, strict_single=True)
    return res.subject_hash


def _cmd_register(args) -> int:
    ledger = get_ledger_adapter()
    sh = _subject_hash_for_image(args.image, args.salt)
    receipt = ledger.register_consent(sh, True)
    print("CONSENT REGISTERED")
    print(f"  subject hash : {sh}")
    print(f"  status       : {receipt.status.value}")
    print(f"  backend      : {receipt.backend}")
    if receipt.tx_ref:
        print(f"  tx ref       : {receipt.tx_ref}")
    print(f"  detail       : {receipt.detail}")
    return 0


def _cmd_withdraw(args) -> int:
    ledger = get_ledger_adapter()
    sh = _subject_hash_for_image(args.image, args.salt)
    receipt = ledger.register_consent(sh, False)
    print("CONSENT WITHDRAWN")
    print(f"  subject hash : {sh}")
    print(f"  status       : {receipt.status.value}")
    print(f"  detail       : {receipt.detail}")
    return 0


def _cmd_status(args) -> int:
    ledger = get_ledger_adapter()
    sh = _subject_hash_for_image(args.image, args.salt)
    consented = ledger.is_consented(sh)
    print("CONSENT STATUS")
    print(f"  subject hash : {sh}")
    print(f"  consented    : {consented}")
    print(f"  -> searches are {'ALLOWED' if consented else 'REFUSED'} for this subject.")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.consent",
        description="Manage consent for a subject identified by their face image.",
    )
    p.add_argument("--salt", default=None, help="hashing salt (default: SUBJECT_HASH_SALT from .env)")
    sub = p.add_subparsers(dest="command", required=True)
    for name, fn, help_ in (
        ("register", _cmd_register, "grant consent for the subject in an image"),
        ("withdraw", _cmd_withdraw, "withdraw consent for the subject in an image"),
        ("status", _cmd_status, "show whether the subject in an image has consented"),
    ):
        sp = sub.add_parser(name, help=help_)
        sp.add_argument("image", help="path to an image of the subject")
        sp.set_defaults(func=fn)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    from src.config import _load_dotenv

    _load_dotenv()
    args = _build_parser().parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # face errors, ledger errors -> clear message + nonzero
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1



if __name__ == "__main__":
    raise SystemExit(main())
