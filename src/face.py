"""Stage 1 -- Face detection, embedding, and salted subject hashing.

Given an image, this stage:
  1. Detects faces (insightface / buffalo_l -> ArcFace w600k_r50).
  2. Selects the primary face (largest by area) and produces:
       - a bounding box (x1, y1, x2, y2, pixels)
       - a normalized 512-d embedding (L2-normalized; stays LOCAL, never anchored)
       - a quality score in [0, 1] with a breakdown
       - a salted subject hash (the ONLY biometric-derived value that may leave
         this machine or touch a ledger)
  3. Handles the awkward cases explicitly: no face, multiple faces, poor quality.

BIOMETRIC RULE: the raw embedding never leaves this process except to a
gitignored local file you explicitly ask for. What identifies a subject on the
ledger is ``subject_hash`` = SHA-256(salt + quantized-embedding), which is
one-way and cannot be inverted back to a face.

Note on identity vs. image: two different photos of the same person produce
*similar but not identical* embeddings, so their subject_hashes differ. The
subject is therefore identified by ONE enrolled reference embedding: you register
consent with a reference image and run the pipeline with that same reference. The
fuzzy "is this the same person" comparison happens on the raw embeddings locally
(cosine similarity), NOT on the hash. This is a deliberate, documented limitation
(a hash cannot do fuzzy matching).

CLI:
    python -m src.face path/to/image.jpg
    python -m src.face path/to/image.jpg --json --min-quality 0.5
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from src.models import canonical_json, sha256_hex

# Precision to which the normalized embedding is quantized before hashing. Makes
# the subject_hash reproducible across runs despite tiny float noise, while
# staying specific to the enrolled face.
_HASH_QUANT_DECIMALS = 5

# Default acceptance thresholds (override via CLI / env).
DEFAULT_MIN_DET_SCORE = 0.5   # insightface detection confidence
DEFAULT_MIN_FACE_FRAC = 0.02  # face area / image area
DEFAULT_MIN_QUALITY = 0.35    # composite score below which we call it poor


# --------------------------------------------------------------------------- #
# Errors -- explicit, so callers (pipeline, tests) can branch on the case.     #
# --------------------------------------------------------------------------- #
class FaceError(RuntimeError):
    """Base class for face-stage problems."""


class NoFaceError(FaceError):
    """No face detected in the image."""


class MultipleFacesError(FaceError):
    """More than one face detected and strict single-face mode was requested."""


class LowQualityError(FaceError):
    """The primary face is below the required quality threshold."""


# --------------------------------------------------------------------------- #
# Result                                                                       #
# --------------------------------------------------------------------------- #
@dataclass
class FaceResult:
    """Output of the face stage for one image.

    ``embedding`` is kept in memory for local matching (Phase 3) but is NEVER
    serialized into an EvidenceRecord or onto a ledger. ``to_public_dict``
    deliberately omits it.
    """

    bbox: tuple[int, int, int, int]         # x1, y1, x2, y2 in pixels
    det_score: float                        # detection confidence [0,1]
    quality: float                          # composite quality [0,1]
    quality_detail: dict[str, float]        # breakdown of the quality score
    num_faces_detected: int
    subject_hash: str                       # salted, one-way
    embedding: np.ndarray = field(repr=False)  # normed 512-d; LOCAL ONLY
    image_path: str = ""
    image_size: tuple[int, int] = (0, 0)    # width, height

    @property
    def usable(self) -> bool:
        return self.quality >= DEFAULT_MIN_QUALITY

    def to_public_dict(self) -> dict[str, Any]:
        """Everything EXCEPT the raw embedding -- safe to print or log."""
        return {
            "image_path": self.image_path,
            "image_size": {"width": self.image_size[0], "height": self.image_size[1]},
            "num_faces_detected": self.num_faces_detected,
            "bbox": {"x1": self.bbox[0], "y1": self.bbox[1], "x2": self.bbox[2], "y2": self.bbox[3]},
            "det_score": round(self.det_score, 4),
            "quality": round(self.quality, 4),
            "quality_detail": {k: round(v, 4) for k, v in self.quality_detail.items()},
            "embedding_dim": int(self.embedding.shape[0]),
            "subject_hash": self.subject_hash,
        }


# --------------------------------------------------------------------------- #
# Model loader (singleton -- loading is slow, ~1-2s)                            #
# --------------------------------------------------------------------------- #
_APP = None


def _get_app():
    global _APP
    if _APP is None:
        import warnings

        warnings.filterwarnings("ignore")
        os.environ.setdefault("OPENCV_LOG_LEVEL", "SILENT")
        os.environ.setdefault("INSIGHTFACE_HOME", os.path.expanduser("~/.insightface"))
        # Silence the noisy per-model prints from insightface.
        import contextlib
        import io

        from insightface.app import FaceAnalysis

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
            app.prepare(ctx_id=-1, det_size=(640, 640))
        _APP = app
    return _APP


# --------------------------------------------------------------------------- #
# Quality scoring                                                              #
# --------------------------------------------------------------------------- #
def _laplacian_sharpness(gray: np.ndarray) -> float:
    """Variance of the Laplacian -- a standard blur proxy. Higher = sharper."""
    import cv2

    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _quality_score(face, img: np.ndarray) -> tuple[float, dict[str, float]]:
    """Composite face quality in [0,1] from detection confidence, relative face
    size, and sharpness. Returns (score, breakdown)."""
    import cv2

    h, w = img.shape[:2]
    x1, y1, x2, y2 = [int(v) for v in face.bbox]
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    face_area = max(1, (x2 - x1) * (y2 - y1))
    frac = face_area / float(w * h)

    crop = img[y1:y2, x1:x2]
    if crop.size == 0:
        sharp_raw = 0.0
    else:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        sharp_raw = _laplacian_sharpness(gray)

    # Normalise each component to ~[0,1] with gentle, documented curves.
    det = float(min(1.0, max(0.0, face.det_score)))
    size_component = float(min(1.0, frac / 0.20))          # 20% of frame -> full marks
    sharp_component = float(min(1.0, sharp_raw / 150.0))    # Laplacian var 150 -> full marks

    # Weighted blend. Detection confidence dominates; size and sharpness refine.
    score = 0.5 * det + 0.25 * size_component + 0.25 * sharp_component
    detail = {
        "det_score": det,
        "face_frac": frac,
        "size_component": size_component,
        "sharpness_raw": sharp_raw,
        "sharpness_component": sharp_component,
    }
    return float(round(score, 6)), detail


# --------------------------------------------------------------------------- #
# Hashing                                                                      #
# --------------------------------------------------------------------------- #
def subject_hash_from_embedding(embedding: np.ndarray, salt: str) -> str:
    """Salted, one-way subject id from a normalized embedding.

    The embedding is quantized (see _HASH_QUANT_DECIMALS) so the same enrolled
    face reproduces the same hash despite tiny float noise. NEVER reversible.
    """
    quant = np.round(np.asarray(embedding, dtype=np.float64), _HASH_QUANT_DECIMALS)
    payload = canonical_json({"salt": salt, "emb": quant.tolist()})
    return sha256_hex(payload)


def _detect_hires(app, img):
    """Re-run detection at larger input sizes for small faces in big images.

    insightface downscales to the prepared det_size (640) before detecting, so a
    small face in a high-resolution photo can be missed. We temporarily bump the
    detector to 1280 then 1920, and always restore 640 afterwards.
    """
    import contextlib
    import io

    try:
        for size in (1280, 1920):
            with contextlib.redirect_stdout(io.StringIO()):
                app.prepare(ctx_id=-1, det_size=(size, size))
            faces = app.get(img)
            if faces:
                return faces
        return []
    finally:
        with contextlib.redirect_stdout(io.StringIO()):
            app.prepare(ctx_id=-1, det_size=(640, 640))


def _load_image_bgr(path: str):
    """Load an image as a BGR uint8 array for insightface, RESPECTING EXIF
    orientation.

    OpenCV's imread ignores EXIF, so phone photos saved sideways (with a
    'rotate on display' flag) reach the detector rotated 90 deg and the face is
    missed -- the classic "face exists but not found" bug. We load via PIL,
    apply exif_transpose, and hand back BGR. Falls back to cv2.imread if PIL
    can't open it.
    """
    import cv2

    try:
        from PIL import Image, ImageOps

        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)  # honour orientation flag
            rgb = np.array(im.convert("RGB"))
        return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    except Exception:
        return cv2.imread(path)


def _resolve_salt(salt: Optional[str]) -> str:
    if salt:
        return salt
    from src.config import _load_dotenv

    _load_dotenv()
    env_salt = os.environ.get("SUBJECT_HASH_SALT", "")
    if not env_salt or env_salt == "change-me-to-a-long-random-string":
        raise FaceError(
            "No usable SUBJECT_HASH_SALT. Set it in .env (see .env.example) or pass --salt."
        )
    return env_salt


# --------------------------------------------------------------------------- #
# Main entry                                                                   #
# --------------------------------------------------------------------------- #
def encode_face(
    image_path: str,
    salt: Optional[str] = None,
    *,
    allow_multiple: bool = True,
    strict_single: bool = False,
    min_quality: float = 0.0,
    recover: bool = True,
) -> FaceResult:
    """Detect and encode the primary face in ``image_path``.

    Args:
        image_path: path to a local image (jpg/png/...).
        salt: hashing salt; defaults to SUBJECT_HASH_SALT from .env.
        allow_multiple: if False (or strict_single), raise on >1 face.
        strict_single: raise MultipleFacesError if more than one face is found
            (use for enrollment, where ambiguity is unacceptable).
        min_quality: raise LowQualityError if the primary face scores below this.

    Returns:
        FaceResult for the largest detected face.

    Raises:
        FileNotFoundError, NoFaceError, MultipleFacesError, LowQualityError.
    """
    import cv2

    if not os.path.exists(image_path):
        raise FileNotFoundError(f"Image not found: {image_path}")

    img = _load_image_bgr(image_path)
    if img is None:
        raise FaceError(f"Could not read image (unsupported/corrupt?): {image_path}")

    app = _get_app()
    faces = app.get(img)

    # Expensive recovery passes run only for a real input image (recover=True),
    # never for the many candidate thumbnails during search (where "no face" is a
    # normal, expected outcome and speed matters).
    if not faces and recover:
        # Fallback 1: high-res re-detect. insightface shrinks the image to 640px
        # before detecting, so a small face in a big phone photo can be missed.
        faces = _detect_hires(app, img)

    if not faces and recover:
        # Fallback 2: the photo may be rotated (phone photo with wrong/no EXIF flag).
        # Try the three rotations and keep the first that yields a face.
        for rot in (cv2.ROTATE_90_CLOCKWISE, cv2.ROTATE_90_COUNTERCLOCKWISE, cv2.ROTATE_180):
            rimg = cv2.rotate(img, rot)
            rfaces = app.get(rimg) or _detect_hires(app, rimg)
            if rfaces:
                img, faces = rimg, rfaces
                break

    h, w = img.shape[:2]

    if not faces:
        raise NoFaceError(
            f"No face detected in {image_path} (tried all orientations). "
            "Use a clearer, front-facing photo where the face is not too small."
        )

    if len(faces) > 1 and (strict_single or not allow_multiple):
        raise MultipleFacesError(
            f"{len(faces)} faces detected in {image_path}; enrollment needs exactly one. "
            "Crop to a single face or pass --allow-multiple to take the largest."
        )

    # Primary face = largest bounding-box area.
    def _area(f):
        x1, y1, x2, y2 = f.bbox
        return (x2 - x1) * (y2 - y1)

    face = max(faces, key=_area)

    quality, detail = _quality_score(face, img)
    if quality < min_quality:
        raise LowQualityError(
            f"Primary face quality {quality:.3f} below required {min_quality:.3f} "
            f"({detail}). Use a sharper, larger, front-facing image."
        )

    embedding = np.asarray(face.normed_embedding, dtype=np.float32)
    resolved_salt = _resolve_salt(salt)
    sh = subject_hash_from_embedding(embedding, resolved_salt)

    x1, y1, x2, y2 = [int(v) for v in face.bbox]
    return FaceResult(
        bbox=(x1, y1, x2, y2),
        det_score=float(face.det_score),
        quality=quality,
        quality_detail=detail,
        num_faces_detected=len(faces),
        subject_hash=sh,
        embedding=embedding,
        image_path=image_path,
        image_size=(w, h),
    )


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity of two embeddings in [-1, 1] (normed -> ~[0,1] for faces).

    insightface embeddings are L2-normalized, so this is just the dot product.
    Used by the Phase-3 pass-2 re-match and by the tests.
    """
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    denom = (np.linalg.norm(a) * np.linalg.norm(b)) or 1.0
    return float(np.dot(a, b) / denom)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #
def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m src.face",
        description="Detect a face, embed it, and print its salted subject hash.",
    )
    p.add_argument("image", help="path to an input image")
    p.add_argument("--salt", default=None, help="hashing salt (default: SUBJECT_HASH_SALT from .env)")
    p.add_argument("--min-quality", type=float, default=0.0, help="reject faces below this quality [0..1]")
    p.add_argument("--strict-single", action="store_true", help="fail if more than one face is present")
    p.add_argument("--save-embedding", default=None, help="write the raw embedding to this local .npy (gitignored dir)")
    p.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        res = encode_face(
            args.image,
            salt=args.salt,
            strict_single=args.strict_single,
            min_quality=args.min_quality,
        )
    except (FileNotFoundError, FaceError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        # 2 = no face / not found, 3 = multiple, 4 = low quality, 1 = other
        code = {NoFaceError: 2, MultipleFacesError: 3, LowQualityError: 4}.get(type(exc), 1)
        return code

    if args.save_embedding:
        os.makedirs(os.path.dirname(os.path.abspath(args.save_embedding)), exist_ok=True)
        np.save(args.save_embedding, res.embedding)

    if args.json:
        import json

        print(json.dumps(res.to_public_dict(), indent=2))
    else:
        d = res.to_public_dict()
        print("FACE STAGE")
        print(f"  image           : {d['image_path']}  ({d['image_size']['width']}x{d['image_size']['height']})")
        print(f"  faces detected  : {d['num_faces_detected']}"
              + ("  (took largest)" if d['num_faces_detected'] > 1 else ""))
        b = d["bbox"]
        print(f"  bounding box    : ({b['x1']}, {b['y1']}) -> ({b['x2']}, {b['y2']})")
        print(f"  det score       : {d['det_score']}")
        print(f"  quality         : {d['quality']}"
              + ("" if res.usable else "   [LOW QUALITY]"))
        print(f"  embedding dim   : {d['embedding_dim']}  (kept local; never anchored)")
        print(f"  subject hash    : {d['subject_hash']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
