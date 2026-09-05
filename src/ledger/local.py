"""LocalLedgerAdapter -- a real, working, append-only, hash-chained ledger.

This is NOT a mock. It is a genuine simulated chain: every entry stores the
previous entry's hash, so editing any past entry breaks the chain and is
detectable. The brief explicitly permits a local/simulated chain, which makes
this a valid standalone submission and our safety net if the on-chain adapter
is not ready.

Storage format (a single JSON file, default data/ledger.json):

    {
      "genesis": "<constant>",
      "entries": [
        {
          "index": 0,
          "type": "consent" | "evidence",
          "timestamp_utc": "2026-09-05T12:00:00Z",
          "payload": { ... },          # consent: {subject_hash, consent_flag}
                                        # evidence: EvidenceRecord.to_dict()
          "prev_hash": "<hash of previous entry, or genesis for index 0>",
          "entry_hash": "<sha256 of this entry's canonical content>"
        },
        ...
      ]
    }

The hash chain is what makes tampering detectable:
    entry_hash = sha256(canonical_json({index, type, timestamp_utc, payload, prev_hash}))
Change any payload byte and entry_hash no longer matches -> chain breaks at that
point -> verify() reports the break and the exact index.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone
from typing import Any

from src.ledger.base import LedgerAdapter, LedgerError
from src.models import (
    Check,
    EvidenceRecord,
    Receipt,
    ReceiptStatus,
    VerificationResult,
    canonical_json,
    sha256_hex,
)

GENESIS = "FACECHAIN-GENESIS-v1"
DEFAULT_LEDGER_PATH = os.path.join("data", "ledger.json")


def _utcnow_iso() -> str:
    """Current UTC time as ISO-8601 with a trailing Z, second precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _entry_hash(index: int, etype: str, timestamp_utc: str, payload: dict[str, Any], prev_hash: str) -> str:
    """Deterministic hash of one entry's content (excluding entry_hash itself)."""
    return sha256_hex(
        canonical_json(
            {
                "index": index,
                "type": etype,
                "timestamp_utc": timestamp_utc,
                "payload": payload,
                "prev_hash": prev_hash,
            }
        )
    )


class LocalLedgerAdapter(LedgerAdapter):
    """A file-backed, hash-chained ledger implementing the LedgerAdapter seam."""

    def __init__(self, path: str = DEFAULT_LEDGER_PATH) -> None:
        self.path = path
        self._ensure_dir()

    # ------------------------------------------------------------------ #
    # Storage helpers                                                     #
    # ------------------------------------------------------------------ #
    def _ensure_dir(self) -> None:
        d = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(d, exist_ok=True)

    def _load(self) -> dict[str, Any]:
        if not os.path.exists(self.path):
            return {"genesis": GENESIS, "entries": []}
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                import json

                data = json.load(fh)
        except (OSError, ValueError) as exc:
            raise LedgerError(f"Could not read ledger at {self.path}: {exc}") from exc
        if "entries" not in data:
            raise LedgerError(f"Ledger file {self.path} is missing 'entries'.")
        return data

    def _save(self, data: dict[str, Any]) -> None:
        """Atomic write: temp file then os.replace, so a crash never truncates
        the ledger."""
        import json

        self._ensure_dir()
        dir_ = os.path.dirname(os.path.abspath(self.path))
        fd, tmp = tempfile.mkstemp(dir=dir_, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise LedgerError(f"Could not write ledger at {self.path}: {exc}") from exc

    def _append(self, etype: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Append a new entry, chaining it to the previous one. Returns the entry."""
        data = self._load()
        entries = data["entries"]
        index = len(entries)
        prev_hash = entries[-1]["entry_hash"] if entries else data.get("genesis", GENESIS)
        timestamp = _utcnow_iso()
        eh = _entry_hash(index, etype, timestamp, payload, prev_hash)
        entry = {
            "index": index,
            "type": etype,
            "timestamp_utc": timestamp,
            "payload": payload,
            "prev_hash": prev_hash,
            "entry_hash": eh,
        }
        entries.append(entry)
        self._save(data)
        return entry

    # ------------------------------------------------------------------ #
    # Chain integrity                                                     #
    # ------------------------------------------------------------------ #
    def check_chain(self) -> tuple[bool, str]:
        """Verify the whole hash chain. Returns (intact, detail).

        Detects two kinds of tampering: an edited payload (recomputed entry_hash
        no longer matches the stored one) and a re-linked/re-ordered chain (an
        entry's prev_hash no longer equals the real previous entry_hash).
        """
        data = self._load()
        entries = data["entries"]
        genesis = data.get("genesis", GENESIS)
        prev = genesis
        for i, e in enumerate(entries):
            if e.get("index") != i:
                return False, f"Entry {i} has wrong index {e.get('index')}."
            if e.get("prev_hash") != prev:
                return False, f"Chain broken at entry {i}: prev_hash does not match previous entry."
            recomputed = _entry_hash(i, e["type"], e["timestamp_utc"], e["payload"], e["prev_hash"])
            if recomputed != e.get("entry_hash"):
                return False, f"Entry {i} was tampered: content no longer matches its stored hash."
            prev = e["entry_hash"]
        return True, "Chain intact."

    # ------------------------------------------------------------------ #
    # LedgerAdapter interface                                             #
    # ------------------------------------------------------------------ #
    def register_consent(self, subject_hash: str, consent_flag: bool) -> Receipt:
        if self.is_consented(subject_hash) == consent_flag:
            # Current state already matches -> idempotent no-op.
            return Receipt(
                status=ReceiptStatus.DUPLICATE,
                record_id=subject_hash,
                tx_ref="",
                backend="local",
                timestamp_utc=_utcnow_iso(),
                detail=f"Consent already {'granted' if consent_flag else 'withdrawn'} for this subject.",
            )
        entry = self._append("consent", {"subject_hash": subject_hash, "consent_flag": bool(consent_flag)})
        return Receipt(
            status=ReceiptStatus.CONFIRMED,
            record_id=subject_hash,
            tx_ref=entry["entry_hash"],
            backend="local",
            timestamp_utc=entry["timestamp_utc"],
            detail=f"Consent {'granted' if consent_flag else 'withdrawn'} and anchored.",
            block_ref=str(entry["index"]),
            entry_hash=entry["entry_hash"],
            prev_hash=entry["prev_hash"],
        )

    def is_consented(self, subject_hash: str) -> bool:
        data = self._load()
        state = False
        for e in data["entries"]:  # last-write-wins
            if e["type"] == "consent" and e["payload"].get("subject_hash") == subject_hash:
                state = bool(e["payload"].get("consent_flag", False))
        return state

    def anchor_evidence(self, record: EvidenceRecord) -> Receipt:
        if not record.bundle_hash:
            record.finalize()
        # Idempotency: same bundle_hash already anchored?
        data = self._load()
        for e in data["entries"]:
            if e["type"] == "evidence" and e["payload"].get("bundle_hash") == record.bundle_hash:
                return Receipt(
                    status=ReceiptStatus.DUPLICATE,
                    record_id=e["payload"].get("record_id", record.record_id),
                    tx_ref=e["entry_hash"],
                    backend="local",
                    timestamp_utc=e["timestamp_utc"],
                    detail="Identical evidence already anchored; no new entry written.",
                    block_ref=str(e["index"]),
                    entry_hash=e["entry_hash"],
                    prev_hash=e["prev_hash"],
                )
        entry = self._append("evidence", record.to_dict())
        return Receipt(
            status=ReceiptStatus.CONFIRMED,
            record_id=record.record_id,
            tx_ref=entry["entry_hash"],
            backend="local",
            timestamp_utc=entry["timestamp_utc"],
            detail="Evidence anchored on the local hash-chained ledger.",
            block_ref=str(entry["index"]),
            entry_hash=entry["entry_hash"],
            prev_hash=entry["prev_hash"],
        )

    def get_record(self, record_id: str) -> EvidenceRecord | None:
        data = self._load()
        found = None
        for e in data["entries"]:  # latest wins if somehow duplicated
            if e["type"] == "evidence" and e["payload"].get("record_id") == record_id:
                found = e["payload"]
        if found is None:
            return None
        return EvidenceRecord.from_dict(found)

    def verify(self, record: EvidenceRecord) -> VerificationResult:
        checks: list[Check] = []

        # 1. Chain integrity (local-specific, but a first-class part of trust).
        chain_ok, chain_detail = self.check_chain()
        checks.append(
            Check(
                name="ledger_chain_intact",
                passed=chain_ok,
                detail=chain_detail,
            )
        )

        # 2. Recompute the bundle hash from the (possibly tampered) record.
        recomputed = record.compute_bundle_hash()

        # 3. Find what was anchored for this record_id.
        anchored_entry = None
        data = self._load()
        for e in data["entries"]:
            if e["type"] == "evidence" and e["payload"].get("record_id") == record.record_id:
                anchored_entry = e
        anchored_hash = anchored_entry["payload"].get("bundle_hash") if anchored_entry else None

        exists = anchored_entry is not None
        checks.append(
            Check(
                name="record_found_on_ledger",
                passed=exists,
                expected=record.record_id,
                actual=(record.record_id if exists else None),
                detail="Record id present on ledger." if exists else "No such record_id anchored.",
            )
        )

        hash_matches = exists and (recomputed == anchored_hash)
        checks.append(
            Check(
                name="bundle_hash_matches_anchor",
                passed=hash_matches,
                expected=anchored_hash,
                actual=recomputed,
                detail=(
                    "Recomputed bundle hash matches the anchored value."
                    if hash_matches
                    else "MISMATCH: the evidence has been altered since it was anchored."
                ),
            )
        )

        verified = chain_ok and exists and hash_matches
        if verified:
            summary = "VERIFIED: evidence is intact and matches the on-ledger anchor."
        elif not chain_ok:
            summary = f"FAILED: ledger tampering detected -- {chain_detail}"
        elif not exists:
            summary = "FAILED: this record was never anchored on the ledger."
        else:
            summary = "FAILED: evidence hash does not match the anchor -- content was tampered."

        return VerificationResult(
            verified=verified,
            record_id=record.record_id,
            checks=checks,
            expected_bundle_hash=anchored_hash,
            actual_bundle_hash=recomputed,
            chain_intact=chain_ok,
            summary=summary,
        )
