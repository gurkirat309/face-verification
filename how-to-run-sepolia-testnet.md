# Sepolia Testnet Quickstart Guide

Short and simple guide to deploying and running the application on the **Ethereum Sepolia Testnet**.

---

## 1. Compile & Deploy to Sepolia (Gurkirat and Ritabrata: NO NEED to compile and deploy this. already deployed. just execute the pipeline)
You can check the deployed contract here: https://sepolia.etherscan.io/tx/0x12d314044227b4ea5f95f0d845c389ddffd7794e9c6d52635b7b561b95cd56ab

```bash
# 1. Compile smart contract artifacts
npx hardhat compile

# 2. Deploy smart contract to Sepolia
python tools/deploy_contract.py
```
> `tools/deploy_contract.py` reads `EVM_RPC_URL` and `EVM_PRIVATE_KEY` from `.env`, signs the deployment with your private key, and automatically updates `.env` with `LEDGER_BACKEND=evm`, `EVM_CONTRACT_ADDRESS`, and `EVM_CHAIN_ID=11155111`.

---

## 2. Execute Pipeline & Verify

```bash
# 1. Run full pipeline and anchor evidence on Sepolia
python -m src.pipeline --image samples/obama_a.jpg

# 2. Verify anchored evidence bundle against Sepolia on-chain data
python -m src.verify --evidence out/evidence_001.json

# 3. Launch interactive CLI / UI
python -m src.ui
```
