"""EVMLedgerAdapter -- Real EVM smart-contract backed ledger implementation.

Talks to EvidenceLedger.sol deployed on an EVM network (local Hardhat node or testnet).
Maintains strict biometric privacy: only cryptographic hashes (subject_hash, bundle_hash)
are sent on-chain as bytes32.
"""

from __future__ import annotations

import glob
import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from hexbytes import HexBytes
from web3 import Web3

from src.ledger.base import LedgerAdapter, LedgerError
from src.models import (
    Check,
    EvidenceRecord,
    Receipt,
    ReceiptStatus,
    VerificationResult,
)


def _utcnow_iso() -> str:
    """Current UTC time as ISO-8601 with a trailing Z, second precision."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hex_to_bytes32(hex_str: str) -> bytes:
    """Convert a 64-char hex string to 32 bytes."""
    clean = hex_str.strip().lower()
    if clean.startswith("0x"):
        clean = clean[2:]
    if len(clean) != 64:
        raise ValueError(f"Expected 64-char hex string, got {len(clean)} chars: {hex_str!r}")
    return bytes.fromhex(clean)


# Default ABI for EvidenceLedger contract
ABI = [
    {
        "inputs": [
            {"internalType": "bytes32", "name": "subjectHash", "type": "bytes32"},
            {"internalType": "bool", "name": "flag", "type": "bool"}
        ],
        "name": "registerConsent",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "subjectHash", "type": "bytes32"}],
        "name": "isConsented",
        "outputs": [{"internalType": "bool", "name": "", "type": "bool"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [
            {"internalType": "string", "name": "recordId", "type": "string"},
            {"internalType": "bytes32", "name": "bundleHash", "type": "bytes32"},
            {"internalType": "bytes32", "name": "subjectHash", "type": "bytes32"}
        ],
        "name": "anchorEvidence",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "string", "name": "recordId", "type": "string"}],
        "name": "getAnchor",
        "outputs": [
            {"internalType": "bytes32", "name": "bundleHash", "type": "bytes32"},
            {"internalType": "bytes32", "name": "subjectHash", "type": "bytes32"},
            {"internalType": "uint256", "name": "timestamp", "type": "uint256"}
        ],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"internalType": "bytes32", "name": "bundleHash", "type": "bytes32"}],
        "name": "getRecordIdByBundle",
        "outputs": [{"internalType": "string", "name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function"
    }
]


class EVMLedgerAdapter(LedgerAdapter):
    """EVM on-chain implementation of the LedgerAdapter seam."""

    def __init__(
        self,
        rpc_url: Optional[str] = None,
        contract_address: Optional[str] = None,
        chain_id: Optional[str] = None,
        private_key: Optional[str] = None,
        **kwargs: object,
    ) -> None:
        self._rpc_url = rpc_url or os.environ.get("EVM_RPC_URL", "http://127.0.0.1:8545")
        self._contract_address = contract_address or os.environ.get("EVM_CONTRACT_ADDRESS", "")
        self._private_key = private_key or os.environ.get("EVM_PRIVATE_KEY", "")
        self._anchored_records: dict[str, EvidenceRecord] = {}

        self._w3 = Web3(Web3.HTTPProvider(self._rpc_url))
        if not self._w3.is_connected():
            raise LedgerError(f"Could not connect to EVM node at {self._rpc_url}")

        if not self._contract_address:
            raise LedgerError("EVM_CONTRACT_ADDRESS is not set. Run deployment script first.")

        # Load compiled ABI if available, otherwise fallback to static ABI
        contract_abi = ABI
        artifact_path = os.path.join("artifacts", "contracts", "EvidenceLedger.sol", "EvidenceLedger.json")
        if os.path.exists(artifact_path):
            try:
                with open(artifact_path, "r", encoding="utf-8") as fh:
                    contract_abi = json.load(fh).get("abi", ABI)
            except Exception:
                pass

        self._contract = self._w3.eth.contract(
            address=self._w3.to_checksum_address(self._contract_address),
            abi=contract_abi,
        )

        # Setup transaction sender account
        self._account = None
        if self._private_key:
            self._account = self._w3.eth.account.from_key(self._private_key)
        elif self._w3.eth.accounts:
            self._account_address = self._w3.eth.accounts[0]

    def _send_tx(self, contract_func: Any) -> tuple[HexBytes, int]:
        """Helper to sign and broadcast transaction, returning (tx_hash, block_number)."""
        try:
            if hasattr(self, "_account") and self._account is not None:
                # Private key signer
                tx = contract_func.build_transaction({
                    "from": self._account.address,
                    "nonce": self._w3.eth.get_transaction_count(self._account.address),
                    "gas": 300000,
                })
                signed = self._account.sign_transaction(tx)
                tx_hash = self._w3.eth.send_raw_transaction(signed.rawTransaction)
            elif hasattr(self, "_account_address") and self._account_address:
                # Local node unlocked account
                tx_hash = contract_func.transact({"from": self._account_address})
            else:
                raise LedgerError("No available signer (account or private key) configured for EVMLedgerAdapter.")

            receipt = self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
            if receipt.status != 1:
                raise LedgerError(f"Transaction reverted on-chain. Tx Hash: {tx_hash.hex()}")
            return tx_hash, receipt.blockNumber
        except Exception as exc:
            if isinstance(exc, LedgerError):
                raise
            raise LedgerError(f"EVM Transaction failed: {exc}") from exc

    def register_consent(self, subject_hash: str, consent_flag: bool) -> Receipt:
        try:
            if self.is_consented(subject_hash) == consent_flag:
                return Receipt(
                    status=ReceiptStatus.DUPLICATE,
                    record_id=subject_hash,
                    tx_ref="",
                    backend="evm",
                    timestamp_utc=_utcnow_iso(),
                    detail=f"Consent already {'granted' if consent_flag else 'withdrawn'} for this subject.",
                )

            subject_b32 = _hex_to_bytes32(subject_hash)
            func = self._contract.functions.registerConsent(subject_b32, consent_flag)
            tx_hash, block_num = self._send_tx(func)

            return Receipt(
                status=ReceiptStatus.CONFIRMED,
                record_id=subject_hash,
                tx_ref=tx_hash.hex(),
                backend="evm",
                timestamp_utc=_utcnow_iso(),
                block_ref=str(block_num),
                detail=f"Consent {'granted' if consent_flag else 'withdrawn'} and anchored on EVM.",
            )
        except LedgerError:
            raise
        except Exception as exc:
            raise LedgerError(f"Failed to register consent: {exc}") from exc

    def is_consented(self, subject_hash: str) -> bool:
        try:
            subject_b32 = _hex_to_bytes32(subject_hash)
            return bool(self._contract.functions.isConsented(subject_b32).call())
        except Exception as exc:
            raise LedgerError(f"EVM read is_consented failed: {exc}") from exc

    def anchor_evidence(self, record: EvidenceRecord) -> Receipt:
        if not record.bundle_hash:
            record.finalize()

        try:
            bundle_b32 = _hex_to_bytes32(record.bundle_hash)
            subject_b32 = _hex_to_bytes32(record.subject_hash)

            # Idempotency check: see if bundle_hash is already anchored
            existing_record_id = self._contract.functions.getRecordIdByBundle(bundle_b32).call()
            if existing_record_id:
                return Receipt(
                    status=ReceiptStatus.DUPLICATE,
                    record_id=existing_record_id,
                    tx_ref="",
                    backend="evm",
                    timestamp_utc=_utcnow_iso(),
                    detail="Identical evidence already anchored on EVM; no new entry written.",
                )

            func = self._contract.functions.anchorEvidence(record.record_id, bundle_b32, subject_b32)
            tx_hash, block_num = self._send_tx(func)

            self._anchored_records[record.record_id] = record

            return Receipt(
                status=ReceiptStatus.CONFIRMED,
                record_id=record.record_id,
                tx_ref=tx_hash.hex(),
                backend="evm",
                timestamp_utc=_utcnow_iso(),
                block_ref=str(block_num),
                detail="Evidence anchored on EVM ledger.",
            )
        except LedgerError:
            raise
        except Exception as exc:
            raise LedgerError(f"Failed to anchor evidence on EVM: {exc}") from exc

    def get_record(self, record_id: str) -> EvidenceRecord | None:
        try:
            bundle_b32, subject_b32, timestamp = self._contract.functions.getAnchor(record_id).call()
            if bundle_b32 == b"\x00" * 32:
                return None

            anchored_bundle_hash = bundle_b32.hex()

            # 1. Search off-chain JSON in out/ directory
            out_dir = "out"
            json_files = glob.glob(os.path.join(out_dir, "evidence_*.json"))
            for filepath in json_files:
                try:
                    with open(filepath, "r", encoding="utf-8") as fh:
                        data = json.load(fh)
                        if data.get("record_id") == record_id:
                            rec = EvidenceRecord.from_dict(data)
                            if rec.bundle_hash == anchored_bundle_hash:
                                return rec
                except Exception:
                    continue

            # 2. In-memory cache fallback (for unit testing when out/ json isn't written)
            cached = self._anchored_records.get(record_id)
            if cached and cached.bundle_hash == anchored_bundle_hash:
                return cached

            return None
        except Exception as exc:
            raise LedgerError(f"EVM get_record failed: {exc}") from exc

    def verify(self, record: EvidenceRecord) -> VerificationResult:
        checks: list[Check] = []
        try:
            recomputed = record.compute_bundle_hash()
            bundle_b32, subject_b32, timestamp = self._contract.functions.getAnchor(record.record_id).call()

            exists = bundle_b32 != b"\x00" * 32
            anchored_hash = bundle_b32.hex() if exists else None

            checks.append(
                Check(
                    name="record_found_on_ledger",
                    passed=exists,
                    expected=record.record_id,
                    actual=(record.record_id if exists else None),
                    detail="Record id present on ledger." if exists else "No such record_id anchored.",
                )
            )

            hash_matches = exists and (recomputed == anchored_hash)
            checks.append(
                Check(
                    name="bundle_hash_matches_anchor",
                    passed=hash_matches,
                    expected=anchored_hash,
                    actual=recomputed,
                    detail=(
                        "Recomputed bundle hash matches the anchored value."
                        if hash_matches
                        else "MISMATCH: the evidence has been altered since it was anchored."
                    ),
                )
            )

            verified = exists and hash_matches
            if verified:
                summary = "VERIFIED: evidence is intact and matches the on-ledger anchor."
            elif not exists:
                summary = "FAILED: this record was never anchored on the ledger."
            else:
                summary = "FAILED: evidence hash does not match the anchor -- content was tampered."

            return VerificationResult(
                verified=verified,
                record_id=record.record_id,
                checks=checks,
                expected_bundle_hash=anchored_hash,
                actual_bundle_hash=recomputed,
                chain_intact=True,
                summary=summary,
            )
        except Exception as exc:
            raise LedgerError(f"EVM verify failed: {exc}") from exc
