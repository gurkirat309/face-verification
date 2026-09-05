"""The ``LedgerAdapter`` interface -- the one seam the whole project hinges on.

Everything in the pipeline talks ONLY to this abstract interface. Two concrete
classes implement it:

* ``LocalLedgerAdapter`` (src/ledger/local.py) -- a fully working, append-only,
  hash-chained JSON ledger on disk. This is the reference implementation and the
  submission safety net.
* ``EVMLedgerAdapter``  (src/ledger/evm.py)   -- a stub for the teammate. Same
  methods, same types; they fill in the bodies to talk to a real chain.

Which one loads is decided by the ``LEDGER_BACKEND`` env var via the factory in
src/config.py. Nothing else in the codebase changes when that flips.

REQUIRED BEHAVIOUR (the shared test suite in tests/test_ledger_contract.py
enforces most of this; the rest is documented here because a type signature
cannot express it):

1. Idempotency on duplicate anchoring.
   Anchoring the SAME EvidenceRecord (same bundle_hash) twice MUST NOT create a
   second logical record. The second call returns a Receipt with
   status == DUPLICATE and the SAME record_id. Rationale: a demo re-run, or an
   EVM retry after a dropped connection, must not double-write.

2. Failed / reverted writes.
   If a write cannot be made durable (disk error locally; revert/out-of-gas/
   dropped tx on EVM), the method MUST NOT silently succeed. Either raise
   LedgerError, or return a Receipt with status == FAILED. It must never return
   CONFIRMED for a write that did not land.

3. ``verify`` is a PURE READ.
   It performs no writes, no network mutations, no filesystem writes. It may
   read the ledger. It recomputes the bundle hash from the record it is given
   and compares it against what the ledger anchored. Calling verify any number
   of times changes nothing.

4. ``get_record`` returns None for an unknown id -- it never raises for "not
   found". (It may raise LedgerError for an actual backend failure.)

5. Consent before evidence is a *pipeline* rule, not enforced here. The adapter
   stores consent honestly; the pipeline is responsible for calling
   ``is_consented`` before searching. See src/config.py / the pipeline.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from src.models import EvidenceRecord, Receipt, VerificationResult


class LedgerError(RuntimeError):
    """Raised when a ledger operation fails in a way the caller must handle
    (e.g. corrupt ledger file, broken hash chain on write, reverted tx)."""


class LedgerAdapter(ABC):
    """Abstract ledger. Implement all five methods; change nothing else.

    Teammate: to add the real chain, subclass this in src/ledger/evm.py (already
    stubbed for you) and implement each method so the tests in
    tests/test_ledger_contract.py pass. See HANDOVER.md.
    """

    @abstractmethod
    def register_consent(self, subject_hash: str, consent_flag: bool) -> Receipt:
        """Record that the subject identified by ``subject_hash`` has (or has
        withdrawn) consent to be searched for.

        Args:
            subject_hash: hex SHA-256 (64 chars) of a salted face embedding.
                The one-way subject identifier. Never a raw embedding.
            consent_flag: True grants consent, False withdraws it. The LATEST
                write for a given subject_hash wins (last-write-wins), so this
                doubles as an update.

        Returns:
            Receipt. record_id == subject_hash. status CONFIRMED on success,
            DUPLICATE if the identical (subject_hash, consent_flag) state was
            already the current state.

        Raises:
            LedgerError: if the write could not be made durable.
        """
        raise NotImplementedError

    @abstractmethod
    def is_consented(self, subject_hash: str) -> bool:
        """Return True iff the current consent state for ``subject_hash`` is
        granted. Unknown subject -> False. Pure read; never raises for 'unknown'.
        """
        raise NotImplementedError

    @abstractmethod
    def anchor_evidence(self, record: EvidenceRecord) -> Receipt:
        """Anchor an evidence bundle so it becomes tamper-evident.

        The implementation anchors ``record.bundle_hash`` (plus subject_hash and
        a timestamp). It does NOT need to store every field on-chain; off-chain
        storage of the full record is fine and expected (the pipeline also writes
        the full JSON to out/). If ``record.bundle_hash`` is empty, the adapter
        MUST call ``record.finalize()`` (or compute it) first -- never anchor an
        empty/None hash.

        Idempotent: anchoring a record whose bundle_hash already exists returns
        status DUPLICATE with the existing record_id and writes nothing new.

        Args:
            record: a fully-populated EvidenceRecord (see src/models.py).

        Returns:
            Receipt. record_id == record.record_id. status CONFIRMED or DUPLICATE.

        Raises:
            LedgerError: on a write that could not be made durable / reverted.
        """
        raise NotImplementedError

    @abstractmethod
    def get_record(self, record_id: str) -> EvidenceRecord | None:
        """Return the anchored EvidenceRecord for ``record_id``, or None if no
        such record is known. Pure read. Never raises for 'not found'.

        On EVM this typically combines the on-chain hash with the off-chain
        stored JSON; if either is missing, return None.
        """
        raise NotImplementedError

    @abstractmethod
    def verify(self, record: EvidenceRecord) -> VerificationResult:
        """Re-verify ``record`` against what was anchored. PURE READ.

        Steps every implementation must perform:
          1. Recompute bundle_hash from ``record`` (record.compute_bundle_hash()).
          2. Look up the anchored bundle_hash for record.record_id.
          3. verified = anchor exists AND recomputed == anchored AND (for local)
             the ledger hash-chain is intact.
          4. Populate VerificationResult.checks so a human can see WHICH check
             failed and the two hashes side by side.

        Returns a VerificationResult; does not raise for a normal FAIL (a
        mismatch is a result, not an error). May raise LedgerError only for a
        genuine backend failure (e.g. unreadable ledger).
        """
        raise NotImplementedError
