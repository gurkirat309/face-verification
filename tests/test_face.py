"""Face-stage tests (Stage 1).

These run the real insightface model against the committed public-domain samples,
so they are slower than the ledger contract tests and are skipped automatically
if insightface or the samples are unavailable. Kept in a separate file so the
fast ledger suite is never blocked by the heavy model load.
"""

from __future__ import annotations

import os

import numpy as np
import pytest

pytest.importorskip("insightface", reason="insightface not installed")
pytest.importorskip("cv2", reason="opencv not installed")

from src.face import (  # noqa: E402
    MultipleFacesError,
    NoFaceError,
    cosine_similarity,
    encode_face,
    subject_hash_from_embedding,
)

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "samples")
OBAMA_A = os.path.join(SAMPLES, "obama_a.jpg")
OBAMA_B = os.path.join(SAMPLES, "obama_b.jpg")
BIDEN = os.path.join(SAMPLES, "biden.jpg")

pytestmark = pytest.mark.skipif(
    not (os.path.exists(OBAMA_A) and os.path.exists(OBAMA_B) and os.path.exists(BIDEN)),
    reason="public-domain sample images not present",
)

SALT = "unit-test-salt"


@pytest.fixture(scope="module")
def enc():
    return {
        "a": encode_face(OBAMA_A, salt=SALT),
        "b": encode_face(OBAMA_B, salt=SALT),
        "biden": encode_face(BIDEN, salt=SALT),
    }


# --- detection / encoding --------------------------------------------------- #
def test_detects_a_face(enc):
    a = enc["a"]
    assert a.num_faces_detected >= 1
    assert a.embedding.shape[0] == 512
    assert a.det_score > 0.5
    assert a.quality > 0.0
    x1, y1, x2, y2 = a.bbox
    assert x2 > x1 and y2 > y1  # sane box


def test_subject_hash_shape(enc):
    sh = enc["a"].subject_hash
    assert len(sh) == 64
    int(sh, 16)  # valid hex


def test_public_dict_omits_embedding(enc):
    d = enc["a"].to_public_dict()
    # The raw embedding must never appear in the serialisable/public view.
    assert "embedding" not in d
    assert d["embedding_dim"] == 512
    assert d["subject_hash"] == enc["a"].subject_hash


# --- the headline: same vs different person --------------------------------- #
def test_same_person_high_similarity(enc):
    sim = cosine_similarity(enc["a"].embedding, enc["b"].embedding)
    assert sim > 0.4, f"same-person similarity unexpectedly low: {sim}"


def test_different_person_low_similarity(enc):
    sim = cosine_similarity(enc["a"].embedding, enc["biden"].embedding)
    assert sim < 0.3, f"different-person similarity unexpectedly high: {sim}"


def test_same_person_more_similar_than_different(enc):
    same = cosine_similarity(enc["a"].embedding, enc["b"].embedding)
    diff = cosine_similarity(enc["a"].embedding, enc["biden"].embedding)
    assert same > diff + 0.3


# --- hashing behaviour ------------------------------------------------------ #
def test_hash_deterministic(enc):
    again = subject_hash_from_embedding(enc["a"].embedding, SALT)
    assert again == enc["a"].subject_hash


def test_hash_depends_on_salt(enc):
    other = subject_hash_from_embedding(enc["a"].embedding, "a-different-salt")
    assert other != enc["a"].subject_hash


def test_different_people_get_different_hashes(enc):
    assert enc["a"].subject_hash != enc["biden"].subject_hash


# --- edge cases ------------------------------------------------------------- #
def test_no_face_raises(tmp_path):
    import cv2

    blank = np.full((240, 240, 3), 127, dtype=np.uint8)
    p = str(tmp_path / "blank.png")
    cv2.imwrite(p, blank)
    with pytest.raises(NoFaceError):
        encode_face(p, salt=SALT)


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        encode_face(os.path.join(SAMPLES, "does_not_exist.jpg"), salt=SALT)


def test_multiple_faces_strict_raises(tmp_path):
    import cv2

    a = cv2.imread(OBAMA_A)
    b = cv2.imread(BIDEN)
    h = min(a.shape[0], b.shape[0])
    combo = cv2.hconcat([a[:h], b[:h]])
    p = str(tmp_path / "two_faces.jpg")
    cv2.imwrite(p, combo)
    # Strict mode refuses ambiguity...
    with pytest.raises(MultipleFacesError):
        encode_face(p, salt=SALT, strict_single=True)
    # ...but default mode takes the largest and reports the count.
    res = encode_face(p, salt=SALT)
    assert res.num_faces_detected >= 2
