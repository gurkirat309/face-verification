"""Deploy EvidenceLedger smart contract to local Hardhat node (http://127.0.0.1:8545).
Prints contract address and updates .env file automatically.
"""

from __future__ import annotations

import json
import os
import sys
from web3 import Web3

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.config import _load_dotenv


def main() -> None:
    _load_dotenv()
    rpc_url = os.environ.get("EVM_RPC_URL", "http://127.0.0.1:8545")
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    if not w3.is_connected():
        raise RuntimeError(f"Could not connect to EVM RPC at {rpc_url}. Is 'npx hardhat node' running?")

    artifact_path = os.path.join("artifacts", "contracts", "EvidenceLedger.sol", "EvidenceLedger.json")
    if not os.path.exists(artifact_path):
        raise FileNotFoundError(f"Artifact not found at {artifact_path}. Run 'npx hardhat compile' first.")

    with open(artifact_path, "r", encoding="utf-8") as fh:
        artifact = json.load(fh)

    abi = artifact["abi"]
    bytecode = artifact["bytecode"]
    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)

    private_key = os.environ.get("EVM_PRIVATE_KEY", "").strip()
    if private_key:
        account_obj = w3.eth.account.from_key(private_key)
        account = account_obj.address
        print(f"Deploying EvidenceLedger from private key account: {account}")

        construct_tx = Contract.constructor().build_transaction({
            "from": account,
            "nonce": w3.eth.get_transaction_count(account),
            "chainId": w3.eth.chain_id,
        })
        signed_tx = account_obj.sign_transaction(construct_tx)
        raw_tx = getattr(signed_tx, "raw_transaction", None) or getattr(signed_tx, "rawTransaction")
        tx_hash = w3.eth.send_raw_transaction(raw_tx)
    elif w3.eth.accounts:
        account = w3.eth.accounts[0]
        print(f"Deploying EvidenceLedger from local node account: {account}")
        tx_hash = Contract.constructor().transact({"from": account})
    else:
        raise RuntimeError(
            "No available account to sign deployment. "
            "For Sepolia/Testnet: Ensure EVM_PRIVATE_KEY is set in .env. "
            "For Local Node: Ensure 'npx hardhat node' is running."
        )

    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    contract_address = receipt.contractAddress
    os.environ["EVM_CONTRACT_ADDRESS"] = contract_address
    print(f"[OK] EvidenceLedger deployed at: {contract_address}")

    print(f"   Block Number: {receipt.blockNumber}")
    print(f"   Tx Hash: {tx_hash.hex()}")

    # Update .env file
    env_path = ".env"
    env_lines = []
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as fh:
            env_lines = fh.readlines()

    keys_to_set = {
        "LEDGER_BACKEND": "evm",
        "EVM_RPC_URL": rpc_url,
        "EVM_CONTRACT_ADDRESS": contract_address,
        "EVM_CHAIN_ID": str(w3.eth.chain_id),
    }

    updated_keys = set()
    new_lines = []
    for line in env_lines:
        k, sep, v = line.partition("=")
        k_strip = k.strip()
        if k_strip in keys_to_set:
            new_lines.append(f"{k_strip}={keys_to_set[k_strip]}\n")
            updated_keys.add(k_strip)
        else:
            new_lines.append(line)

    for k, v in keys_to_set.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={v}\n")

    with open(env_path, "w", encoding="utf-8") as fh:
        fh.writelines(new_lines)

    print(f"[OK] Updated {env_path} with deployed contract configuration.")


if __name__ == "__main__":
    main()
