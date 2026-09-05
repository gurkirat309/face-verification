"""Ledger seam: the abstract LedgerAdapter and its two implementations.

Import the interface and error from here; get a live adapter from
``src.config.get_ledger_adapter()`` (do not instantiate concrete adapters
directly in pipeline code).
"""

from src.ledger.base import LedgerAdapter, LedgerError

__all__ = ["LedgerAdapter", "LedgerError"]
