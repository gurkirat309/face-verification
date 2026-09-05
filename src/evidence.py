"""Evidence bundle construction + on-disk layout (Stage 3 -> Stage 4 bridge).

Turns a verified search match into an EvidenceRecord and writes the on-disk
"evidence bundle": the record JSON plus the sidecar artifacts needed to re-verify
it later (the matched image and the matched post text).

On-disk layout for evidence NNN (under out/):
    out/evidence_NNN.json        the EvidenceRecord (this is the anchored content)
    out/evidence_NNN.image.jpg   the matched image bytes  (for the SHA-256 re-hash)
    out/evidence_NNN.post.txt    the matched post text    (for the SHA-256 re-hash)

WHY SIDECARS: the record stores hashes, not the raw image/text. To demonstrate
tamper-evidence ("alter one byte of the image / one character of the post"), the
verifier must re-hash the actual artifact and compare it to the recorded hash.
The sidecars are those artifacts. They live under out/ (gitignored).

HASHES EXPLAINED (a README talking point):
  * SHA-256  -> byte-exact integrity. One flipped bit changes it completely.
  * pHash    -> perceptual hash (imagehash). Survives crop/recompression, so the
                SAME image re-posted elsewhere still matches (small Hamming
                distance). Comparable by distance, not equality.
"""

from __future__ import annotations

import glob
import os
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from src.models import EvidenceRecord, sha256_hex


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return sha256_hex(fh.read())


def phash_file(path: str) -> str:
    """Perceptual hash (hex) of an image file via imagehash.phash."""
    import imagehash
    from PIL import Image

    with Image.open(path) as im:
        return str(imagehash.phash(im.convert("RGB")))


def phash_distance(a: str, b: str) -> int:
    """Hamming distance between two hex pHash strings (0 == identical look)."""
    import imagehash

    return imagehash.hex_to_hash(a) - imagehash.hex_to_hash(b)


# --------------------------------------------------------------------------- #
# Bundle paths                                                                 #
# --------------------------------------------------------------------------- #
@dataclass
class BundlePaths:
    record_json: str
    image: str
    post_text: str

    @classmethod
    def for_index(cls, out_dir: str, index: int) -> "BundlePaths":
        base = os.path.join(out_dir, f"evidence_{index:03d}")
        return cls(record_json=base + ".json", image=base + ".image.jpg", post_text=base + ".post.txt")

    @classmethod
    def for_record_json(cls, record_json_path: str) -> "BundlePaths":
        base = re.sub(r"\.json$", "", record_json_path)
        return cls(record_json=record_json_path, image=base + ".image.jpg", post_text=base + ".post.txt")


def next_evidence_index(out_dir: str) -> int:
    existing = glob.glob(os.path.join(out_dir, "evidence_*.json"))
    nums = []
    for p in existing:
        m = re.search(r"evidence_(\d+)\.json$", p)
        if m:
            nums.append(int(m.group(1)))
    return (max(nums) + 1) if nums else 1


# --------------------------------------------------------------------------- #
# Build + persist                                                              #
# --------------------------------------------------------------------------- #
def build_evidence_record(
    *,
    subject_hash: str,
    source_url: str,
    platform: str,
    matched_image_path: str,
    match_confidence: float,
    match_threshold: float,
    post_text: str = "",
    notes: str = "",
    retrieved_at_utc: Optional[str] = None,
) -> EvidenceRecord:
    """Assemble a finalized EvidenceRecord from a verified match."""
    rec = EvidenceRecord(
        record_id=uuid.uuid4().hex,
        subject_hash=subject_hash,
        source_url=source_url,
        platform=platform or "unknown",
        retrieved_at_utc=retrieved_at_utc or utcnow_iso(),
        image_sha256=sha256_file(matched_image_path),
        image_phash=phash_file(matched_image_path),
        match_confidence=round(float(match_confidence), 6),
        match_threshold=round(float(match_threshold), 6),
        post_text_sha256=sha256_hex(post_text) if post_text else None,
        notes=notes or None,
    )
    return rec.finalize()


def write_evidence_bundle(
    record: EvidenceRecord,
    *,
    matched_image_path: str,
    post_text: str,
    out_dir: str = "out",
    index: Optional[int] = None,
) -> BundlePaths:
    """Write the record JSON + image + post-text sidecars to out/. Returns paths."""
    import json
    import shutil

    os.makedirs(out_dir, exist_ok=True)
    if index is None:
        index = next_evidence_index(out_dir)
    paths = BundlePaths.for_index(out_dir, index)

    with open(paths.record_json, "w", encoding="utf-8") as fh:
        json.dump(record.to_dict(), fh, indent=2, ensure_ascii=False)
    shutil.copyfile(matched_image_path, paths.image)
    with open(paths.post_text, "w", encoding="utf-8") as fh:
        fh.write(post_text or "")
    return paths


def load_evidence_record(record_json_path: str) -> EvidenceRecord:
    import json

    with open(record_json_path, "r", encoding="utf-8") as fh:
        return EvidenceRecord.from_dict(json.load(fh))
