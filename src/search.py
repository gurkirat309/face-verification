"""Stage 3 -- Two-pass web/social search (the real differentiator).

Pass 1 (reverse image search): send the subject's face to Google Lens via
SerpApi and get back candidate web pages / images that look similar. This is a
GENUINE search -- it hits the live web -- not a hardcoded result.

Pass 2 (face re-verification): download each candidate image and RE-RUN the face
matcher on it, keeping only candidates whose face matches the subject above a
similarity threshold. This converts a fuzzy "looks similar" hit into a verified
match with a numeric confidence score that goes into the evidence bundle. A clean
"no match found" exit is a first-class outcome.

BUDGET DISCIPLINE (the SerpApi key is a scarce free tier):
  * Every SerpApi response is cached to disk (cache/serpapi/), keyed by the
    request. A cached run costs ZERO searches and is logged as CACHED.
  * A live call happens only on a cache miss or with refresh=True, and is logged
    as LIVE with a spent-search warning.
  * Candidate images are downloaded once and cached (cache/images/), so pass-2
    re-runs are free too.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

SERPAPI_ENDPOINT = "https://serpapi.com/search.json"
DEFAULT_CACHE_DIR = "cache"
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) FaceChain/0.1 (educational)"


class SearchError(RuntimeError):
    """A search-stage failure the caller should handle (network, quota, config)."""


# --------------------------------------------------------------------------- #
# Data structures                                                             #
# --------------------------------------------------------------------------- #
@dataclass
class Candidate:
    """One reverse-image-search hit (pass 1, before face verification)."""

    position: int
    title: str
    page_link: str          # the web/social page where the image appears
    source: str             # e.g. "twitter.com", "wikipedia.org"
    image_url: str          # full-size image URL (may be a crawler/SEO URL)
    thumbnail_url: str = ""  # Google-hosted thumbnail; reliable fallback for face check
    section: str = ""       # which response section it came from


@dataclass
class VerifiedMatch:
    """A candidate whose face was re-confirmed to match the subject (pass 2)."""

    candidate: Candidate
    similarity: float               # cosine similarity to the subject reference
    image_local_path: str           # cached downloaded image (for hashing later)


@dataclass
class SearchOutcome:
    """Result of the full two-pass search."""

    query_image_url: str
    was_cached: bool                # pass-1 response served from cache?
    total_candidates: int           # candidates returned by pass 1
    checked: int                    # candidates we actually face-checked
    threshold: float
    matches: list[VerifiedMatch] = field(default_factory=list)  # sorted desc by similarity
    best_similarity: float = 0.0    # highest similarity seen, even if below threshold

    @property
    def matched(self) -> bool:
        return len(self.matches) > 0


# --------------------------------------------------------------------------- #
# SerpApi client (cached)                                                     #
# --------------------------------------------------------------------------- #
SERPAPI_IMAGE_ENDPOINT = "https://serpapi.com/image"
_MAX_UPLOAD_BYTES = 500 * 1024  # SerpApi image upload cap


def _prepare_upload_bytes(image_path: str) -> bytes:
    """Return JPEG bytes for the image, downscaled if needed to stay under the
    SerpApi 500 KB upload limit."""
    with open(image_path, "rb") as fh:
        raw = fh.read()
    if len(raw) <= _MAX_UPLOAD_BYTES and image_path.lower().endswith((".jpg", ".jpeg")):
        return raw
    import cv2  # local import so the ledger tests never need opencv

    img = cv2.imread(image_path)
    if img is None:
        raise SearchError(f"Could not read image for upload: {image_path}")
    quality = 90
    scale = 1.0
    while True:
        resized = cv2.resize(img, None, fx=scale, fy=scale) if scale < 1.0 else img
        ok, buf = cv2.imencode(".jpg", resized, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok and len(buf) <= _MAX_UPLOAD_BYTES:
            return bytes(buf)
        if quality > 60:
            quality -= 10
        else:
            scale *= 0.8
        if scale < 0.2:
            raise SearchError(f"Could not compress {image_path} under 500 KB for upload.")


class SerpApiLensClient:
    """Minimal, cache-first client for SerpApi's Google Lens engine.

    Primary flow (works for any LOCAL image, no public URL needed):
        1. upload_image(path)  -> image_id   (POST /image; cached by file hash)
        2. reverse_image_search(image_id=...) -> Google Lens results (1 search)
    A url= flow is also supported for already-public images.
    """

    def __init__(self, api_key: str, cache_dir: str = DEFAULT_CACHE_DIR) -> None:
        if not api_key:
            raise SearchError(
                "No SerpApi key. Set RIS_API_KEY in .env (RIS_PROVIDER=serpapi)."
            )
        self.api_key = api_key
        self.cache_dir = os.path.join(cache_dir, "serpapi")
        self._upload_index = os.path.join(cache_dir, "uploads.json")

    # -- image upload (cached by file content hash) ------------------------- #
    def upload_image(self, image_path: str, *, refresh: bool = False, timeout: int = 40) -> str:
        data = _prepare_upload_bytes(image_path)
        fhash = hashlib.sha256(data).hexdigest()
        index = {}
        if os.path.exists(self._upload_index):
            with open(self._upload_index, "r", encoding="utf-8") as fh:
                index = json.load(fh)
        if not refresh and fhash in index:
            return index[fhash]

        boundary = "----FaceChain" + hashlib.sha1(fhash.encode()).hexdigest()[:16]
        body = self._multipart(boundary, data)
        req = urllib.request.Request(
            SERPAPI_IMAGE_ENDPOINT,
            data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "User-Agent": _UA},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                out = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            raise SearchError(f"SerpApi image upload failed: {exc}") from exc
        image_id = out.get("image_id")
        if not image_id:
            raise SearchError(f"SerpApi image upload returned no image_id: {out}")
        os.makedirs(os.path.dirname(os.path.abspath(self._upload_index)), exist_ok=True)
        index[fhash] = image_id
        with open(self._upload_index, "w", encoding="utf-8") as fh:
            json.dump(index, fh, indent=2)
        return image_id

    def _multipart(self, boundary: str, image_bytes: bytes) -> bytes:
        nl = b"\r\n"
        parts = [
            f"--{boundary}".encode(), nl,
            b'Content-Disposition: form-data; name="api_key"', nl, nl,
            self.api_key.encode(), nl,
            f"--{boundary}".encode(), nl,
            b'Content-Disposition: form-data; name="image"; filename="q.jpg"', nl,
            b"Content-Type: image/jpeg", nl, nl,
            image_bytes, nl,
            f"--{boundary}--".encode(), nl,
        ]
        return b"".join(parts)

    def _cache_path(self, key_fields: dict) -> str:
        key = hashlib.sha256(json.dumps(key_fields, sort_keys=True).encode()).hexdigest()[:32]
        return os.path.join(self.cache_dir, key + ".json")

    def reverse_image_search(
        self,
        *,
        image_id: Optional[str] = None,
        image_url: Optional[str] = None,
        refresh: bool = False,
        timeout: int = 40,
    ) -> tuple[dict, bool]:
        """Return (response_json, was_cached). Spends ONE SerpApi search only on
        a live call (cache miss or refresh=True). Provide image_id (preferred) or
        image_url."""
        if not image_id and not image_url:
            raise SearchError("reverse_image_search needs image_id or image_url.")
        key_fields = {"engine": "google_lens", "image_id": image_id, "url": image_url}
        cache_path = self._cache_path(key_fields)
        if not refresh and os.path.exists(cache_path):
            with open(cache_path, "r", encoding="utf-8") as fh:
                return json.load(fh), True

        params = {"engine": "google_lens", "api_key": self.api_key}
        if image_id:
            params["image_id"] = image_id
        else:
            params["url"] = image_url
        req_url = SERPAPI_ENDPOINT + "?" + urllib.parse.urlencode(params)
        try:
            req = urllib.request.Request(req_url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            if os.path.exists(cache_path):  # fall back to cache rather than die
                with open(cache_path, "r", encoding="utf-8") as fh:
                    return json.load(fh), True
            raise SearchError(f"SerpApi request failed and no cache available: {exc}") from exc

        if data.get("error"):
            raise SearchError(f"SerpApi returned an error: {data['error']}")

        os.makedirs(self.cache_dir, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        return data, False


# --------------------------------------------------------------------------- #
# Parsing + image download                                                     #
# --------------------------------------------------------------------------- #
def _as_url(v) -> str:
    if isinstance(v, dict):  # some schemas nest {link/url/width/height}
        return v.get("link") or v.get("url") or ""
    return v or ""


def parse_candidates(response: dict) -> list[Candidate]:
    """Extract candidates from a Google Lens response.

    Reads every section that can hold a matching page:
      * visual_matches  -- similar images across the web
      * exact_matches   -- pages using the exact same image
      * organic_results -- related web pages (often the person's own pages)
    Keeps BOTH the full image URL and the Google-hosted thumbnail so pass-2 can
    fall back to the thumbnail when the full image URL is a crawler/SEO link that
    doesn't download as a real image (common for LinkedIn/Instagram results).
    De-duplicates by page link.
    """
    out: list[Candidate] = []
    seen: set[str] = set()
    for section in ("visual_matches", "exact_matches", "organic_results"):
        for m in response.get(section, []):
            link = m.get("link") or ""
            image = _as_url(m.get("image"))
            thumb = _as_url(m.get("thumbnail"))
            if not (image or thumb) or (link and link in seen):
                continue
            if link:
                seen.add(link)
            out.append(
                Candidate(
                    position=int(m.get("position", len(out) + 1)),
                    title=(m.get("title") or "").strip(),
                    page_link=link,
                    source=m.get("source") or urllib.parse.urlparse(link).netloc,
                    image_url=image or thumb,
                    thumbnail_url=thumb,
                    section=section,
                )
            )
    return out


def download_image(url: str, cache_dir: str = DEFAULT_CACHE_DIR, timeout: int = 25) -> Optional[str]:
    """Download an image to cache/images/, keyed by URL hash. Returns the local
    path, or None on failure (per-candidate failures must not kill the run)."""
    img_dir = os.path.join(cache_dir, "images")
    key = hashlib.sha256(url.encode()).hexdigest()[:32]
    # Reuse any previously downloaded file with this key.
    if os.path.isdir(img_dir):
        for fn in os.listdir(img_dir):
            if fn.startswith(key):
                return os.path.join(img_dir, fn)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = resp.headers.get("Content-Type", "")
            data = resp.read()
    except Exception:
        return None
    ext = ".jpg"
    if "png" in ctype:
        ext = ".png"
    elif "webp" in ctype:
        ext = ".webp"
    os.makedirs(img_dir, exist_ok=True)
    path = os.path.join(img_dir, key + ext)
    with open(path, "wb") as fh:
        fh.write(data)
    return path


# --------------------------------------------------------------------------- #
# The two-pass orchestration                                                   #
# --------------------------------------------------------------------------- #
def search_and_verify(
    query_image_path: str,
    reference_embedding: np.ndarray,
    *,
    client: SerpApiLensClient,
    threshold: float = 0.5,
    max_candidates: int = 100,
    refresh: bool = False,
    salt: Optional[str] = None,
    verbose: bool = False,
) -> SearchOutcome:
    """Run pass-1 reverse image search, then pass-2 face re-verification.

    Args:
        query_image_path: LOCAL path to the subject's image. It is uploaded to
            SerpApi (bytes) to get an image_id, then Google Lens searches on it.
        reference_embedding: the subject's normalized embedding (from Stage 1).
        client: a SerpApiLensClient.
        threshold: cosine similarity a candidate must beat to count as a match.
        max_candidates: cap on how many candidates we download+check (pass 2).
        refresh: force a live SerpApi call even if cached.
    """
    from src.face import cosine_similarity, encode_face, NoFaceError, FaceError

    image_id = client.upload_image(query_image_path, refresh=refresh)
    response, was_cached = client.reverse_image_search(image_id=image_id, refresh=refresh)
    candidates = parse_candidates(response)

    outcome = SearchOutcome(
        query_image_url=f"image_id:{image_id}",
        was_cached=was_cached,
        total_candidates=len(candidates),
        checked=0,
        threshold=threshold,
    )

    from src._util import suppress_native_stderr

    for cand in candidates[:max_candidates]:
        # Try the full image first, then fall back to the Google-hosted thumbnail
        # (the full URL for LinkedIn/Instagram hits is often a crawler/SEO link
        # that doesn't download as a real face image).
        res = None
        used_path = None
        for url in (cand.image_url, cand.thumbnail_url):
            if not url:
                continue
            path = download_image(url)
            if not path:
                continue
            try:
                with suppress_native_stderr():  # hush libpng noise from odd thumbnails
                    res = encode_face(path, salt=salt or "search-temp-salt")
                used_path = path
                break
            except (NoFaceError, FaceError, FileNotFoundError):
                continue  # try the next URL for this candidate
        if res is None:
            continue  # no downloadable face from any URL -> skip candidate
        outcome.checked += 1
        sim = cosine_similarity(reference_embedding, res.embedding)
        if verbose:
            print(f"    checked {cand.source[:28]:28} [{cand.section[:6]:6}] sim={sim:+.3f} {'MATCH' if sim>=threshold else ''}")
        outcome.best_similarity = max(outcome.best_similarity, sim)
        if sim >= threshold:
            outcome.matches.append(VerifiedMatch(candidate=cand, similarity=sim, image_local_path=used_path))

    outcome.matches.sort(key=lambda m: m.similarity, reverse=True)
    return outcome


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.search",
        description="Two-pass reverse-image search + face re-verification.",
    )
    p.add_argument("--image", required=True, help="local image of the subject (uploaded to SerpApi + used for pass-2 matching)")
    p.add_argument("--reference", default=None, help="local reference image for pass-2 (default: same as --image)")
    p.add_argument("--threshold", type=float, default=None, help="match threshold [0..1] (default: FACE_MATCH_THRESHOLD or 0.5)")
    p.add_argument("--max", type=int, default=100, help="max candidates to face-check")
    p.add_argument("--refresh", action="store_true", help="force a LIVE SerpApi call (spends 1 search)")
    p.add_argument("--salt", default=None, help="hashing salt (default from .env)")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    from src.config import _load_dotenv
    from src.face import encode_face

    args = _build_parser().parse_args(argv)
    _load_dotenv()
    api_key = os.environ.get("RIS_API_KEY", "")
    threshold = args.threshold if args.threshold is not None else float(os.environ.get("FACE_MATCH_THRESHOLD", "0.5"))

    try:
        reference = args.reference or args.image
        ref = encode_face(reference, salt=args.salt)
        client = SerpApiLensClient(api_key)
        print(f"[SEARCH] pass 1: upload + reverse image search  ({'LIVE - spends 1 search' if args.refresh else 'cache if available'})")
        outcome = search_and_verify(
            args.image, ref.embedding, client=client,
            threshold=threshold, max_candidates=args.max, refresh=args.refresh,
            salt=args.salt, verbose=True,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"[SEARCH] source          : {'CACHED (0 searches spent)' if outcome.was_cached else 'LIVE (1 search spent)'}")
    print(f"[SEARCH] candidates      : {outcome.total_candidates} returned, {outcome.checked} face-checked")
    print(f"[SEARCH] threshold       : {outcome.threshold}")
    if outcome.matched:
        print(f"[SEARCH] VERIFIED MATCHES : {len(outcome.matches)} (best similarity {outcome.matches[0].similarity:.3f})")
        for m in outcome.matches[:5]:
            print(f"    - {m.similarity:.3f}  {m.candidate.source}  {m.candidate.page_link}")
    else:
        print(f"[SEARCH] NO MATCH FOUND   (best similarity seen: {outcome.best_similarity:.3f}, below {outcome.threshold})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
