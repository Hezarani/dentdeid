"""Optional OCR (Tesseract via ``pytesseract``; install ``dentdeid[ocr]`` + the Tesseract binary).

OCR is used in three places, never as the only safeguard:

* **finding extra text** the geometric detector missed (union of both, so OCR can only add
  redactions, never remove them under the default policy);
* the ``phi-only`` policy: a detected region may be *kept* only if OCR reads it confidently
  and every token is a known-safe technical token (``R``, ``L``, ``kV``, ``mA``, ``mm``, short
  numbers …). Anything unreadable, unknown or identifying is redacted;
* **verification**: the cleaned output is OCR'd and searched for the harvested identifiers.

The OCR'd strings themselves are never written to reports.
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np

from .textdetect import Region


@lru_cache(maxsize=1)
def available() -> bool:
    try:
        import pytesseract
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def _prep(gray8: np.ndarray, scale: int) -> np.ndarray:
    from PIL import Image
    im = Image.fromarray(gray8)
    if scale > 1:
        im = im.resize((im.width * scale, im.height * scale), Image.LANCZOS)
    return np.asarray(im)


def read_region(gray8: np.ndarray, r: Region) -> tuple[str, float]:
    """OCR one region. Returns ``(text, mean_confidence_0_100)``."""
    import pytesseract
    crop = gray8[r.y0:r.y1, r.x0:r.x1]
    if crop.size == 0:
        return "", 0.0
    # Tesseract prefers dark text on light background.
    if crop.mean() < 128:
        crop = 255 - crop
    crop = np.pad(crop, 8, mode="constant", constant_values=255)
    img = _prep(crop, 3 if crop.shape[0] < 60 else 2)
    d = pytesseract.image_to_data(img, config="--psm 6", output_type=pytesseract.Output.DICT)
    words, confs = [], []
    for t, c in zip(d["text"], d["conf"]):
        t = (t or "").strip()
        try:
            c = float(c)
        except (TypeError, ValueError):
            c = -1
        if t and c >= 0:
            words.append(t)
            confs.append(c)
    return " ".join(words), (float(np.mean(confs)) if confs else 0.0)


def plausible_word_box(text: str, box: tuple[int, int, int, int], shape: tuple[int, ...]) -> bool:
    """Reject OCR artefacts: a word box must be glyph-sized and about as wide as its characters."""
    x0, y0, x1, y1 = box
    h, w = y1 - y0, x1 - x0
    H, W = shape[0], shape[1]
    if h <= 0 or w <= 0:
        return False
    if h > max(24, (min(H, W) * 8) // 100) or w > W // 2:
        return False
    n = sum(ch.isalnum() for ch in text)
    return n >= 2 and w <= (len(text) + 1) * h * 2


def find_words(gray8: np.ndarray, min_conf: float = 60.0) -> list[tuple[str, float, tuple[int, int, int, int]]]:
    """Whole-image sparse-text OCR (both polarities). Returns ``[(text, conf, (x0,y0,x1,y1))]``.

    Only word-level results (Tesseract level 5) with plausible, glyph-sized boxes are kept, so
    OCR can add word-sized redactions but never a runaway page-sized box.
    """
    import pytesseract
    out = []
    scale = 2 if max(gray8.shape) < 1000 else 1
    for img in (gray8, 255 - gray8):
        big = _prep(img, scale)
        d = pytesseract.image_to_data(big, config="--psm 11", output_type=pytesseract.Output.DICT)
        for i, t in enumerate(d["text"]):
            t = (t or "").strip()
            if int(d.get("level", [5] * len(d["text"]))[i]) != 5:
                continue
            try:
                c = float(d["conf"][i])
            except (TypeError, ValueError):
                continue
            if len(t) >= 2 and c >= min_conf and any(ch.isalnum() for ch in t):
                x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
                box = (x // scale, y // scale, (x + w) // scale + 1, (y + h) // scale + 1)
                if plausible_word_box(t, box, gray8.shape):
                    out.append((t, c, box))
    return out
