"""Two-pass search orchestration tests.

No live SerpApi calls: a stub client returns a synthetic Lens response, and
download_image is monkeypatched to map candidate URLs to local sample images.
This exercises pass-2 face re-verification (the real differentiator) offline.
Requires insightface + samples.
"""

from __future__ import annotations

import os

import pytest

pytest.importorskip("insightface", reason="insightface not installed")
pytest.importorskip("cv2", reason="opencv not installed")

import src.search as search  # noqa: E402
from src.face import encode_face  # noqa: E402
from src.search import SearchOutcome, search_and_verify  # noqa: E402

SAMPLES = os.path.join(os.path.dirname(os.path.dirname(__file__)), "samples")
OBAMA_A = os.path.join(SAMPLES, "obama_a.jpg")
OBAMA_B = os.path.join(SAMPLES, "obama_b.jpg")
BIDEN = os.path.join(SAMPLES, "biden.jpg")

pytestmark = pytest.mark.skipif(
    not all(os.path.exists(p) for p in (OBAMA_A, OBAMA_B, BIDEN)),
    reason="sample images not present",
)

SALT = "search-test-salt"


class _StubClient:
    """Stands in for SerpApiLensClient; makes no network calls."""

    def __init__(self, response):
        self._response = response

    def upload_image(self, path, *, refresh=False):
        return "stub_image_id"

    def reverse_image_search(self, *, image_id=None, image_url=None, refresh=False):
        return self._response, True  # always "cached"


def _response_with(urls):
    return {
        "visual_matches": [
            {"position": i + 1, "title": f"cand{i}", "link": f"https://site{i}.com/p",
             "source": f"site{i}.com", "image": u}
            for i, u in enumerate(urls)
        ]
    }


@pytest.fixture(autouse=True)
def _map_downloads(monkeypatch):
    """Map fake candidate image URLs to local sample files (no downloads)."""
    mapping = {
        "url://obama_b": OBAMA_B,
        "url://biden": BIDEN,
        "url://missing": None,
    }
    monkeypatch.setattr(search, "download_image", lambda url, *a, **k: mapping.get(url))


def test_finds_and_verifies_same_person():
    ref = encode_face(OBAMA_A, salt=SALT)
    client = _StubClient(_response_with(["url://obama_b", "url://biden"]))
    outcome = search_and_verify(OBAMA_A, ref.embedding, client=client, threshold=0.4, salt=SALT)
    assert isinstance(outcome, SearchOutcome)
    assert outcome.matched is True
    assert outcome.checked == 2
    # obama_b is the match; biden is filtered out.
    assert len(outcome.matches) == 1
    assert outcome.matches[0].candidate.source == "site0.com"
    assert outcome.matches[0].similarity > 0.4


def test_no_match_clean_exit():
    ref = encode_face(OBAMA_A, salt=SALT)
    client = _StubClient(_response_with(["url://biden"]))  # only a different person
    outcome = search_and_verify(OBAMA_A, ref.embedding, client=client, threshold=0.4, salt=SALT)
    assert outcome.matched is False
    assert outcome.matches == []
    assert outcome.best_similarity < 0.4  # saw a face, but not a match


def test_candidate_without_downloadable_image_is_skipped():
    ref = encode_face(OBAMA_A, salt=SALT)
    client = _StubClient(_response_with(["url://missing", "url://obama_b"]))
    outcome = search_and_verify(OBAMA_A, ref.embedding, client=client, threshold=0.4, salt=SALT)
    assert outcome.checked == 1  # missing one skipped before face-check
    assert outcome.matched is True


def test_threshold_gates_matches():
    ref = encode_face(OBAMA_A, salt=SALT)
    client = _StubClient(_response_with(["url://obama_b"]))
    # Impossibly high threshold -> even the true match is rejected.
    outcome = search_and_verify(OBAMA_A, ref.embedding, client=client, threshold=0.999, salt=SALT)
    assert outcome.matched is False
    assert outcome.best_similarity > 0.4  # it WAS similar, just below the bar
