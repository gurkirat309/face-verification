"""Adapter-agnostic contract test suite for LedgerAdapter.

This is the single source of truth for "does a ledger backend behave correctly?"
It is written against the ABSTRACT interface, so it runs unchanged against:

  * LocalLedgerAdapter  (runs by default here)
  * EVMLedgerAdapter    (the teammate points these same tests at their adapter)

TEAMMATE: your success criterion is "these tests pass against my adapter."
To run them against yours, set the env var RUN_EVM_TESTS=1 and make sure your
adapter is constructible from src.config.get_ledger_adapter() with
LEDGER_BACKEND=evm. Then:  RUN_EVM_TESTS=1 LEDGER_BACKEND=evm pytest -q
See HANDOVER.md for the full walkthrough and pass criteria.

The behaviours pinned here (also documented in src/ledger/base.py):
  * consent round-trip + last-write-wins update
  * unknown subject -> not consented
  * anchor -> get_record round-trip
  * idempotency: anchoring the same bundle twice does not double-write
  * verify() passes for an intact record
  * verify() FAILS (as a result, not an exception) when the record is tampered
  * verify() is a pure read (repeatable, no side effects)
"""

from __future__ import annotations

import os
import uuid

import pytest

from src.ledger.base import LedgerAdapter
from src.models import EvidenceRecord, ReceiptStatus, sha256_hex


# --------------------------------------------------------------------------- #
# Fixtures: build an adapter under test. Add more implementations here.        #
# --------------------------------------------------------------------------- #
def _make_local(tmp_path) -> LedgerAdapter:
    from src.ledger.local import LocalLedgerAdapter

    return LocalLedgerAdapter(path=str(tmp_path / "ledger.json"))


def _make_evm(tmp_path) -> LedgerAdapter:
    # Only used when RUN_EVM_TESTS=1. The teammate's real adapter.
    os.environ["LEDGER_BACKEND"] = "evm"
    from tools.deploy_contract import main as deploy_main

    try:
        deploy_main()
    except Exception:
        pass

    from src.config import get_ledger_adapter

    return get_ledger_adapter()



from src.config import _load_dotenv

_load_dotenv()

_PARAMS = ["local"]
if os.environ.get("RUN_EVM_TESTS") == "1":
    _PARAMS.append("evm")



@pytest.fixture(params=_PARAMS)
def adapter(request, tmp_path) -> LedgerAdapter:
    if request.param == "local":
        return _make_local(tmp_path)
    return _make_evm(tmp_path)


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #
def _subject_hash(seed: str = "alice") -> str:
    return sha256_hex(f"embedding-of-{seed}" + "SALT")


def _sample_record(subject: str | None = None, url: str = "https://example.com/post/1") -> EvidenceRecord:
    rec = EvidenceRecord(
        record_id=uuid.uuid4().hex,
        subject_hash=subject or _subject_hash(),
        source_url=url,
        platform="example",
        retrieved_at_utc="2026-09-05T10:00:00Z",
        image_sha256=sha256_hex(b"the-matched-image-bytes"),
        image_phash="ff0011223344556677"[:16],  # 16 hex chars
        match_confidence=0.91,
        match_threshold=0.65,
        post_text_sha256=sha256_hex("hello world"),
        notes="found via reverse image search pass-2 re-match",
    )
    return rec.finalize()


# --------------------------------------------------------------------------- #
# Consent                                                                      #
# --------------------------------------------------------------------------- #
def test_unknown_subject_not_consented(adapter: LedgerAdapter):
    assert adapter.is_consented(_subject_hash("nobody")) is False


def test_consent_round_trip(adapter: LedgerAdapter):
    sh = _subject_hash("alice")
    r = adapter.register_consent(sh, True)
    assert r.status is ReceiptStatus.CONFIRMED
    assert r.record_id == sh
    assert adapter.is_consented(sh) is True


def test_consent_idempotent(adapter: LedgerAdapter):
    sh = _subject_hash("bob")
    adapter.register_consent(sh, True)
    again = adapter.register_consent(sh, True)
    assert again.status is ReceiptStatus.DUPLICATE
    assert adapter.is_consented(sh) is True


def test_consent_withdrawal_last_write_wins(adapter: LedgerAdapter):
    sh = _subject_hash("carol")
    adapter.register_consent(sh, True)
    assert adapter.is_consented(sh) is True
    adapter.register_consent(sh, False)
    assert adapter.is_consented(sh) is False


# --------------------------------------------------------------------------- #
# Anchoring + retrieval                                                        #
# --------------------------------------------------------------------------- #
def test_anchor_and_get_round_trip(adapter: LedgerAdapter):
    rec = _sample_record()
    receipt = adapter.anchor_evidence(rec)
    assert receipt.status is ReceiptStatus.CONFIRMED
    assert receipt.record_id == rec.record_id

    got = adapter.get_record(rec.record_id)
    assert got is not None
    assert got.record_id == rec.record_id
    assert got.bundle_hash == rec.bundle_hash
    assert got.compute_bundle_hash() == rec.bundle_hash


def test_get_unknown_record_returns_none(adapter: LedgerAdapter):
    assert adapter.get_record("does-not-exist") is None


def test_anchor_idempotent_same_bundle(adapter: LedgerAdapter):
    rec = _sample_record()
    first = adapter.anchor_evidence(rec)
    second = adapter.anchor_evidence(rec)  # identical bundle_hash
    assert first.status is ReceiptStatus.CONFIRMED
    assert second.status is ReceiptStatus.DUPLICATE
    assert second.record_id == first.record_id


def test_bundle_hash_autofilled_if_empty(adapter: LedgerAdapter):
    rec = _sample_record()
    rec.bundle_hash = ""  # simulate caller forgetting to finalize
    receipt = adapter.anchor_evidence(rec)
    assert receipt.status is ReceiptStatus.CONFIRMED
    assert rec.bundle_hash  # adapter must have populated it


# --------------------------------------------------------------------------- #
# Verification                                                                 #
# --------------------------------------------------------------------------- #
def test_verify_intact_record_passes(adapter: LedgerAdapter):
    rec = _sample_record()
    adapter.anchor_evidence(rec)
    result = adapter.verify(rec)
    assert result.verified is True
    assert result.actual_bundle_hash == result.expected_bundle_hash


def test_verify_tampered_record_fails(adapter: LedgerAdapter):
    rec = _sample_record()
    adapter.anchor_evidence(rec)

    # Tamper: change one content field AFTER anchoring.
    rec.source_url = "https://evil.example.com/swapped"
    result = adapter.verify(rec)

    assert result.verified is False
    assert result.actual_bundle_hash != result.expected_bundle_hash
    # A tamper is a RESULT, not an exception.


def test_verify_unanchored_record_fails(adapter: LedgerAdapter):
    rec = _sample_record()  # never anchored
    result = adapter.verify(rec)
    assert result.verified is False


def test_verify_is_pure_read(adapter: LedgerAdapter):
    rec = _sample_record()
    adapter.anchor_evidence(rec)
    r1 = adapter.verify(rec)
    r2 = adapter.verify(rec)
    r3 = adapter.verify(rec)
    assert r1.verified is r2.verified is r3.verified is True
    # get_record still returns the same thing -> verify wrote nothing.
    assert adapter.get_record(rec.record_id).bundle_hash == rec.bundle_hash
