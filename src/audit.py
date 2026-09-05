"""Append-only audit log for every search attempt.

The consent framing requires accountability: we log WHO ran a search, WHEN, and
against WHICH subject hash -- and whether it was allowed or refused. This is a
local, append-only JSONL file (one JSON object per line) under data/ (gitignored,
because it references subject hashes and operator identity).

Why JSONL and not the ledger: the ledger interface is intentionally narrow
(consent + evidence). The audit trail is higher-volume operational logging. It
could be anchored on-chain later (hash each day's log), but that is out of scope
for the graded pipeline. See README "Known limitations".
"""

from __future__ import annotations

import getpass
import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional

DEFAULT_LOG_PATH = os.path.join("data", "search_log.jsonl")


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def current_operator() -> str:
    """Best-effort identity of whoever is running the tool (the OS user).

    Overridable via --operator on the CLIs. This is 'who ran the search' for the
    audit trail; it is not an auth system (out of scope by design)."""
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USERNAME") or os.environ.get("USER") or "unknown"


@dataclass
class SearchLogEntry:
    timestamp_utc: str
    operator: str
    subject_hash: str
    event: str        # "search_attempt" | "search_allowed" | "search_refused" | "match_found" | "no_match"
    detail: str = ""


class SearchAuditLog:
    """Append-only writer/reader for the search audit trail."""

    def __init__(self, path: str = DEFAULT_LOG_PATH) -> None:
        self.path = path

    def _ensure_dir(self) -> None:
        d = os.path.dirname(os.path.abspath(self.path))
        os.makedirs(d, exist_ok=True)

    def log(self, subject_hash: str, event: str, detail: str = "", operator: Optional[str] = None) -> SearchLogEntry:
        entry = SearchLogEntry(
            timestamp_utc=_utcnow_iso(),
            operator=operator or current_operator(),
            subject_hash=subject_hash,
            event=event,
            detail=detail,
        )
        self._ensure_dir()
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")
        return entry

    def read_all(self) -> list[SearchLogEntry]:
        if not os.path.exists(self.path):
            return []
        out: list[SearchLogEntry] = []
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    out.append(SearchLogEntry(**json.loads(line)))
        return out
