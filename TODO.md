# TODO — Web3 work + what's still remaining in the app

A single, plain-English status of the whole project as of now. For the deep
contract spec see [HANDOVER.md](HANDOVER.md); for the demo/recording runbook see
[REMAINING.md](REMAINING.md). This file is the bird's-eye "what's left".

---

## 1. Snapshot — what already works (done)

- Full pipeline end to end: **face → consent gate → web search → evidence → anchor**.
- Consent gate refuses non-registered faces; every search is logged.
- Genuine live web search (SerpApi Google Lens, two-pass with face re-verify).
- **Tamper-evident local blockchain** (`LocalLedgerAdapter`, hash-chained) + a
  `verify` command that fails loudly on any tampering.
- Polished CLI output, 41 automated tests passing, full docs.
- **This is already a valid submission on the local ledger** — the contest rules
  allow a local/simulated chain.

The ONLY part not implemented is the *real on-chain* ledger. Everything below is
either that Web3 work (teammate) or optional hardening.

---

## 2. Web3 — what has to be done (teammate's part)

**Goal:** replace the local ledger with a real blockchain WITHOUT changing any
other code. The seam is already built; only `src/ledger/evm.py` gets filled in.
Full contract in [HANDOVER.md](HANDOVER.md). Ordered task list:

| # | Task | Detail | Est. |
|---|------|--------|------|
| 1 | **Pick a network** | Local node (Anvil/Hardhat — free, fast, no keys) for dev + demo, or a public testnet (Sepolia / Base Sepolia — needs a funded test wallet). Local is enough. | 15–30 min |
| 2 | **Design contract storage** | Two mappings: `consent[bytes32 subject] → bool`, `anchor[bytes32 recordId] → (bytes32 bundleHash, bytes32 subjectHash, uint256 timestamp)`, plus a way to detect a duplicate `bundleHash` (mapping or event). | 30 min |
| 3 | **Write + compile the Solidity contract** | Functions: `registerConsent(subject, flag)`, `isConsented(subject) view`, `anchorEvidence(recordId, bundleHash, subjectHash)`, `getAnchor(recordId) view`. Emit events so `getRecord`/`verify` can read back. | 1–2 h |
| 4 | **Deploy the contract** | To the chosen network; note the contract address + chain id. | 30 min |
| 5 | **Fill in `EVMLedgerAdapter`** | Implement the 5 methods with `web3.py` (`pip install web3`). Map tx hash → `Receipt.tx_ref`, block number → `block_ref`. Idempotent `anchor_evidence`. `verify` is a pure read. See HANDOVER §2–§3. | 2–3 h |
| 6 | **Wire config** | Read `EVM_RPC_URL`, `EVM_CONTRACT_ADDRESS`, `EVM_CHAIN_ID` (+ signer) in `src/config.py`'s existing `evm` branch. Put values in `.env` (never commit keys). | 20 min |
| 7 | **Pass the shared tests** | `set LEDGER_BACKEND=evm & set RUN_EVM_TESTS=1 & pytest -q tests/test_ledger_contract.py`. **Pass criterion: all green, same as `local`.** | 1 h (iterate) |
| 8 | **One full end-to-end run on-chain** | `LEDGER_BACKEND=evm` → `consent register` → `pipeline` → `verify`. If these work, integration is done. | 30 min |

**Rough total: ~half a day.** Hard rules for the teammate:
- Anchor only **hashes** (`bundle_hash`, `subject_hash`) as `bytes32`. **Raw face
  embeddings must never go on chain** — they never leave the local machine.
- Keep on-chain data tiny (a few `bytes32` + timestamp) → cheap gas.
- Idempotency required; a mismatch in `verify` is a *result*, not an exception.

**Open decisions for the teammate:** local vs testnet; key management (env /
keystore / wallet); one-anchor-per-tx vs batching; storage vs event-log reads.

---

## 3. Remaining in the app (non-Web3)

### Required before submission (yours)
- [ ] **Record the demo** — steps in [REMAINING.md](REMAINING.md) (§"The demo").
- [ ] **Submit** repo link + recording before **Sept 7, 11:59 PM**.
- [ ] Decide backend for the recording: `local` (ready now) or `evm` (only if the
      teammate finishes tasks in §2 first). Local is the safe default.

### Nice-to-have / hardening (optional, not needed for a valid submission)
- [ ] **Live "no match" demo.** The no-match path is tested, but only shown via a
      stub. To show it live on camera you'd need a face with no web presence
      (e.g. a throwaway/AI-generated face) — costs 1 SerpApi search. ~15 min.
- [ ] **Real crypto-shredding.** The README describes it; today the off-chain
      bundle in `out/` is plaintext. Add at-rest encryption of the bundle + a
      "shred" command that destroys the key, leaving only the on-chain hash. ~2 h.
- [ ] **Social-media-specific tagging.** Results carry a `source` string only;
      could classify hits into platforms (twitter/instagram/news/etc.) for a
      nicer report. ~1 h.
- [ ] **Per-install salt management.** `SUBJECT_HASH_SALT` is a single `.env`
      value; a production version would generate/rotate it per install. ~30 min.
- [ ] **CI.** Add a GitHub Actions workflow to run `pytest` on push (the live
      search is cached/stubbed, so CI stays free). ~30 min.
- [ ] **Cosmetic:** git shows CRLF/LF warnings on Windows — harmless; a
      `.gitattributes` would silence them. ~5 min.

### Explicitly out of scope (by brief — leave alone)
- Website / hosted frontend / deployment.
- Auth, user accounts, multi-tenancy.
- Improving accuracy at identifying *strangers*.

---

## 4. Integration — once the Web3 part is ready

No code changes beyond `evm.py` + the config values. Just:

```bash
set LEDGER_BACKEND=evm
python -m src.consent register samples\obama_a.jpg
python -m src.pipeline --image samples\obama_a.jpg
python -m src.verify   --evidence out\evidence_001.json
```

If those three succeed on the teammate's chain, the app is fully on-chain and the
recording can use `LEDGER_BACKEND=evm`. If the chain slips, ship on `local` — see
the fallback plan in [REMAINING.md](REMAINING.md).
