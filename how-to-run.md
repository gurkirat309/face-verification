# Quickstart Runbook

Short guide to running the application on both the EVM Blockchain and the Local Ledger.

---

## 🚀 Option A: On-Chain EVM (Hardhat)

### 1. Start Local Blockchain
*(Terminal 1)*
```bash
npx hardhat node
```

### 2. Deploy Smart Contract
*(Terminal 2)*
```bash
python tools/deploy_contract.py
```

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
