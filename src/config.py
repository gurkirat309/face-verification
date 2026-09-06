"""Configuration + the ledger factory.

The whole point of the seam: ONE env value, ``LEDGER_BACKEND``, decides which
adapter the entire pipeline uses. Flip it from ``local`` to ``evm`` and nothing
else in the codebase changes.

    LEDGER_BACKEND=local   -> LocalLedgerAdapter (default; the safety net)
    LEDGER_BACKEND=evm     -> EVMLedgerAdapter   (teammate's on-chain adapter)
"""

from __future__ import annotations

import os

from src.ledger.base import LedgerAdapter
from src.models import LedgerBackend


def _load_dotenv() -> None:
    """Load .env into os.environ if present. Uses python-dotenv when installed,
    otherwise a tiny built-in parser so Phase 0 runs with zero dependencies."""
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
        return
    except Exception:
        pass
    # Minimal fallback parser: KEY=VALUE lines, '#' comments, no interpolation.
    path = ".env"
    if not os.path.exists(path):
        return
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def get_ledger_backend() -> LedgerBackend:
    _load_dotenv()
    raw = os.environ.get("LEDGER_BACKEND", "local").strip().lower()
    try:
        return LedgerBackend(raw)
    except ValueError as exc:
        raise ValueError(
            f"LEDGER_BACKEND={raw!r} is not valid. Use 'local' or 'evm'."
        ) from exc


def get_ledger_adapter() -> LedgerAdapter:
    """Return the active ledger adapter based on ``LEDGER_BACKEND``.

    Everything in the pipeline calls THIS -- never a concrete adapter class
    directly -- so switching backends is a one-line env change.
    """
    backend = get_ledger_backend()
    if backend is LedgerBackend.LOCAL:
        from src.ledger.local import LocalLedgerAdapter

        ledger_path = os.environ.get("LOCAL_LEDGER_PATH", os.path.join("data", "ledger.json"))
        return LocalLedgerAdapter(path=ledger_path)

    # backend is EVM
    from src.ledger.evm import EVMLedgerAdapter

    # The teammate reads whatever EVM_* env vars they need here.
    return EVMLedgerAdapter(
        rpc_url=os.environ.get("EVM_RPC_URL"),
        contract_address=os.environ.get("EVM_CONTRACT_ADDRESS"),
        chain_id=os.environ.get("EVM_CHAIN_ID"),
        private_key=os.environ.get("EVM_PRIVATE_KEY"),
    )

