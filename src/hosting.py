"""Temporary public image hosting for engines that need a URL (Yandex).

Yandex reverse image search takes a public image URL (no upload API) and rejects
many file hosts (catbox/tmpfiles). imgbb (i.ibb.co CDN) is accepted, so we upload
there. Uploads are made with a short expiration so the subject's face is not left
hosted, and the returned URL is cached by file hash to avoid re-uploading.

Needs a free imgbb API key in IMGBB_API_KEY (get one at https://api.imgbb.com/).
This is only used for the Yandex engine; the Google Lens engine uploads directly
to SerpApi and never needs this.

PRIVACY NOTE: this sends the image to imgbb. It is the same class of action as
sending the image to any reverse-image search service, and the expiration makes
it transient, but it is an external upload -- documented so it is a conscious
choice, not a silent one.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.parse
import urllib.request
from typing import Optional

IMGBB_ENDPOINT = "https://api.imgbb.com/1/upload"
_INDEX = os.path.join("cache", "uploads_imgbb.json")


class HostingError(RuntimeError):
    pass


def imgbb_upload(
    image_path: str,
    api_key: str,
    *,
    expiration: int = 600,  # seconds; auto-delete after 10 min by default
    timeout: int = 60,
    refresh: bool = False,
) -> str:
    """Upload a local image to imgbb and return a public direct-image URL.

    Cached by file content hash (+ expiration) so re-runs don't re-upload.
    """
    if not api_key:
        raise HostingError(
            "No imgbb API key. Set IMGBB_API_KEY in .env (free key at https://api.imgbb.com/)."
        )
    with open(image_path, "rb") as fh:
        raw = fh.read()
    fhash = hashlib.sha256(raw).hexdigest()

    index = {}
    if os.path.exists(_INDEX):
        with open(_INDEX, "r", encoding="utf-8") as fh:
            index = json.load(fh)
    # Cached URLs can expire; only reuse when caller allows and we stored one.
    if not refresh and fhash in index:
        return index[fhash]

    b64 = base64.b64encode(raw).decode("ascii")
    data = urllib.parse.urlencode(
        {"key": api_key, "image": b64, "expiration": str(expiration)}
    ).encode("ascii")
    try:
        with urllib.request.urlopen(IMGBB_ENDPOINT, data=data, timeout=timeout) as resp:
            out = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        raise HostingError(f"imgbb upload failed: {exc}") from exc
    if not out.get("success"):
        raise HostingError(f"imgbb upload rejected: {out}")
    url = out["data"].get("url") or out["data"].get("display_url")
    if not url:
        raise HostingError(f"imgbb returned no url: {out}")

    os.makedirs(os.path.dirname(os.path.abspath(_INDEX)), exist_ok=True)
    index[fhash] = url
    with open(_INDEX, "w", encoding="utf-8") as fh:
        json.dump(index, fh, indent=2)
    return url
