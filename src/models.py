"""Shared data structures for the Face -> Search -> Blockchain pipeline.

These models are the *contract* between the pipeline and any ledger backend
(local simulated chain today, real EVM chain later). They are deliberately
plain ``dataclasses`` with explicit, deterministic JSON serialisation so that:

1. The teammate building the on-chain adapter can read this file alone and know
   exactly what fields exist, their types, units, and whether they are required.
2. Hashing is reproducible. The bundle hash and the ledger hash-chain both
   depend on *canonical* JSON (sorted keys, no insignificant whitespace, UTF-8),
   so the same logical record always hashes to the same value on any machine.

BIOMETRIC RULE (non-negotiable, read before touching the chain)
---------------------------------------------------------------
The ONLY biometric-derived value that ever leaves this machine or touches a
ledger is ``subject_hash`` -- a salted SHA-256 of a face embedding. It is a
one-way hash and cannot be inverted back to a face. Raw face embeddings and raw
images NEVER appear in an EvidenceRecord, a Receipt, or on any chain. If you are
the on-chain adapter author: you will only ever receive hashes. Do not add a
field that carries an embedding.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional

SCHEMA_VERSION = "1.0"


# --------------------------------------------------------------------------- #
# Canonical JSON helpers -- the single source of truth for how we serialise    #
# anything that will be hashed. Both adapters MUST use these so that a record   #
# hashed locally and a record hashed on-chain agree byte-for-byte.             #
# --------------------------------------------------------------------------- #
def canonical_json(obj: Any) -> str:
    """Serialise ``obj`` to a deterministic JSON string.

    Deterministic == sorted keys, compact separators, non-ASCII preserved.
    Two logically-equal dicts always produce the identical string, so their
    SHA-256 digests match. This is the backbone of the hash chain.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_hex(data: bytes | str) -> str:
    """Return the lowercase hex SHA-256 digest of ``data``."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- #
# Enums                                                                        #
# --------------------------------------------------------------------------- #
class LedgerBackend(str, Enum):
    """Which ledger implementation is active. Chosen by ``LEDGER_BACKEND`` env."""

    LOCAL = "local"
    EVM = "evm"


class ReceiptStatus(str, Enum):
    """Outcome of a write operation (register_consent / anchor_evidence)."""

    CONFIRMED = "confirmed"  # write succeeded and is now durable on the ledger
    DUPLICATE = "duplicate"  # identical write already existed; treated as success (idempotent)
    FAILED = "failed"        # write did not take effect


# --------------------------------------------------------------------------- #
# Receipt -- returned by every write operation                                 #
# --------------------------------------------------------------------------- #
@dataclass
class Receipt:
    """Proof/outcome of a ledger write.

    A Receipt is what ``register_consent`` and ``anchor_evidence`` hand back.
    It has to make sense for BOTH the local chain and a real EVM chain, so the
    fields are generic. The on-chain adapter maps its own concepts onto these:

    Field         | Type        | Req | Meaning / units
    --------------|-------------|-----|-------------------------------------------
    status        | ReceiptStatus| yes| confirmed / duplicate / failed
    record_id     | str          | yes| id of the affected record. For consent this
                  |              |    | is the subject_hash; for evidence the
                  |              |    | EvidenceRecord.record_id.
    tx_ref        | str          | yes| Opaque transaction reference. LOCAL: the
                  |              |    | entry_hash. EVM: the transaction hash (0x...).
    backend       | str          | yes| "local" or "evm" -- who produced this.
    block_ref     | str | None   | no | LOCAL: entry index as string. EVM: block
                  |              |    | number as string. None if not applicable.
    entry_hash    | str | None   | no | LOCAL: SHA-256 of this ledger entry (used by
                  |              |    | the hash chain). EVM: may repeat tx_ref or be
                  |              |    | None -- there is no local hash chain on EVM.
    prev_hash     | str | None   | no | LOCAL: entry_hash of the previous entry, or
                  |              |    | the genesis constant for the first entry.
                  |              |    | EVM: None.
    timestamp_utc | str          | yes| ISO-8601 UTC time the write was recorded.
    detail        | str          | yes| Human-readable message for logs/UX.
    """

    status: ReceiptStatus
    record_id: str
    tx_ref: str
    backend: str
    timestamp_utc: str
    detail: str = ""
    block_ref: Optional[str] = None
    entry_hash: Optional[str] = None
    prev_hash: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Receipt":
        d = dict(d)
        d["status"] = ReceiptStatus(d["status"])
        return cls(**d)


# --------------------------------------------------------------------------- #
# EvidenceRecord -- the off-chain evidence bundle                              #
# --------------------------------------------------------------------------- #
@dataclass
class EvidenceRecord:
    """A tamper-evident bundle describing one confirmed match of a subject's
    likeness found on the web.

    WHAT GOES ON CHAIN vs WHAT STAYS OFF CHAIN
    ------------------------------------------
    The full record (this whole object, as JSON) is written to disk under
    ``out/`` and is the "evidence file". The chain does NOT need the whole thing.
    The single value that must be anchored is ``bundle_hash`` (a SHA-256 over the
    canonical JSON of every field below except the ledger-bookkeeping ones). That
    hash, plus ``subject_hash`` and a timestamp, is all a smart contract needs to
    store (each is a bytes32). Re-verifying later means: recompute bundle_hash
    from the off-chain JSON and check it equals the on-chain value.

    This split is also our answer to the immutability-vs-erasure tension
    (crypto-shredding): sensitive detail lives off-chain and can be destroyed;
    the chain keeps only an opaque hash pointer.

    FIELD SPEC (the teammate designs contract storage from this table)
    -----------------------------------------------------------------
    Field            | Type       | Req | Units / notes
    -----------------|------------|-----|--------------------------------------------
    record_id        | str        | yes | uuid4 hex. Primary key. <= 64 chars.
    schema_version   | str        | yes | e.g. "1.0". Lets the contract evolve.
    subject_hash     | str        | yes | hex SHA-256 (64 chars) of salted face
                     |            |     | embedding. THE ONLY biometric-derived
                     |            |     | field. One-way. MUST be hashed (already is).
                     |            |     | On chain -> bytes32.
    source_url       | str        | yes | URL where the matching image/post was found.
    platform         | str        | yes | e.g. "twitter","instagram","web","unknown".
    retrieved_at_utc | str        | yes | ISO-8601 UTC when the candidate was fetched.
    image_sha256     | str        | yes | hex SHA-256 (64 chars) of the exact matched
                     |            |     | image bytes. Byte-exact integrity check.
    image_phash      | str        | yes | perceptual hash (hex, imagehash phash,
                     |            |     | typically 16 hex chars). Survives crop /
                     |            |     | recompression -> same image elsewhere still
                     |            |     | matches. Distance-comparable, not equality.
    match_confidence | float      | yes | Pass-2 face similarity in [0.0, 1.0].
                     |            |     | Higher = more similar (cosine similarity).
    match_threshold  | float      | yes | The [0,1] cosine-similarity threshold the
                     |            |     | match had to beat to be accepted.
    post_text_sha256 | str | None | no  | hex SHA-256 of associated post text, if any.
    notes            | str | None | no  | Freeform. <= 2000 chars. No PII beyond URL.
    bundle_hash      | str        | yes | hex SHA-256 over canonical JSON of all the
                     |            |     | fields above (computed; NOT hand-set). This
                     |            |     | is THE value anchored on chain -> bytes32.

    NOTE ON SIZE: keep the record small. Do not store raw image bytes here; store
    the hashes. The raw matched image is cached separately under data/ (gitignored)
    for the demo re-hash step.
    """

    record_id: str
    subject_hash: str
    source_url: str
    platform: str
    retrieved_at_utc: str
    image_sha256: str
    image_phash: str
    match_confidence: float
    match_threshold: float
    post_text_sha256: Optional[str] = None
    notes: Optional[str] = None
    schema_version: str = SCHEMA_VERSION
    bundle_hash: str = ""  # filled by compute_bundle_hash(); part of the stored record

    # Fields that are the *content* of the bundle, i.e. what bundle_hash covers.
    # bundle_hash itself is excluded (a hash cannot cover itself).
    _CONTENT_FIELDS = (
        "record_id",
        "schema_version",
        "subject_hash",
        "source_url",
        "platform",
        "retrieved_at_utc",
        "image_sha256",
        "image_phash",
        "match_confidence",
        "match_threshold",
        "post_text_sha256",
        "notes",
    )

    def content_dict(self) -> dict[str, Any]:
        """The subset of fields that ``bundle_hash`` is computed over."""
        return {k: getattr(self, k) for k in self._CONTENT_FIELDS}

    def compute_bundle_hash(self) -> str:
        """Compute (do not store) the SHA-256 over the canonical content JSON.

        This is a *pure* function of the content fields -- call it any time to
        check integrity. ``verify`` uses it to detect tampering: if any content
        field changed, this value changes and no longer matches the anchor.
        """
        return sha256_hex(canonical_json(self.content_dict()))

    def finalize(self) -> "EvidenceRecord":
        """Populate ``bundle_hash`` from the current content. Returns self."""
        self.bundle_hash = self.compute_bundle_hash()
        return self

    def to_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in self._CONTENT_FIELDS}
        d["bundle_hash"] = self.bundle_hash
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EvidenceRecord":
        known = set(cls._CONTENT_FIELDS) | {"bundle_hash"}
        filtered = {k: v for k, v in d.items() if k in known}
        return cls(**filtered)


# --------------------------------------------------------------------------- #
# Verification result                                                          #
# --------------------------------------------------------------------------- #
@dataclass
class Check:
    """One individual pass/fail step inside a verification.

    Keeping checks itemised is what lets the demo print, side by side, exactly
    which hash diverged -- far more convincing on camera than a single boolean.
    """

    name: str            # short id, e.g. "bundle_hash_matches_anchor"
    passed: bool
    expected: Optional[str] = None  # e.g. the on-chain / anchored value
    actual: Optional[str] = None    # e.g. the freshly recomputed value
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationResult:
    """Outcome of ``verify(record)``.

    ``verified`` is the single headline boolean. ``checks`` explains it.
    """

    verified: bool
    record_id: str
    checks: list[Check] = field(default_factory=list)
    expected_bundle_hash: Optional[str] = None  # anchored value read from ledger
    actual_bundle_hash: Optional[str] = None    # recomputed from the given record
    chain_intact: bool = True                   # did the ledger hash-chain verify?
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["checks"] = [c.to_dict() for c in self.checks]
        return d
