"""Evidence-bundle + verify tests (Stage 4).

Offline: builds records from a committed sample image (needs imagehash + Pillow,
NOT insightface), anchors on a temp ledger, and exercises the verifier including
all tamper modes. Uses env LOCAL_LEDGER_PATH so verify_bundle's factory-created
adapter reads the same temp ledger.
"""

from __future__ import annotations

import json
import os

import pytest

pytest.importorskip("imagehash", reason="imagehash not installed")
pytest.importorskip("PIL", reason="Pillow not installed")

from src.evidence import (  # noqa: E402
    BundlePaths,
    build_evidence_record,
    next_evidence_index,
    write_evidence_bundle,
)
from src.ledger.local import LocalLedgerAdapter  # noqa: E402
from src.models import sha256_hex  # noqa: E402

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "samples")
IMG = os.path.join(SAMPLES, "obama_a.jpg")

pytestmark = pytest.mark.skipif(not os.path.exists(IMG), reason="sample image missing")

SUBJECT = "d" * 64
POST_TEXT = "Barack Obama official portrait"


@pytest.fixture
def env_ledger(tmp_path, monkeypatch):
    """Point both the direct adapter and verify_bundle's factory at one temp ledger."""
    ledger_path = str(tmp_path / "ledger.json")
    monkeypatch.setenv("LEDGER_BACKEND", "local")
    monkeypatch.setenv("LOCAL_LEDGER_PATH", ledger_path)
    return LocalLedgerAdapter(path=ledger_path)


def _make_bundle(tmp_path, env_ledger):
    rec = build_evidence_record(
        subject_hash=SUBJECT,
        source_url="https://example.com/post/42",
        platform="example.com",
        matched_image_path=IMG,
        match_confidence=0.91,
        match_threshold=0.5,
        post_text=POST_TEXT,
    )
    paths = write_evidence_bundle(rec, matched_image_path=IMG, post_text=POST_TEXT, out_dir=str(tmp_path))
    env_ledger.anchor_evidence(rec)
    return rec, paths


def _verify(record_json):
    from src.verify import verify_bundle

    return verify_bundle(record_json)


def test_clean_bundle_verifies(tmp_path, env_ledger):
    _, paths = _make_bundle(tmp_path, env_ledger)
    ok, rows = _verify(paths.record_json)
    assert ok is True
    assert all(r[1] == "PASS" for r in rows)


def test_record_has_both_hashes(tmp_path, env_ledger):
    rec, _ = _make_bundle(tmp_path, env_ledger)
    assert len(rec.image_sha256) == 64
    assert len(rec.image_phash) >= 8  # perceptual hash hex
    assert rec.bundle_hash == rec.compute_bundle_hash()


def test_tamper_image_byte_fails(tmp_path, env_ledger):
    _, paths = _make_bundle(tmp_path, env_ledger)
    with open(paths.image, "ab") as fh:
        fh.write(b"\x00")  # append a byte -> different SHA-256
    ok, rows = _verify(paths.record_json)
    assert ok is False
    img_row = next(r for r in rows if r[0] == "image_sha256")
    assert img_row[1] == "FAIL"


def test_tamper_post_text_fails(tmp_path, env_ledger):
    _, paths = _make_bundle(tmp_path, env_ledger)
    with open(paths.post_text, "w", encoding="utf-8") as fh:
        fh.write(POST_TEXT + "!")  # one extra character
    ok, rows = _verify(paths.record_json)
    assert ok is False
    txt_row = next(r for r in rows if r[0] == "post_text_sha256")
    assert txt_row[1] == "FAIL"


def test_tamper_evidence_json_field_fails(tmp_path, env_ledger):
    _, paths = _make_bundle(tmp_path, env_ledger)
    with open(paths.record_json, "r", encoding="utf-8") as fh:
        d = json.load(fh)
    d["source_url"] = "https://evil.example.com/swapped"  # edit a content field
    with open(paths.record_json, "w", encoding="utf-8") as fh:
        json.dump(d, fh)
    ok, rows = _verify(paths.record_json)
    assert ok is False
    anchor_row = next(r for r in rows if r[0] == "bundle_hash_vs_anchor")
    assert anchor_row[1] == "FAIL"


def test_tamper_ledger_breaks_chain(tmp_path, env_ledger):
    rec, paths = _make_bundle(tmp_path, env_ledger)
    ledger_path = os.environ["LOCAL_LEDGER_PATH"]
    with open(ledger_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    # Edit an evidence entry payload without fixing its entry_hash.
    for e in data["entries"]:
        if e["type"] == "evidence":
            e["payload"]["source_url"] = "https://attacker.example.com"
            break
    with open(ledger_path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    ok, rows = _verify(paths.record_json)
    assert ok is False
    chain_row = next(r for r in rows if r[0] == "ledger_chain_intact")
    assert chain_row[1] == "FAIL"


def test_next_index_increments(tmp_path, env_ledger):
    assert next_evidence_index(str(tmp_path)) == 1
    _make_bundle(tmp_path, env_ledger)
    assert next_evidence_index(str(tmp_path)) == 2
