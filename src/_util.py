"""Small internal utilities (output hygiene for clean demo recordings)."""

from __future__ import annotations

import contextlib
import os
import sys


@contextlib.contextmanager
def suppress_native_stderr():
    """Silence C-library stderr (e.g. libpng "IDAT: incorrect data check" from
    OpenCV decoding slightly-corrupt candidate thumbnails) at the file-descriptor
    level, so a demo run shows clean staged output instead of raw decoder spew.

    Python-level logging is unaffected; only fd 2 is redirected for the block.
    """
    try:
        stderr_fd = sys.stderr.fileno()
    except (AttributeError, OSError):
        # No real fd (e.g. under pytest capture) -> nothing to suppress.
        yield
        return
    saved = os.dup(stderr_fd)
    devnull = os.open(os.devnull, os.O_WRONLY)
    try:
        os.dup2(devnull, stderr_fd)
        yield
    finally:
        os.dup2(saved, stderr_fd)
        os.close(saved)
        os.close(devnull)
