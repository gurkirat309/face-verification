"""EVMLedgerAdapter -- STUB for the on-chain teammate to implement.

>>> THIS IS THE ONLY FILE YOU (the Web3 teammate) NEED TO EDIT. <<<

Read HANDOVER.md first. In short:

* The rest of the codebase talks only to the LedgerAdapter interface
  (src/ledger/base.py). If you make the tests in
  tests/test_ledger_contract.py pass against this class, the entire pipeline
  works against your real chain with zero other changes.
* Flip to your adapter by setting LEDGER_BACKEND=evm in .env. The factory in
  src/config.py does the rest.
* You will only ever receive HASHES of biometric data (subject_hash,
  bundle_hash) -- 64-char hex strings that map cleanly to bytes32. Never accept
  or store a raw face embedding. See the field spec in src/models.py and
  HANDOVER.md.

What to anchor on-chain (keep it tiny -> cheap gas):
    - bundle_hash  (bytes32)  <- the tamper-evident fingerprint of the evidence
    - subject_hash (bytes32)  <- the salted, one-way subject id
    - a timestamp / block time
    - record_id    (store as bytes32/string, your call) -> lets get_record work
The full EvidenceRecord JSON stays OFF chain (the pipeline already writes it to
out/). Your get_record() combines the on-chain hash with that off-chain JSON.

Suggested tools (your decision): web3.py, a testnet like Sepolia or Base
Sepolia, or a local node like Anvil/Hardhat. Key management, network, gas, and
batching are open questions listed for you in HANDOVER.md.

Every method below raises NotImplementedError on purpose so an accidental run on
the EVM backend fails loudly instead of silently doing nothing.
"""

from __future__ import annotations

from src.ledger.base import LedgerAdapter
from src.models import EvidenceRecord, Receipt, VerificationResult

_TODO = "EVMLedgerAdapter is not implemented yet. See HANDOVER.md and implement this method."


class EVMLedgerAdapter(LedgerAdapter):
    """On-chain implementation of the LedgerAdapter seam. Fill in the bodies.

    Constructor args are up to you (rpc_url, contract_address, chain_id, a signer
    reference, etc.). Read them from env in src/config.py's factory so nothing
    else in the codebase needs to know your parameters. Do NOT hardcode secrets;
    do NOT accept a raw private key through this class's public API in a way that
    could get logged.
    """

    def __init__(self, **kwargs: object) -> None:
        # Store rpc url / contract address / signer here once implemented.
        self._config = kwargs

    def register_consent(self, subject_hash: str, consent_flag: bool) -> Receipt:
        """See LedgerAdapter.register_consent. Send a tx that records consent for
        subject_hash; return a Receipt whose tx_ref is the tx hash and block_ref
        is the block number. status=CONFIRMED once mined; raise LedgerError (or
        status=FAILED) on revert."""
        raise NotImplementedError(_TODO)

    def is_consented(self, subject_hash: str) -> bool:
        """See LedgerAdapter.is_consented. A pure `view`/`call` read of current
        consent state for subject_hash. Unknown subject -> False."""
        raise NotImplementedError(_TODO)

    def anchor_evidence(self, record: EvidenceRecord) -> Receipt:
        """See LedgerAdapter.anchor_evidence. Ensure record.bundle_hash is set
        (call record.finalize() if empty), then anchor bundle_hash + subject_hash
        + record_id on-chain. Must be idempotent on duplicate bundle_hash
        (return status=DUPLICATE, no second tx). Store/return tx hash + block."""
        raise NotImplementedError(_TODO)

    def get_record(self, record_id: str) -> EvidenceRecord | None:
        """See LedgerAdapter.get_record. Combine the on-chain anchor for
        record_id with the off-chain JSON (out/) to rebuild the EvidenceRecord.
        Return None if either side is missing."""
        raise NotImplementedError(_TODO)

    def verify(self, record: EvidenceRecord) -> VerificationResult:
        """See LedgerAdapter.verify. PURE READ. Recompute record.bundle_hash,
        read the anchored bundle_hash from chain for record.record_id, and report
        verified = (anchor exists AND recomputed == anchored). Populate .checks
        so the demo can show the two hashes side by side. A mismatch is a normal
        FAIL result, not an exception."""
        raise NotImplementedError(_TODO)
