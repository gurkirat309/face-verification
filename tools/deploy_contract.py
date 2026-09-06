"""Deploy EvidenceLedger smart contract to local Hardhat node (http://127.0.0.1:8545).
Prints contract address and updates .env file automatically.
"""

from __future__ import annotations

import json
import os
from web3 import Web3


def main() -> None:
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

    account = w3.eth.accounts[0]
    print(f"Deploying EvidenceLedger from account: {account}")

    Contract = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx_hash = Contract.constructor().transact({"from": account})
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)

    contract_address = receipt.contractAddress
    os.environ["EVM_CONTRACT_ADDRESS"] = contract_address
    print(f"✅ EvidenceLedger deployed at: {contract_address}")

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

    print(f"✅ Updated {env_path} with deployed contract configuration.")


if __name__ == "__main__":
    main()
