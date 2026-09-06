# Quickstart Runbook

Short guide to running the application on both the EVM Blockchain and the Local Ledger.

---

## 🚀 Option A: On-Chain EVM (Hardhat)

### 0. One-time install
```bash
pip install -r requirements.txt      # includes web3 (needed for the EVM backend)
npm install                          # Hardhat + toolchain (needs Node 20+)
npx hardhat compile                  # produces artifacts/ used by the deploy script
```

### 1. Start Local Blockchain
*(Terminal 1 — leave it running)*
```bash
npx hardhat node
```

### 2. Deploy Smart Contract
*(Terminal 2 — writes the contract address + LEDGER_BACKEND=evm into .env)*
```bash
python tools/deploy_contract.py
```
> The EVM backend only works while `npx hardhat node` is running. If you stop the
> node, set `LEDGER_BACKEND=local` in `.env` (Option B) or re-run steps 1-2.

### 3. Register Consent
```bash
python -m src.consent register samples/obama_a.jpg
```

### 4. Check Consent Status
```bash
python -m src.consent status samples/obama_a.jpg
```

### 5. Execute Pipeline & Verify
```bash
# Run full pipeline
python -m src.pipeline --image samples/obama_a.jpg

# Verify anchored evidence bundle
python -m src.verify --evidence out/evidence_001.json
```

---

## ⚡ Option B: Local Ledger (No Hardhat Needed)

1. Open `.env` and set:
   ```env
   LEDGER_BACKEND=local
   ```
2. Run commands directly:
   ```bash
   python -m src.consent register samples/obama_a.jpg
   python -m src.pipeline --image samples/obama_a.jpg
   python -m src.verify --evidence out/evidence_001.json
   ```

---

## 🧪 Run All Tests
```bash
pytest -v
```
