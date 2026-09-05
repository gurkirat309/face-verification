"""Search parsing tests (no network, no insightface) -- always run."""

from __future__ import annotations

from src.search import parse_candidates


def test_parse_reads_visual_and_exact_and_dedups():
    response = {
        "visual_matches": [
            {"position": 1, "title": "A", "link": "https://a.com/1", "source": "a.com",
             "thumbnail": "https://img/a1.jpg"},
            {"position": 2, "title": "B", "link": "https://b.com/2", "source": "b.com",
             "image": "https://img/b2.jpg"},
            {"position": 3, "title": "no image", "link": "https://c.com/3"},  # skipped: no image
        ],
        "exact_matches": [
            {"position": 1, "title": "A dup", "link": "https://a.com/1",  # dup link -> skipped
             "thumbnail": "https://img/a1b.jpg"},
            {"position": 2, "title": "D", "link": "https://d.com/4", "source": "d.com",
             "image": {"link": "https://img/d4.jpg"}},  # nested dict image
        ],
    }
    cands = parse_candidates(response)
    links = [c.page_link for c in cands]
    assert links == ["https://a.com/1", "https://b.com/2", "https://d.com/4"]
    assert cands[1].image_url == "https://img/b2.jpg"
    assert cands[2].image_url == "https://img/d4.jpg"  # unwrapped nested dict


def test_parse_empty_response():
    assert parse_candidates({}) == []
    assert parse_candidates({"ai_overview": {"text": "..."}}) == []
