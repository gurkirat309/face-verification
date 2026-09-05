# HANDOVER — implementing the on-chain ledger (`EVMLedgerAdapter`)

**Audience:** you know Web3 (Solidity, wallets, RPC, gas). You do **not** know
this codebase. This doc gives you everything to plug a real chain in without
reading the rest of the code.

**The one-sentence job:** implement the five methods of one class so that the
existing tests pass against it; change nothing else.

---

## 1. What's already done vs. what's yours

**Done (don't touch):**
- The whole pipeline: face detection, consent gate, two-pass web search,
  evidence-bundle construction, verification, CLIs, 41 passing tests.
- The **seam**: an abstract `LedgerAdapter` (`src/ledger/base.py`) that the whole
  codebase talks to. A working `LocalLedgerAdapter` (`src/ledger/local.py`) is
  the reference implementation and current default.
- Shared data models (`src/models.py`) with deterministic hashing.
- A factory (`src/config.py`) that picks the adapter from `LEDGER_BACKEND`.

**Yours (edit exactly one file):**
- `src/ledger/evm.py` — the `EVMLedgerAdapter` class. It currently raises
  `NotImplementedError` in every method. Fill in the bodies.
- Plus: your Solidity contract, deployment, and whatever env vars you need
  (read them in the factory — see §6). Those live in your own files/repo area.

**Do not edit:** `base.py`, `local.py`, `models.py`, `config.py`'s public shape,
the pipeline, or the tests. If you think you must, ping first — it usually means
the seam needs a change we should both agree on.

---

## 2. The `LedgerAdapter` contract (prose + signatures)

Import types from `src.models`. All five methods must be implemented.

```python
register_consent(subject_hash: str, consent_flag: bool) -> Receipt
is_consented(subject_hash: str) -> bool
anchor_evidence(record: EvidenceRecord) -> Receipt
get_record(record_id: str) -> EvidenceRecord | None
verify(record: EvidenceRecord) -> VerificationResult
```

**`register_consent(subject_hash, consent_flag)`**
Record consent state for a subject. `subject_hash` is a 64-char hex string
(→ `bytes32`). `consent_flag` True grants, False withdraws; **last write wins**
(so this is also the update path). Return a `Receipt` with `record_id ==
subject_hash`, `tx_ref` = your tx hash, `block_ref` = block number,
`status = CONFIRMED` when mined. If the same state already holds, you *may*
return `status = DUPLICATE` and skip the tx. On revert/failure: raise
`LedgerError` **or** return `status = FAILED` — never `CONFIRMED`.

**`is_consented(subject_hash) -> bool`**
Pure `view`/`call` read of current consent. Unknown subject → `False`. Never
raise for "unknown". This is called by the pipeline's consent gate **before every
search**, so keep it cheap (a mapping lookup).

**`anchor_evidence(record) -> Receipt`**
Anchor the evidence. If `record.bundle_hash` is empty, call `record.finalize()`
first (never anchor an empty hash). On-chain you only need to store:
`bundle_hash` (bytes32), `subject_hash` (bytes32), `record_id`, and a timestamp
(block time is fine). **Idempotency is required:** anchoring a `bundle_hash` that
already exists must NOT send a second tx — return `status = DUPLICATE` with the
existing `record_id`. Return `tx_ref` = tx hash, `block_ref` = block number,
`status = CONFIRMED`. Raise `LedgerError` / return `FAILED` on revert.

**`get_record(record_id) -> EvidenceRecord | None`**
Return the full record, or `None` if unknown (never raise for "not found").
The chain only holds hashes, so combine the on-chain anchor with the off-chain
JSON the pipeline wrote to `out/evidence_*.json`. If the on-chain anchor exists
but you can't find the off-chain JSON (or vice-versa), return `None`.

**`verify(record) -> VerificationResult`**
**Pure read — no writes, no network mutations, idempotent.** Steps:
1. `recomputed = record.compute_bundle_hash()`.
2. Read the anchored `bundle_hash` for `record.record_id` from chain.
3. `verified = anchor exists AND recomputed == anchored`.
4. Populate `.checks` (list of `Check`) so the demo can print the two hashes
   side by side; set `.expected_bundle_hash`, `.actual_bundle_hash`, `.summary`.
A hash mismatch is a **normal result** (`verified = False`), **not** an
exception. Only raise `LedgerError` for a genuine backend failure (RPC down).
(There is no on-chain "hash chain" to check like the local ledger has; you can
set `chain_intact = True` or omit that check.)

**Errors:** raise `src.ledger.base.LedgerError` for failures the caller must
handle. It's already imported in `evm.py`.

---

## 3. `EvidenceRecord` field spec (design your storage from this)

From `src/models.py`. The record is written to `out/` as JSON; on-chain you store
only the hashes. `bundle_hash` is a SHA-256 over the canonical JSON of all the
content fields (everything except `bundle_hash` itself) — it is computed for you
by `record.compute_bundle_hash()`; **do not recompute it in Solidity**, just
store/compare the value.

| Field | Type | Req | On-chain? | Units / notes |
|---|---|---|---|---|
| `record_id` | str (uuid4 hex, ≤64) | yes | key (bytes32/str) | primary key |
| `schema_version` | str | yes | optional | e.g. "1.0" |
| `subject_hash` | str hex-64 | yes | **yes → bytes32** | salted one-way face hash. **MUST stay hashed.** |
| `source_url` | str | yes | no (off-chain) | where the match was found |
| `platform` | str | yes | no | e.g. "twitter", "britannica.com" |
| `retrieved_at_utc` | str ISO-8601 | yes | no | fetch time |
| `image_sha256` | str hex-64 | yes | no (covered by bundle_hash) | byte-exact image hash |
| `image_phash` | str hex (~16) | yes | no | perceptual hash |
| `match_confidence` | float [0,1] | yes | no | cosine similarity |
| `match_threshold` | float [0,1] | yes | no | acceptance bar used |
| `post_text_sha256` | str hex-64 \| null | no | no | hash of post text |
| `notes` | str (≤2000) \| null | no | no | freeform, no extra PII |
| `bundle_hash` | str hex-64 | yes | **yes → bytes32** | the anchored fingerprint |

**Hard rules:**
- **Raw face embeddings NEVER go on chain (or anywhere off this machine).** You
  will only ever receive `subject_hash` / `bundle_hash`. There is no field that
  carries an embedding; do not add one.
- Anchor `bundle_hash` and `subject_hash` as `bytes32`. Keep the record_id so
  `get_record`/`verify` can find the anchor.
- Keep on-chain data tiny (a few bytes32 + timestamp) → cheap gas.

`Receipt` fields you fill: `status` (`ReceiptStatus.CONFIRMED|DUPLICATE|FAILED`),
`record_id`, `tx_ref` (tx hash), `backend="evm"`, `timestamp_utc`,
`block_ref` (block number as str), `detail` (human message). `entry_hash` /
`prev_hash` are local-ledger concepts — leave them `None`.

---

## 4. Run the shared test suite against your adapter (your pass criteria)

The contract tests in `tests/test_ledger_contract.py` are written against the
abstract interface. To run them against **your** adapter:

```bash
# make your adapter constructible from the factory with LEDGER_BACKEND=evm
set RIS_...                     # not needed for ledger tests
set LEDGER_BACKEND=evm
set RUN_EVM_TESTS=1
pytest -q tests/test_ledger_contract.py
```

`RUN_EVM_TESTS=1` adds an `evm` parametrization that builds your adapter via
`src.config.get_ledger_adapter()`. **Pass criterion: every test in that file is
green against `evm`, exactly as it is against `local`.** That covers: consent
round-trip + last-write-wins, unknown→not-consented, anchor→get round-trip,
idempotent duplicate anchoring, verify passes intact / fails tampered (as a
result, not an exception), and verify is a pure read.

> Testnet note: these tests do many small writes. Run them against a **local node
> (Anvil/Hardhat)** for speed/cost, then do one end-to-end run on your testnet.

---

## 5. Flip the backend and run the whole pipeline on your chain

```bash
set LEDGER_BACKEND=evm
python -m src.consent register samples/obama_a.jpg     # consent tx on your chain
python -m src.pipeline --image samples/obama_a.jpg     # anchors evidence on your chain
python -m src.verify   --evidence out/evidence_001.json
```

If those three work, integration is done — no other code changes.

---

## 6. Where to read your config (the only edit outside evm.py)

`src/config.py` → `get_ledger_adapter()` already has the `evm` branch:

```python
return EVMLedgerAdapter(
    rpc_url=os.environ.get("EVM_RPC_URL"),
    contract_address=os.environ.get("EVM_CONTRACT_ADDRESS"),
    chain_id=os.environ.get("EVM_CHAIN_ID"),
)
```

Add whatever kwargs you need here and read them in `EVMLedgerAdapter.__init__`.
Put the values in `.env` (gitignored). **Never** commit a private key or accept
one through the adapter's public API in a way that could get logged; use a
keystore / env / wallet of your choosing. `.env.example` has placeholder
`EVM_*` keys.

---

## 7. Open questions for you to decide

- **Network:** local (Anvil/Hardhat) for the demo, or a public testnet
  (Sepolia / Base Sepolia)? Local is free and fast; a public testnet is more
  impressive on camera. Both satisfy the brief.
- **Key management:** how the signer is supplied (env, keystore file, browser
  wallet). Keep it out of git.
- **Gas / batching:** anchor one evidence per tx (simple) or batch multiple
  `bundle_hash`es per tx (cheaper, more complex)? The interface is per-record;
  batching would be an internal optimisation inside `anchor_evidence`.
- **Storage layout:** two mappings (`consent[bytes32]→bool`,
  `anchor[bytes32 record_id]→(bundle_hash, subject_hash, timestamp)`), plus an
  index on `bundle_hash` for idempotency. Event logs are enough for
  `get_record`/`verify` if you prefer events over storage.
- **Consent withdrawal on an immutable chain:** last-write-wins via a new tx that
  sets the flag false (the current record is the latest event/state). Fine.

Ping me (the pipeline author) if any contract behaviour in §2 is awkward to
implement on-chain — better to adjust the seam together than to diverge from it.
