"""Consent gate tests (Stage 2).

These use the LocalLedgerAdapter directly with a temp ledger + temp audit log, so
they are fast and do NOT need insightface (subject hashes are just strings here).
"""

from __future__ import annotations

import pytest

from src.audit import SearchAuditLog
from src.consent import ConsentRefused, check_consent_or_refuse
from src.ledger.local import LocalLedgerAdapter


@pytest.fixture
def ledger(tmp_path):
    return LocalLedgerAdapter(path=str(tmp_path / "ledger.json"))


@pytest.fixture
def audit(tmp_path):
    return SearchAuditLog(path=str(tmp_path / "search_log.jsonl"))


SUBJECT = "a" * 64  # stand-in subject hash


def test_gate_refuses_unregistered(ledger, audit):
    with pytest.raises(ConsentRefused):
        check_consent_or_refuse(SUBJECT, ledger, operator="tester", audit=audit)


def test_gate_allows_after_registration(ledger, audit):
    ledger.register_consent(SUBJECT, True)
    # Should NOT raise.
    check_consent_or_refuse(SUBJECT, ledger, operator="tester", audit=audit)


def test_gate_refuses_after_withdrawal(ledger, audit):
    ledger.register_consent(SUBJECT, True)
    check_consent_or_refuse(SUBJECT, ledger, operator="tester", audit=audit)
    ledger.register_consent(SUBJECT, False)
    with pytest.raises(ConsentRefused):
        check_consent_or_refuse(SUBJECT, ledger, operator="tester", audit=audit)


def test_every_attempt_is_logged(ledger, audit):
    # one refused attempt
    with pytest.raises(ConsentRefused):
        check_consent_or_refuse(SUBJECT, ledger, operator="alice", audit=audit)
    # grant + one allowed attempt
    ledger.register_consent(SUBJECT, True)
    check_consent_or_refuse(SUBJECT, ledger, operator="alice", audit=audit)

    entries = audit.read_all()
    events = [e.event for e in entries]
    # attempt+refused, then attempt+allowed
    assert events == ["search_attempt", "search_refused", "search_attempt", "search_allowed"]
    assert all(e.operator == "alice" for e in entries)
    assert all(e.subject_hash == SUBJECT for e in entries)
    assert all(e.timestamp_utc.endswith("Z") for e in entries)
