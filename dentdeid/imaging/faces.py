"""Faces in extra-oral photographs (orthodontic / aesthetic records).

Detection uses OpenCV's bundled Haar cascades (frontal + profile, both directions). Actions:

* ``blur``  — irreversible pixelate-then-smooth over a generously expanded face box;
* ``fill``  — solid black box;
* ``crop-perioral`` — crop the photograph to the perioral region (below the nose to the chin).
  For smile/aesthetic cases this keeps what the dentist needs and removes the eyes, brows
  and nose — the features people use to recognise faces. Preston et al. (2022) found black
  boxes over facial features left 55.5% of faces recognisable, while progressive cropping
  delayed recognition far longer. Lips and teeth can still be distinctive; see the docs.
* ``off`` — report only.

OpenCV (``opencv-python-headless``) is a core dependency; if it cannot be imported, colour
photographs fail closed.

Black bars over the eyes are deliberately **not** offered: the evidence says they do not
de-identify.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

from .redact import pixelate_blur

FACE_ACTIONS = ("blur", "fill", "crop-perioral", "off")


@dataclass(frozen=True)
class Face:
    x0: int
    y0: int
    x1: int
    y1: int
    view: str  # "frontal" | "profile"

    @property
    def w(self) -> int:
        return self.x1 - self.x0

    @property
    def h(self) -> int:
        return self.y1 - self.y0

    def as_dict(self) -> dict:
        return asdict(self)


_CASCADES = ("haarcascade_frontalface_default.xml", "haarcascade_frontalface_alt2.xml",
             "haarcascade_profileface.xml")


def available() -> bool:
    """True only if OpenCV *and* its Haar cascade files are usable.

    (OpenCV 5 removed ``CascadeClassifier`` from the main package; importing ``cv2`` alone
    is therefore not enough — a photo must never be passed as 'checked for faces' when the
    detector cannot run.)
    """
    try:
        import os

        import cv2
        if not hasattr(cv2, "CascadeClassifier"):
            return False
        base = getattr(getattr(cv2, "data", None), "haarcascades", "")
        return bool(base) and all(os.path.isfile(os.path.join(base, n)) for n in _CASCADES)
    except Exception:  # pragma: no cover - depends on environment
        return False


def _iou(a, b) -> float:
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if not inter:
        return 0.0
    ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / ua


def detect_faces(rgb: np.ndarray, min_rel: float = 0.08) -> list[Face]:
    """Detect faces with Haar cascades. Raises ``RuntimeError`` if OpenCV is missing."""
    try:
        import cv2
    except Exception as e:  # pragma: no cover
        raise RuntimeError("face detection needs OpenCV (opencv-python-headless)") from e
    a = np.asarray(rgb)
    gray = a if a.ndim == 2 else cv2.cvtColor(a[..., :3], cv2.COLOR_RGB2GRAY)
    h, w = gray.shape
    scale = min(1.0, 900.0 / max(h, w))
    small = cv2.resize(gray, (int(w * scale), int(h * scale)), interpolation=cv2.INTER_AREA) if scale < 1 else gray
    small = cv2.equalizeHist(small)
    ms = max(24, int(min(small.shape) * min_rel))
    found: list[tuple[int, int, int, int, str]] = []
    base = cv2.data.haarcascades
    for name, view in ((_CASCADES[0], "frontal"), (_CASCADES[1], "frontal"), (_CASCADES[2], "profile")):
        casc = cv2.CascadeClassifier(base + name)
        if casc.empty():
            raise RuntimeError(f"Haar cascade {name} could not be loaded")
        for flip in ((False, True) if view == "profile" else (False,)):
            img = cv2.flip(small, 1) if flip else small
            rects = casc.detectMultiScale(img, scaleFactor=1.1, minNeighbors=6, minSize=(ms, ms))
            for (x, y, fw, fh) in (rects if len(rects) else []):
                if flip:
                    x = img.shape[1] - x - fw
                found.append((int(x / scale), int(y / scale), int((x + fw) / scale), int((y + fh) / scale), view))
    merged: list[list] = []
    for f in sorted(found, key=lambda t: -(t[2] - t[0]) * (t[3] - t[1])):
        for m in merged:
            if _iou(f, m) > 0.25:
                m[0], m[1] = min(m[0], f[0]), min(m[1], f[1])
                m[2], m[3] = max(m[2], f[2]), max(m[3], f[3])
                break
        else:
            merged.append(list(f))
    return [Face(*m) for m in sorted(merged, key=lambda m: (m[1], m[0]))]


def expand(face: Face, shape) -> tuple[int, int, int, int]:
    """Cover the whole head: Haar boxes are tight around eyes–mouth."""
    h, w = shape[:2]
    return (max(0, int(face.x0 - 0.20 * face.w)), max(0, int(face.y0 - 0.35 * face.h)),
            min(w, int(face.x1 + 0.20 * face.w)), min(h, int(face.y1 + 0.25 * face.h)))


def perioral_box(face: Face, shape) -> tuple[int, int, int, int]:
    """Region from just below the nose to just below the chin."""
    h, w = shape[:2]
    return (max(0, int(face.x0 + 0.12 * face.w)), max(0, int(face.y0 + 0.66 * face.h)),
            min(w, int(face.x1 - 0.12 * face.w)), min(h, int(face.y1 + 0.12 * face.h)))


def apply_face_action(rgb: np.ndarray, faces: list[Face], action: str) -> tuple[np.ndarray, list[tuple[int, int, int, int]]]:
    """Apply ``action``; returns ``(new_image, boxes_changed)``. ``crop-perioral`` returns the crop."""
    if action not in FACE_ACTIONS:
        raise ValueError(f"face action must be one of {FACE_ACTIONS}")
    out = np.array(rgb, copy=True)
    if not faces or action == "off":
        return out, []
    if action == "crop-perioral":
        main = max(faces, key=lambda f: f.w * f.h)
        x0, y0, x1, y1 = perioral_box(main, out.shape)
        # obscure any *other* face that would fall inside the crop
        for f in faces:
            if f is not main:
                pixelate_blur(out, expand(f, out.shape))
        return out[y0:y1, x0:x1].copy(), [(x0, y0, x1, y1)]
    boxes = [expand(f, out.shape) for f in faces]
    for b in boxes:
        if action == "blur":
            pixelate_blur(out, b)
        else:
            out[b[1]:b[3], b[0]:b[2]] = 0
    return out, boxes
