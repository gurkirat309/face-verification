# REMAINING — what's left, and how to demo (plain English)

This is your runbook. No jargon. Everything the *pipeline* needs is done and
working; what's left is the on-chain teammate's part (optional for a valid
submission) and the recording.

---

## Where things stand

**Done and working (all of it is on GitHub):**
- Face detection → web search → evidence → blockchain anchor, end to end.
- The consent gate (refuses faces that haven't opted in).
- A genuine live web search (validated: 21 real matches found for the sample).
- A real, tamper-evident local ledger + a verify command that proves it.
- Polished, colored terminal output. 41 automated tests passing.
- Full docs: `README.md`, `HANDOVER.md` (for the teammate), this file.

**The submission is already valid on the local ledger** — the contest rules allow
a local/simulated chain. The teammate's real chain is an upgrade, not a
requirement.

---

## Checklist

### Teammate's job (the on-chain part — optional upgrade)
- [ ] Read `HANDOVER.md`.
- [ ] Write the Solidity contract + fill in `src/ledger/evm.py`.
- [ ] Make the ledger tests pass with `LEDGER_BACKEND=evm` (their pass criterion).
- [ ] Deploy (local node or a testnet) and do one full run on their chain.

### Your job
- [ ] Record the screen demo (commands below).
- [ ] Submit the repo link + the recording before **Sept 7, 11:59 PM**.
- [ ] Decide: submit on the **local ledger** (safe, ready now) or **wait** for the
      teammate's chain. See the fallback plan below.

### Either of us
- [ ] Final read-through of the README before submitting.
- [ ] Pick which backend the recording uses (`local` is the safe default).

---

## The demo — exact commands, in order, with expected output

Do this in a fresh terminal, inside the project folder, with the venv active
(`.venv\Scripts\activate`). **The search is already cached, so this costs 0
SerpApi searches** unless you add `--refresh`.

**Step 0 — start clean (so the recording is reproducible):**
```bash
del data\ledger.json 2>nul & del out\evidence_* 2>nul
```

**Step 1 — register the subject's consent:**
```bash
python -m src.consent register samples\obama_a.jpg
```
Expect: `CONSENT REGISTERED … status: confirmed`.

**Step 2 — show the gate refusing someone who did NOT consent:**
```bash
python -m src.consent status samples\biden.jpg
```
Expect: `consented: False → searches are REFUSED`.

**Step 3 — run the full pipeline (the main event):**
```bash
python -m src.pipeline --image samples\obama_a.jpg
```
Expect three panels: **FACE** (subject hash + "consent OK"), **SEARCH**
(`CACHED`, ~59 candidates, ~21 face-checked, a VERIFIED MATCH ~0.99 from
Britannica/Library of Congress/etc.), **CHAIN** (a green `EVIDENCE ANCHORED`
panel with the hashes and `out\evidence_001.json`).

> To prove the search is *live* on camera, use `--refresh` instead (spends 1 of
> your ~246 remaining searches). Do this at most once or twice.

**Step 4 — verify the evidence is intact (green):**
```bash
python -m src.verify --evidence out\evidence_001.json
```
Expect a table with every row **PASS** and `VERDICT: VERIFIED`.

**Step 5 — tamper the image, verify again (this is the money shot):**
```bash
python -m src.verify --evidence out\evidence_001.json --tamper-image
```
Expect `image_sha256` **FAIL** with the two hashes shown, `VERDICT: TAMPERED`.

**Step 6 — tamper the ledger file itself, verify again:**
```bash
python tools\tamper_ledger.py
python -m src.verify --evidence out\evidence_001.json
```
Expect `ledger_chain_intact` **FAIL** naming the exact broken entry,
`VERDICT: TAMPERED`.

**Step 7 (only if the teammate's chain is ready) — same pipeline, real chain:**
```bash
set LEDGER_BACKEND=evm
python -m src.consent register samples\obama_a.jpg
python -m src.pipeline --image samples\obama_a.jpg
python -m src.verify --evidence out\evidence_001.json
```

After recording, restore a clean bundle: rerun Step 0, Step 1, Step 3.

---

## Pre-flight checklist for the recording

- [ ] Terminal font size **large** (18–22pt) — hashes must be readable.
- [ ] Window wide enough that the panels/tables don't wrap.
- [ ] venv active; you're in the project folder.
- [ ] Decide backend: `local` (default, safe) or `evm` (only if teammate's ready).
- [ ] Cache is warm (run Step 3 once before recording so it says `CACHED` — or
      plan a single `--refresh` to show a live search).
- [ ] `.env` is filled in (salt + SerpApi key). It's gitignored — it won't leak.
- [ ] Do a full dry run once before hitting record.
- [ ] Close anything with private info on screen.

---

## Fallback plan — if the teammate's chain isn't ready by Sept 7

**Submit on the local ledger. This is a complete, valid entry.** Nothing to
change in code — `LEDGER_BACKEND=local` is already the default.

What to say in the recording / README (one honest line):
> "The blockchain layer uses a local, append-only, hash-chained ledger — a real
> simulated chain that detects any tampering, which the rules permit. The same
> code runs against a public EVM chain by flipping one config value; that adapter
> is stubbed with a full handover spec for our on-chain teammate."

The README already contains this framing (see "Which blockchain, and why the seam
matters" and "Known limitations"). You don't need to edit anything.

---

## If you're short on time — do these first

1. **(2 min)** Confirm the repo is pushed and opens on GitHub.
2. **(5 min)** One dry run of Steps 0–6 above.
3. **(10 min)** Record Steps 1–6 on the local ledger. ← this alone is a full valid submission.
4. **(2 min)** Submit repo link + recording.
5. *(optional, later)* Steps 7 with the teammate's chain if it lands in time.

Total critical path: **~20 minutes** and you're submitted.
