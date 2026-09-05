# FaceChain — Consent-Based Face → Web Search → Blockchain Evidence Pipeline

> **Status: Phase 0 complete (the seam + safety-net ledger).** Face, search, and
> polish stages land in later phases. See `REMAINING.md` (added later) for the
> live checklist.

## What it does (the honest one-liner)

A person uploads **their own** face. The pipeline searches the web for posts that
reuse their likeness (impersonation self-search), and when it finds a genuine
match it builds a tamper-evident **evidence bundle** and **anchors it on a
blockchain**. The finding is then provably timestamped — even after the
impersonating post is deleted.

Pipeline shape:

```
[ face scan ]  ->  [ consent gate ]  ->  [ web / social search ]  ->  [ evidence + blockchain anchor ]
   Phase 1           Phase 2                Phase 3                     Phase 4
```

## Why consent-based (not a face-finder for strangers)

Pointing a face-search tool at *strangers* is functionally a stalking tool and is
illegal or restricted in several places (Illinois BIPA, GDPR Art. 9, Canada's OPC
ruling against Clearview AI). This project deliberately builds the **consent-based
inversion**: you search for misuse of **your own** likeness. That framing is both
lawful and makes the blockchain *necessary* rather than decorative — the whole
value is a tamper-evident, independently-timestamped record.

A real consent gate enforces this in code: a subject must be registered
(`hash(face_embedding + salt)` → consent flag) before any search runs; unregistered
subjects are refused, and every search is logged.

## Biometric handling (non-negotiable)

- **No raw face embedding ever touches the ledger.** Only a salted SHA-256
  (`subject_hash`) does — one-way, non-invertible. This constraint is baked into
  the data model and the adapter docs so the on-chain teammate cannot violate it
  by accident.
- Raw images and embeddings stay **local**, under `data/` (gitignored).
- Sample faces must be your own or clearly public-domain — never scraped third
  parties.

## The ledger seam (why this project can't have integration surprises)

Everything talks to one interface, `LedgerAdapter`, with five operations:
`register_consent`, `is_consented`, `anchor_evidence`, `get_record`, `verify`.

Two implementations behind it:

| Adapter | Status | What it is |
|---|---|---|
| `LocalLedgerAdapter` | ✅ done | Real append-only, **hash-chained** JSON ledger on disk. Editing history breaks the chain and is detectable. A valid standalone submission. |
| `EVMLedgerAdapter` | stub | Same methods/types; the teammate fills in the bodies to talk to a real chain. |

Switch backends with **one env value**: `LEDGER_BACKEND=local|evm`. Nothing else
changes. The teammate's success criterion is literally "the shared adapter tests
pass against my adapter" (`tests/test_ledger_contract.py`). See `HANDOVER.md`
(added in Phase 6).

## SHA-256 vs perceptual hash (a design talking point)

The evidence bundle stores **both**:
- **SHA-256** of the matched image — byte-exact integrity (one flipped bit → fail).
- **pHash** (perceptual hash, `imagehash`) — survives crop/recompression, so the
  same image re-posted elsewhere still matches.

## Immutability vs. right-to-erasure

Blockchains are append-only, but people have a right to erasure. Answer:
**crypto-shredding** — the sensitive evidence lives off-chain (encrypted where
needed); the chain stores only an opaque hash pointer. Destroy the off-chain data
(or its key) and the on-chain hash becomes a meaningless fingerprint pointing at
nothing. (Fully wired as the pipeline matures.)

## Which blockchain

- **Today / default:** a local hash-chained ledger (`LocalLedgerAdapter`). The
  brief permits a local/simulated chain, and re-verification against the on-chain
  record is fully demonstrable (including tamper detection).
- **On-chain:** an EVM chain via `EVMLedgerAdapter` (teammate's work). Network /
  gas / key decisions are theirs — see `HANDOVER.md`.

## Setup & run

```bash
py -3.10 -m venv .venv
.venv\Scripts\activate        # Windows
pip install -r requirements.txt
cp .env.example .env          # then edit
pytest -q                     # runs the ledger contract suite
```

(Face / search / pipeline CLIs are documented as their phases land.)

## Known limitations

_Filled out in Phase 6 — RIS coverage gaps, false pos/neg, simulated chain ≠
production trust, erasure tension, threshold tuning._

## Repository layout

```
src/
  models.py          shared dataclasses (EvidenceRecord, Receipt, VerificationResult)
  config.py          .env loader + LEDGER_BACKEND factory
  ledger/
    base.py          LedgerAdapter interface + required-behaviour docs
    local.py         LocalLedgerAdapter (working, hash-chained)
    evm.py           EVMLedgerAdapter (stub for teammate)
tests/
  test_ledger_contract.py   adapter-agnostic contract suite
data/  out/          gitignored: images, embeddings, ledger, evidence JSON
```
