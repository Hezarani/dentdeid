"""Changing stored pixel values inside regions (PS3.15: overlays/shutters are not enough)."""
from __future__ import annotations

from typing import Iterable

import numpy as np

from .textdetect import Region


def box_union_mask(shape: tuple[int, int], boxes: Iterable[tuple[int, int, int, int]]) -> np.ndarray:
    m = np.zeros(shape, dtype=bool)
    h, w = shape
    for x0, y0, x1, y1 in boxes:
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
        if x1 > x0 and y1 > y0:
            m[y0:y1, x0:x1] = True
    return m


def fill_boxes(arr: np.ndarray, boxes: Iterable[tuple[int, int, int, int]], value) -> np.ndarray:
    """Return a copy of ``arr`` (HxW, HxWxC, or FxHxW[xC]) with each box set to ``value``."""
    out = np.array(arr, copy=True)
    for x0, y0, x1, y1 in boxes:
        if out.ndim == 2:
            h, w = out.shape
            out[max(0, y0):min(h, y1), max(0, x0):min(w, x1)] = value
        elif out.ndim == 3 and out.shape[-1] in (3, 4) and out.shape[0] > 4:
            h, w = out.shape[:2]
            out[max(0, y0):min(h, y1), max(0, x0):min(w, x1), ...] = value
        else:  # frames first
            h, w = out.shape[1:3]
            out[:, max(0, y0):min(h, y1), max(0, x0):min(w, x1), ...] = value
    return out


def region_boxes(regions: Iterable[Region], keep_scales: bool = False) -> list[tuple[int, int, int, int]]:
    return [(r.x0, r.y0, r.x1, r.y1) for r in regions if not (keep_scales and r.kind == "scale")]


def pixelate_blur(rgb: np.ndarray, box: tuple[int, int, int, int], block: int | None = None,
                  contrast: float = 0.35) -> None:
    """Irreversibly obscure ``box`` in place: coarse pixelation, box-mean smoothing, and a
    contrast collapse toward the region mean.

    Pixelation first destroys high-frequency detail (so de-blurring attacks have nothing to
    recover); smoothing removes the blocky edges; flattening the remaining contrast removes the
    coarse light/dark layout (eye sockets vs. cheeks) that face detectors — and people — use.
    """
    x0, y0, x1, y1 = box
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return
    roi = rgb[y0:y1, x0:x1].astype(np.float64)
    bh, bw = roi.shape[:2]
    b = block or max(6, min(bh, bw) // 4)
    for yy in range(0, bh, b):
        for xx in range(0, bw, b):
            cell = roi[yy:yy + b, xx:xx + b]
            cell[...] = cell.mean(axis=(0, 1))
    # separable box smoothing (k ~ b)
    k = max(3, b | 1)
    pad = k // 2
    for axis in (0, 1):
        widths = [(0, 0)] * roi.ndim
        widths[axis] = (pad, pad)
        p = np.pad(roi, widths, mode="edge")
        c = np.cumsum(p, axis=axis, dtype=np.float64)
        c = np.concatenate([np.zeros_like(np.take(c, [0], axis=axis)), c], axis=axis)
        n = roi.shape[axis]
        roi = (np.take(c, range(k, k + n), axis=axis) - np.take(c, range(0, n), axis=axis)) / k
    mean = roi.mean(axis=(0, 1), keepdims=True)
    roi = mean + (roi - mean) * contrast
    rgb[y0:y1, x0:x1] = np.clip(roi + 0.5, 0, 255).astype(rgb.dtype)
