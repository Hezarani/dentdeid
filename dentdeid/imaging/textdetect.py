"""Burned-in text and colour-annotation detector for dental images.

A deterministic, dependency-light (NumPy only) detector designed around how text is burned
into dental radiographs and screenshots:

* **High-contrast thin strokes.** Burned-in characters are drawn at high intensity against a
  contrasting background and are only a few pixels thick. A pixel is a *bright-stroke*
  candidate if it is ≥ ``bright_min`` and its morphological **white top-hat** (the pixel minus
  the grey-level *opening* with a (2r+1)×(2r+1) square) is ≥ ``contrast``. The opening erases
  structures thinner than the window but preserves broad ones, so crowns, restorations,
  implants and brackets — the bright things in a dental radiograph — are suppressed while
  pen-stroke-thin glyphs survive. *Dark-stroke* candidates use the black top-hat (closing
  minus pixel) for dark text on a light header band.
* **Colour on a greyscale image.** Radiographs are greyscale, so strongly chromatic pixels in
  an otherwise grey image are overlays (measurements, labels, names).
* **Characters form lines.** Candidates are grouped into 8-connected components; components
  with character-like size and fill are chained into horizontal lines. Isolated bright blobs
  — metal restorations, implants, brackets joined by an arch-wire, root-canal fillings — do
  not form aligned rows of similar-height glyphs and are rejected.
* **Scales are not names.** Rows of evenly spaced thin ticks (ceph rulers, mm scales) are
  reported as ``scale`` so research users can keep calibration rulers if they wish.
* **Radiograph, screenshot or photograph.** :func:`image_class` tells a greyscale radiograph
  from a *screenshot* (a greyscale image with coloured software chrome — scanned like a
  radiograph, colour pass included) and a camera *photograph* (where natural texture makes
  automatic text redaction unreliable, so detections are only proposed for review).

Every step uses integer arithmetic only, so the JavaScript port in ``docs/detector.js``
(used by the zero-install web app) returns *identical* boxes; the test-suite checks parity.
The detector is intentionally conservative (it prefers redacting a false positive to missing
a name) and its output is always meant for **human review**.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np

__all__ = ["DetectParams", "Region", "detect_text", "to_gray", "components", "image_class", "merge_regions",
           "ALGORITHM_VERSION"]

ALGORITHM_VERSION = "dentdeid-text-3"  # bump when detector output can change


@dataclass(frozen=True)
class DetectParams:
    bright_min: int = 150          # bright-stroke pixels must be at least this bright …
    dark_max: int = 90             # … dark-stroke pixels at most this dark
    contrast: int = 80             # … and differ from the local extreme by at least this much
    radius: int = 3                # structuring element is a (2r+1)^2 square (strokes <= 2r px survive)
    chroma_min: int = 48           # colour-overlay pixel: max(R,G,B)-min(R,G,B) >= this
    gray_chroma: int = 24          # "greyscale image" test: pixels with chroma > this …
    gray_percent: int = 2          # … make up at most this percentage of the image
    min_h: int = 5                 # glyph height bounds (px)
    max_h_percent: int = 5         # max glyph height = max(16, min(H, W) * this / 100)
    min_pixels: int = 6
    max_fill_percent: int = 85     # glyphs wider than 3 px must not be solid blobs
    min_components: int = 3        # characters per accepted line
    margin_percent: int = 15       # margin band (top/bottom/left/right)
    detect_bright: bool = True
    detect_dark: bool = True
    detect_color: bool = True
    # A second, more sensitive stroke pass catches faint text drawn over anatomy; its regions
    # are merged with the first pass (on DentDeID-Synth: hard-case recall 66% -> 83%,
    # standard recall unchanged at 100%, false-positive area 0.11% -> 0.14%).
    sensitive_pass: bool = True
    sensitive_bright_min: int = 120
    sensitive_contrast: int = 50
    # image_class(): a colour image is a *screenshot* when at least this percentage of its
    # pixels is neutral grey (max-min <= neutral_chroma; a little tolerance for JPEG and colour
    # casts) and this percentage is neutral mid-grey (16 < luma < 240) — a radiograph panel
    # inside coloured software chrome — or when this percentage of its mid-tone pixels
    # (8 < luma < 250) sits in perfectly flat areas, as software backgrounds and panels do
    # (camera photographs have sensor noise; pure black/white backdrops are not counted).
    neutral_chroma: int = 8
    screenshot_neutral_percent: int = 50
    screenshot_mid_percent: int = 30
    screenshot_flat_percent: int = 40


@dataclass(frozen=True)
class Region:
    x0: int
    y0: int
    x1: int  # exclusive
    y1: int  # exclusive
    kind: str        # "text" | "scale"
    source: str      # "bright" | "dark" | "color" | "mixed"
    confidence: str  # "high" | "medium" | "low"
    n: int           # number of glyph components

    @property
    def area(self) -> int:
        return (self.x1 - self.x0) * (self.y1 - self.y0)

    def as_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------------------
# primitives
# ---------------------------------------------------------------------------------------
def to_gray(img: np.ndarray) -> tuple[np.ndarray, np.ndarray | None]:
    """Return ``(gray_uint8, chroma_uint8_or_None)`` using integer BT.601-style luma."""
    a = np.asarray(img)
    if a.dtype != np.uint8:
        raise TypeError("detect_text expects uint8 input; window/normalise first")
    if a.ndim == 2:
        return a, None
    if a.ndim == 3 and a.shape[2] >= 3:
        r = a[..., 0].astype(np.int32)
        g = a[..., 1].astype(np.int32)
        b = a[..., 2].astype(np.int32)
        gray = ((77 * r + 150 * g + 29 * b + 128) >> 8).astype(np.uint8)
        mx = np.maximum(np.maximum(r, g), b)
        mn = np.minimum(np.minimum(r, g), b)
        return gray, (mx - mn).astype(np.uint8)
    if a.ndim == 3 and a.shape[2] == 1:
        return a[..., 0], None
    raise ValueError(f"unsupported image shape {a.shape}")


def _local(gray: np.ndarray, r: int, fn) -> np.ndarray:
    """Separable (2r+1)x(2r+1) min or max filter with edge replication."""
    h, w = gray.shape
    p = np.pad(gray, ((0, 0), (r, r)), mode="edge")
    out = p[:, 0:w].copy()
    for k in range(1, 2 * r + 1):
        out = fn(out, p[:, k:k + w])
    p = np.pad(out, ((r, r), (0, 0)), mode="edge")
    out2 = p[0:h, :].copy()
    for k in range(1, 2 * r + 1):
        out2 = fn(out2, p[k:k + h, :])
    return out2


def components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """8-connected components of a boolean mask as ``(x0, y0, x1, y1, count)`` boxes
    (``x1``/``y1`` exclusive), sorted by ``(y0, x0, y1, x1)``. Run-length union-find."""
    h, w = mask.shape
    runs_x0: list[int] = []
    runs_x1: list[int] = []
    runs_y: list[int] = []
    row_start: list[int] = []
    m = np.asarray(mask, dtype=bool)
    for y in range(h):
        row_start.append(len(runs_y))
        row = m[y]
        if not row.any():
            continue
        d = np.diff(np.concatenate(([0], row.view(np.int8), [0])))
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        runs_x0.extend(starts.tolist())
        runs_x1.extend(ends.tolist())
        runs_y.extend([y] * len(starts))
    row_start.append(len(runs_y))
    n = len(runs_y)
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for y in range(1, h):
        a0, a1 = row_start[y - 1], row_start[y]
        b0, b1 = row_start[y], row_start[y + 1]
        i, j = a0, b0
        while i < a1 and j < b1:
            # runs [x0,x1) in consecutive rows touch (8-connectivity) iff c <= b and a <= d
            if runs_x0[j] <= runs_x1[i] and runs_x0[i] <= runs_x1[j]:
                ri, rj = find(i), find(j)
                if ri != rj:
                    if ri < rj:
                        parent[rj] = ri
                    else:
                        parent[ri] = rj
            if runs_x1[i] < runs_x1[j]:
                i += 1
            else:
                j += 1
    boxes: dict[int, list[int]] = {}
    for k in range(n):
        root = find(k)
        x0, x1, y = runs_x0[k], runs_x1[k], runs_y[k]
        b = boxes.get(root)
        if b is None:
            boxes[root] = [x0, y, x1, y + 1, x1 - x0]
        else:
            if x0 < b[0]:
                b[0] = x0
            if x1 > b[2]:
                b[2] = x1
            if y + 1 > b[3]:
                b[3] = y + 1
            b[4] += x1 - x0
    out = [tuple(b) for b in boxes.values()]
    out.sort(key=lambda b: (b[1], b[0], b[3], b[2]))
    return out  # type: ignore[return-value]


# ---------------------------------------------------------------------------------------
# grouping
# ---------------------------------------------------------------------------------------
def _glyphs(comps, max_h: int, p: DetectParams):
    out = []
    for (x0, y0, x1, y1, cnt) in comps:
        h = y1 - y0
        w = x1 - x0
        if h < p.min_h or h > max_h:
            continue
        if w > 4 * h + 4:
            continue
        if cnt < p.min_pixels:
            continue
        if w > 3 and 100 * cnt > p.max_fill_percent * w * h:
            continue
        out.append((x0, y0, x1, y1, cnt))
    return out


def _lines(glyphs, p: DetectParams):
    n = len(glyphs)
    order = sorted(range(n), key=lambda i: (glyphs[i][0], glyphs[i][1], glyphs[i][2], glyphs[i][3]))
    g = [glyphs[i] for i in order]
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    maxh_all = max((b[3] - b[1] for b in g), default=0)
    reach = (3 * maxh_all + 4) // 2 + 1
    for i in range(n):
        xi0, yi0, xi1, yi1, _ = g[i]
        hi = yi1 - yi0
        for j in range(i + 1, n):
            xj0, yj0, xj1, yj1, _ = g[j]
            if xj0 > xi1 + reach:
                break
            hj = yj1 - yj0
            hmin, hmax = (hi, hj) if hi <= hj else (hj, hi)
            if 2 * hmax > 5 * hmin:
                continue
            overlap = min(yi1, yj1) - max(yi0, yj0)
            if 2 * overlap < hmin:
                continue
            gap = xj0 - xi1
            if 2 * gap > 3 * hmax + 4:
                continue
            ri, rj = find(i), find(j)
            if ri != rj:
                parent[max(ri, rj)] = min(ri, rj)
    groups: dict[int, list[int]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)
    return [[g[i] for i in sorted(idx)] for _, idx in sorted(groups.items())]


def _is_scale(members) -> bool:
    if len(members) < 8:
        return False
    if any(b[2] - b[0] > 2 for b in members):
        return False
    xs = sorted(members, key=lambda b: b[0])
    gaps = [xs[k + 1][0] - xs[k][2] for k in range(len(xs) - 1)]
    if min(gaps) <= 0:
        return False
    s = sum(gaps)
    s2 = sum(x * x for x in gaps)
    m = len(gaps)
    # coefficient of variation <= 0.15  <=>  (m*s2 - s^2) * 400 <= 9 * s^2
    return (m * s2 - s * s) * 400 <= 9 * s * s


def _in_margin(x0, y0, x1, y1, w, h, pct) -> bool:
    mb_y = (h * pct) // 100
    mb_x = (w * pct) // 100
    cy2 = y0 + y1
    cx2 = x0 + x1
    return cy2 < 2 * mb_y or cy2 > 2 * (h - mb_y) or cx2 < 2 * mb_x or cx2 > 2 * (w - mb_x)


_CONF_RANK = {"low": 0, "medium": 1, "high": 2}


def _regions_from(mask, source: str, h: int, w: int, max_h: int, p: DetectParams,
                  allow_pairs: bool = True) -> list[Region]:
    comps = components(mask)
    gl = _glyphs(comps, max_h, p)
    out: list[Region] = []
    for members in _lines(gl, p):
        k = len(members)
        x0 = min(b[0] for b in members)
        y0 = min(b[1] for b in members)
        x1 = max(b[2] for b in members)
        y1 = max(b[3] for b in members)
        margin = _in_margin(x0, y0, x1, y1, w, h, p.margin_percent)
        if _is_scale(members):
            kind, conf = "scale", "high"
        elif k >= p.min_components:
            kind = "text"
            conf = "high" if (k >= 5 or margin) else "medium"
        elif allow_pairs and k == 2 and margin and all(b[0] > 0 and b[1] > 0 and b[2] < w and b[3] < h for b in members):
            # two glyphs in a margin (e.g. "ID"); anatomy cut off by the image edge is excluded
            kind, conf = "text", "low"
        else:
            continue
        hs = sorted(b[3] - b[1] for b in members)
        med = hs[(len(hs) - 1) // 2]
        pad = (med + 3) // 4 + 2
        out.append(Region(max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad),
                          kind, source, conf, k))
    return out


def _merge(regions: list[Region]) -> list[Region]:
    rs = sorted(regions, key=lambda r: (r.y0, r.x0, r.y1, r.x1, r.kind, r.source))
    changed = True
    while changed:
        changed = False
        for i in range(len(rs)):
            a = rs[i]
            for j in range(i + 1, len(rs)):
                b = rs[j]
                if a.kind != b.kind:
                    continue
                if a.x0 <= b.x1 and b.x0 <= a.x1 and a.y0 <= b.y1 and b.y0 <= a.y1:
                    conf = a.confidence if _CONF_RANK[a.confidence] >= _CONF_RANK[b.confidence] else b.confidence
                    rs[i] = Region(min(a.x0, b.x0), min(a.y0, b.y0), max(a.x1, b.x1), max(a.y1, b.y1),
                                   a.kind, a.source if a.source == b.source else "mixed", conf, a.n + b.n)
                    del rs[j]
                    changed = True
                    break
            if changed:
                break
    rs.sort(key=lambda r: (r.y0, r.x0, r.y1, r.x1, r.kind, r.source))
    return rs


def is_grayscale_like(chroma: np.ndarray | None, p: DetectParams = DetectParams()) -> bool:
    if chroma is None:
        return True
    n = chroma.size
    return int(np.count_nonzero(chroma > p.gray_chroma)) * 100 <= p.gray_percent * n


def _stroke_regions(gray, h, w, max_h, bright_min, dark_max, contrast, p: DetectParams,
                    allow_pairs: bool = True) -> list[Region]:
    g16 = gray.astype(np.int16)
    regions: list[Region] = []
    if p.detect_bright:
        opening = _local(_local(gray, p.radius, np.minimum), p.radius, np.maximum).astype(np.int16)
        m = (g16 >= bright_min) & (g16 - opening >= contrast)
        regions += _regions_from(m, "bright", h, w, max_h, p, allow_pairs)
    if p.detect_dark:
        closing = _local(_local(gray, p.radius, np.maximum), p.radius, np.minimum).astype(np.int16)
        m = (g16 <= dark_max) & (closing - g16 >= contrast)
        regions += _regions_from(m, "dark", h, w, max_h, p, allow_pairs)
    return regions


def image_class(img: np.ndarray, params: DetectParams | None = None) -> str:
    """``"radiograph"`` (greyscale), ``"screenshot"`` (greyscale content in coloured chrome)
    or ``"photo"`` (a colour photograph)."""
    p = params or DetectParams()
    gray, chroma = to_gray(img)
    if chroma is None or is_grayscale_like(chroma, p):
        return "radiograph"
    n = chroma.size
    neutral = chroma <= p.neutral_chroma
    mid = neutral & (gray > 16) & (gray < 240)
    if (int(np.count_nonzero(neutral)) * 100 >= p.screenshot_neutral_percent * n
            and int(np.count_nonzero(mid)) * 100 >= p.screenshot_mid_percent * n):
        return "screenshot"
    h, w = gray.shape
    if h > 1 and w > 1:
        g0, c0 = gray[:-1, :-1], chroma[:-1, :-1]
        flat = ((g0 == gray[:-1, 1:]) & (g0 == gray[1:, :-1]) & (c0 == chroma[:-1, 1:]) & (c0 == chroma[1:, :-1])
                & (g0 > 8) & (g0 < 250))
        if int(np.count_nonzero(flat)) * 100 >= p.screenshot_flat_percent * (h - 1) * (w - 1):
            return "screenshot"
    return "photo"


def merge_regions(regions: list[Region]) -> list[Region]:
    """Merge overlapping regions (e.g. found on several views of the same image)."""
    return _merge(list(regions))


def detect_text(img: np.ndarray, params: DetectParams | None = None, force_color: bool = False) -> list[Region]:
    """Detect burned-in text lines (and scales) in a uint8 greyscale or RGB image.

    The colour-overlay pass runs on greyscale-like images, or on any colour image when
    ``force_color`` is set (screenshots: coloured labels next to coloured software chrome).
    """
    p = params or DetectParams()
    gray, chroma = to_gray(img)
    h, w = gray.shape
    max_h = max(16, (min(h, w) * p.max_h_percent) // 100)
    regions = _stroke_regions(gray, h, w, max_h, p.bright_min, p.dark_max, p.contrast, p)
    if p.sensitive_pass:
        # the sensitive pass needs >= 3 aligned glyphs (no two-glyph margin rule): faint noise
        # specks near root apices must not become "text"
        regions += _stroke_regions(gray, h, w, max_h, p.sensitive_bright_min, p.dark_max,
                                   p.sensitive_contrast, p, allow_pairs=False)
    if p.detect_color and chroma is not None and (force_color or is_grayscale_like(chroma, p)):
        m = chroma >= p.chroma_min
        regions += _regions_from(m, "color", h, w, max_h, p)
    return _merge(regions)


def color_overlay_fraction(img: np.ndarray, p: DetectParams | None = None) -> float:
    """Fraction of strongly chromatic pixels (0 for greyscale input)."""
    p = p or DetectParams()
    _, chroma = to_gray(img)
    if chroma is None:
        return 0.0
    return float(np.count_nonzero(chroma >= p.chroma_min)) / chroma.size
