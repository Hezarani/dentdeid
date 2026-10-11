"""Procedural, clearly synthetic dental images.

These are *schematic* radiographs and photos. They are not realistic enough for clinical
AI training and don't try to be. Their purpose is to reproduce the things that matter for
de-identification:

* the **layouts** burned-in text appears in (header strips, corner blocks, footers,
  white screenshot bands, coloured measurement labels);
* the **confounders** a text detector must not mistake for writing: metal restorations,
  crowns, root-canal fillings, implants with threads, orthodontic brackets on an arch-wire,
  trabecular bone texture, calibration rulers.

Every drawing call is seeded, so a given ``(seed, kind)`` always produces identical pixels.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


@dataclass
class TextItem:
    text: str
    bbox: tuple[int, int, int, int]   # x0, y0, x1, y1 (exclusive)
    phi: bool
    category: str                     # name | dob | id | date | time | clinic | dentist | technique | label | marker | phone

    def as_dict(self) -> dict:
        return {"text": self.text, "bbox": list(self.bbox), "phi": self.phi, "category": self.category}


@dataclass
class Rendered:
    image: np.ndarray                         # uint8, HxW (grey) or HxWx3 (RGB)
    kind: str
    text: list[TextItem] = field(default_factory=list)
    scales: list[tuple[int, int, int, int]] = field(default_factory=list)
    confounders: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------
def _font(size: int) -> ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def _noise(rng, shape, std) -> np.ndarray:
    return rng.normal(0.0, std, shape)


def _mottle(rng, h, w, scale, std) -> np.ndarray:
    small = rng.normal(0.0, 1.0, (max(2, h // scale), max(2, w // scale)))
    im = Image.fromarray(((small - small.min()) / (np.ptp(small) + 1e-9) * 255).astype(np.uint8))
    im = im.resize((w, h), Image.BICUBIC).filter(ImageFilter.GaussianBlur(scale / 3))
    a = np.asarray(im, dtype=np.float64) / 255.0
    return (a - a.mean()) * std / (a.std() + 1e-9)


def _finish(layer: Image.Image, rng, blur: float, noise: float, mottle: float = 0.02) -> np.ndarray:
    im = layer.filter(ImageFilter.GaussianBlur(blur)) if blur > 0 else layer
    a = np.asarray(im, dtype=np.float64) / 255.0
    h, w = a.shape
    a = a + _mottle(rng, h, w, 24, mottle) + _noise(rng, a.shape, noise)
    return np.clip(a * 255.0 + 0.5, 0, 255).astype(np.uint8)


def draw_text(img: Image.Image, xy, text: str, size: int, fill, items: list[TextItem] | None,
              phi: bool, category: str) -> TextItem:
    d = ImageDraw.Draw(img)
    f = _font(size)
    d.text(xy, text, font=f, fill=fill)
    x0, y0, x1, y1 = d.textbbox(xy, text, font=f)
    it = TextItem(text, (int(x0), int(y0), int(x1), int(y1)), phi, category)
    if items is not None:
        items.append(it)
    return it


def _g(v: float) -> int:
    return int(max(0, min(255, round(v * 255))))


# ---------------------------------------------------------------------------------------
# teeth
# ---------------------------------------------------------------------------------------
def _tooth(d: ImageDraw.ImageDraw, cx: float, occlusal_y: float, width: float, crown_h: float,
           root_len: float, up: bool, rng, feats: list[str], allow: set[str]) -> tuple[float, float, float, float]:
    """Draw one tooth whose occlusal surface is at ``occlusal_y``; ``up`` = roots extend upward
    (a maxillary tooth). Returns the crown box."""
    s = -1 if up else 1   # direction from occlusal surface into the jaw
    cw = width * 0.92
    # root(s)
    n_roots = 2 if width > 0.8 * 60 and rng.random() < 0.6 else 1
    for k in range(n_roots):
        off = 0 if n_roots == 1 else (-0.22 if k == 0 else 0.22) * width
        top = occlusal_y + s * crown_h * 0.9
        tip = occlusal_y + s * (crown_h + root_len)
        rw = width * (0.55 if n_roots == 1 else 0.32)
        poly = [(cx + off - rw / 2, top), (cx + off + rw / 2, top), (cx + off + rw * 0.12, tip),
                (cx + off - rw * 0.12, tip)]
        d.polygon(poly, fill=_g(0.60 + rng.uniform(-0.03, 0.03)))
        # canal
        d.line([(cx + off, top), (cx + off, tip - s * 4)], fill=_g(0.40), width=max(1, int(width * 0.06)))
        if "rct" in allow and rng.random() < 0.18:
            d.line([(cx + off, top), (cx + off, tip - s * 3)], fill=_g(0.985), width=max(2, int(width * 0.07)))
            feats.append("root_canal_filling")
    # crown
    y_occ, y_cej = occlusal_y, occlusal_y + s * crown_h
    box = (cx - cw / 2, min(y_occ, y_cej), cx + cw / 2, max(y_occ, y_cej))
    d.rounded_rectangle(box, radius=int(cw * 0.3), fill=_g(0.80 + rng.uniform(-0.03, 0.03)))
    ebox = (box[0] + 2, box[1] + (0 if not up else 0), box[2] - 2, box[3])
    inner = (cx - cw * 0.28, min(y_occ + s * crown_h * 0.35, y_cej), cx + cw * 0.28,
             max(y_occ + s * crown_h * 0.35, y_cej))
    d.rounded_rectangle(inner, radius=int(cw * 0.15), fill=_g(0.47))  # pulp chamber
    del ebox
    r = rng.random()
    if "restoration" in allow and r < 0.22:
        rb = (cx - cw * 0.3, min(y_occ, y_occ + s * crown_h * 0.45), cx + cw * 0.25,
              max(y_occ, y_occ + s * crown_h * 0.45))
        d.rounded_rectangle(rb, radius=3, fill=_g(0.995))
        feats.append("metal_restoration")
    elif "crown" in allow and r < 0.30:
        d.rounded_rectangle(box, radius=int(cw * 0.3), outline=_g(0.99), width=max(3, int(cw * 0.12)))
        feats.append("prosthetic_crown")
    return box


def _implant(d, cx, occlusal_y, width, length, up, feats):
    s = -1 if up else 1
    top = occlusal_y + s * width * 0.6
    tip = top + s * length
    w = width * 0.42
    y0, y1 = min(top, tip), max(top, tip)
    d.rectangle((cx - w / 2, y0, cx + w / 2, y1), fill=_g(0.99))
    step = 6
    y = y0 + 4
    while y < y1 - 4:  # threads: small notches on both sides
        d.rectangle((cx - w / 2 - 3, y, cx - w / 2, y + 2), fill=_g(0.99))
        d.rectangle((cx + w / 2, y, cx + w / 2 + 3, y + 2), fill=_g(0.99))
        y += step
    # abutment + crown
    cb = (cx - width * 0.45, min(occlusal_y, top), cx + width * 0.45, max(occlusal_y, top))
    d.rounded_rectangle(cb, radius=6, fill=_g(0.97))
    feats.append("implant")


# ---------------------------------------------------------------------------------------
# radiograph kinds
# ---------------------------------------------------------------------------------------
def panoramic(rng, w: int = 1400, h: int = 700, allow: set[str] | None = None) -> Rendered:
    allow = allow if allow is not None else {"restoration", "crown", "rct", "implant", "ortho"}
    base = Image.new("L", (w, h), _g(0.10))
    d = ImageDraw.Draw(base)
    cx, cy = w / 2, h * 0.52
    # jaw bone (broad soft bands)
    bone = Image.new("L", (w, h), 0)
    bd = ImageDraw.Draw(bone)
    xs = np.linspace(w * 0.06, w * 0.94, 60)
    lower = [(x, cy + h * 0.16 + (x - cx) ** 2 / (w * w) * h * -0.55 + h * 0.05) for x in xs]
    upper = [(x, cy - h * 0.18 + (x - cx) ** 2 / (w * w) * h * 0.35) for x in xs]
    bd.line(lower, fill=_g(0.30), width=int(h * 0.26))
    bd.line(upper, fill=_g(0.26), width=int(h * 0.22))
    bd.rectangle((w * 0.05, h * 0.18, w * 0.13, h * 0.85), fill=_g(0.24))  # rami
    bd.rectangle((w * 0.87, h * 0.18, w * 0.95, h * 0.85), fill=_g(0.24))
    bone = bone.filter(ImageFilter.GaussianBlur(h * 0.05))
    base = Image.fromarray(np.maximum(np.asarray(base), np.asarray(bone)))
    d = ImageDraw.Draw(base)
    # spine shadows at the far edges
    for x in (w * 0.015, w * 0.985):
        d.rectangle((x - w * 0.012, h * 0.1, x + w * 0.012, h * 0.95), fill=_g(0.18))
    feats: list[str] = []
    n = 14
    tw = (w * 0.62) / n
    crown_h, root_len = h * 0.085, h * 0.17
    occl = lambda x: cy + (x - cx) ** 2 / (w * w) * h * 0.25  # noqa: E731 (smile curve)
    ortho = "ortho" in allow and rng.random() < 0.25
    implant_at = int(rng.integers(0, n)) if ("implant" in allow and rng.random() < 0.4) else -1
    wire_up, wire_lo = [], []
    for arch_up in (False, True):  # lower teeth (roots down) then upper teeth (roots up)
        for i in range(n):
            if rng.random() < 0.06:
                continue  # missing tooth
            x = w * 0.19 + (i + 0.5) * tw
            width = tw * (1.15 if i < 3 or i >= n - 3 else 0.9)
            oy = occl(x) + (-4 if arch_up else 4)   # upper crowns above, lower crowns below the plane
            if arch_up and i == implant_at:
                _implant(d, x, oy, width, root_len, True, feats)
                continue
            box = _tooth(d, x, oy, width, crown_h, root_len, arch_up, rng, feats, allow)
            if ortho:
                by = (box[1] + box[3]) / 2
                d.rectangle((x - width * 0.18, by - width * 0.14, x + width * 0.18, by + width * 0.14), fill=_g(0.99))
                (wire_lo if arch_up else wire_up).append((x, by))
    if ortho:
        for wire in (wire_up, wire_lo):
            if len(wire) > 1:
                d.line(wire, fill=_g(0.97), width=3)
        feats.append("orthodontic_brackets_and_wire")
    img = _finish(base, rng, blur=1.6, noise=0.022, mottle=0.03)
    return Rendered(img, "panoramic", confounders=sorted(set(feats)))


def periapical(rng, w: int = 480, h: int = 640, allow: set[str] | None = None) -> Rendered:
    allow = allow if allow is not None else {"restoration", "crown", "rct"}
    tex = _mottle(rng, h, w, 6, 1.0)
    trab = (tex > 0.3).astype(np.float64) * 0.10
    a = 0.30 + trab + _mottle(rng, h, w, 40, 0.04)
    base = Image.fromarray(np.clip(a * 255, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(base)
    feats: list[str] = []
    upper = rng.random() < 0.5
    n = int(rng.integers(2, 4))
    tw = w / (n + 0.4)
    for i in range(n):
        x = tw * (i + 0.7)
        if upper:   # maxillary teeth: crowns at the bottom, roots up
            _tooth(d, x, h * 0.64, tw * 0.9, h * 0.22, h * 0.45, True, rng, feats, allow)
        else:       # mandibular teeth: crowns at the top, roots down
            _tooth(d, x, h * 0.36, tw * 0.9, h * 0.22, h * 0.45, False, rng, feats, allow)
    # dark air gap at the occlusal side
    if upper:
        d.rectangle((0, h * 0.64, w, h), fill=_g(0.06))
    else:
        d.rectangle((0, 0, w, h * 0.36), fill=_g(0.06))
    img = _finish(base, rng, blur=1.3, noise=0.02, mottle=0.02)
    return Rendered(img, "periapical", confounders=sorted(set(feats)))


def bitewing(rng, w: int = 640, h: int = 480) -> Rendered:
    base = Image.new("L", (w, h), _g(0.12))
    d = ImageDraw.Draw(base)
    feats: list[str] = []
    n = 4
    tw = w / (n + 0.3)
    for i in range(n):
        x = tw * (i + 0.65)
        _tooth(d, x, h * 0.49, tw * 0.95, h * 0.28, h * 0.30, False, rng, feats, {"restoration", "crown"})
        _tooth(d, x + tw * 0.08, h * 0.51, tw * 0.95, h * 0.28, h * 0.30, True, rng, feats, {"restoration", "crown"})
    img = _finish(base, rng, blur=1.2, noise=0.02)
    return Rendered(img, "bitewing", confounders=sorted(set(feats)))


def cephalometric(rng, w: int = 1000, h: int = 820) -> Rendered:
    base = Image.new("L", (w, h), _g(0.08))
    d = ImageDraw.Draw(base)
    # soft-tissue profile + skull
    d.ellipse((w * 0.12, h * 0.05, w * 0.78, h * 0.80), fill=_g(0.22))
    d.polygon([(w * 0.70, h * 0.35), (w * 0.84, h * 0.48), (w * 0.76, h * 0.52), (w * 0.80, h * 0.66),
               (w * 0.74, h * 0.78), (w * 0.60, h * 0.92), (w * 0.45, h * 0.86)], fill=_g(0.22))
    d.ellipse((w * 0.16, h * 0.08, w * 0.72, h * 0.62), outline=_g(0.72), width=int(w * 0.018))
    d.polygon([(w * 0.46, h * 0.55), (w * 0.70, h * 0.62), (w * 0.66, h * 0.80), (w * 0.50, h * 0.78)], fill=_g(0.45))
    for k in range(5):  # cervical vertebrae
        y = h * (0.62 + k * 0.07)
        d.rectangle((w * 0.28, y, w * 0.36, y + h * 0.05), fill=_g(0.42))
    feats = ["skull_outline"]
    for i in range(6):
        _tooth(d, w * (0.56 + i * 0.022), h * 0.69, w * 0.02, h * 0.05, h * 0.09, False, rng, feats, {"restoration"})
    img = _finish(base, rng, blur=1.8, noise=0.02, mottle=0.03)
    # calibration ruler (ticks every 8 px; long ticks every 5th) — drawn after blur, like a real
    # software overlay/physical ruler edge
    pim = Image.fromarray(img)
    rd = ImageDraw.Draw(pim)
    x0, y0 = int(w * 0.86), int(h * 0.06)
    n_ticks = 21
    for k in range(n_ticks):
        L = 16 if k % 5 == 0 else 9
        rd.line([(x0 + k * 5, y0), (x0 + k * 5, y0 + L)], fill=235, width=1)
    rd.line([(x0, y0), (x0 + (n_ticks - 1) * 5, y0)], fill=235, width=1)
    scale_box = (x0 - 1, y0, x0 + (n_ticks - 1) * 5 + 2, y0 + 17)
    return Rendered(np.asarray(pim), "cephalometric", scales=[scale_box], confounders=sorted(set(feats)) + ["ruler"])


def cbct_screenshot(rng, w: int = 1280, h: int = 800) -> Rendered:
    base = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(base)
    pw, ph = w // 2 - 12, h // 2 - 12
    panels = [(8, 8), (w // 2 + 4, 8), (8, h // 2 + 4), (w // 2 + 4, h // 2 + 4)]
    for (px, py) in panels:
        d.rectangle((px, py, px + pw, py + ph), fill=_g(0.05), outline=_g(0.35), width=1)
    # axial: bone ring with teeth
    ax, ay = panels[0]
    d.ellipse((ax + pw * 0.2, ay + ph * 0.12, ax + pw * 0.8, ay + ph * 0.92), outline=_g(0.55), width=int(pw * 0.05))
    for k in range(12):
        t = np.pi * (0.1 + 0.8 * k / 11)
        tx = ax + pw / 2 - np.cos(t) * pw * 0.27
        ty = ay + ph * 0.55 - np.sin(t) * ph * 0.36
        d.ellipse((tx - 9, ty - 9, tx + 9, ty + 9), fill=_g(0.85))
    # coronal / sagittal: jaw silhouettes
    cxp, cyp = panels[1]
    d.polygon([(cxp + pw * 0.2, cyp + ph * 0.3), (cxp + pw * 0.8, cyp + ph * 0.3), (cxp + pw * 0.7, cyp + ph * 0.85),
               (cxp + pw * 0.3, cyp + ph * 0.85)], fill=_g(0.42))
    sx, sy = panels[2]
    d.ellipse((sx + pw * 0.15, sy + ph * 0.1, sx + pw * 0.85, sy + ph * 0.9), fill=_g(0.30))
    d.rectangle((sx + pw * 0.55, sy + ph * 0.45, sx + pw * 0.62, sy + ph * 0.8), fill=_g(0.85))
    rx, ry = panels[3]
    d.ellipse((rx + pw * 0.25, ry + ph * 0.1, rx + pw * 0.75, ry + ph * 0.9), fill=_g(0.60))
    img = _finish(base, rng, blur=1.0, noise=0.012, mottle=0.01)
    return Rendered(img, "cbct_screenshot", confounders=["panel_borders", "multi_view_layout"])


# ---------------------------------------------------------------------------------------
# synthetic intra-oral photograph (colour)
# ---------------------------------------------------------------------------------------
def intraoral_photo(rng, w: int = 900, h: int = 600) -> Rendered:
    im = Image.new("RGB", (w, h), (18, 14, 14))
    d = ImageDraw.Draw(im)
    d.ellipse((w * 0.04, h * 0.08, w * 0.96, h * 0.95), fill=(150, 40, 45))        # lips / retracted cheeks
    d.ellipse((w * 0.10, h * 0.18, w * 0.90, h * 0.86), fill=(60, 18, 22))         # oral cavity
    d.rectangle((w * 0.12, h * 0.26, w * 0.88, h * 0.46), fill=(215, 120, 125))    # upper gingiva
    d.rectangle((w * 0.12, h * 0.58, w * 0.88, h * 0.76), fill=(210, 115, 120))    # lower gingiva
    n = 10
    tw = (w * 0.72) / n
    for i in range(n):
        x = w * 0.14 + i * tw
        shade = int(rng.integers(215, 240))
        d.rounded_rectangle((x + 2, h * 0.34, x + tw - 2, h * 0.52), radius=int(tw * 0.25),
                            fill=(shade, shade - 6, shade - 25))
        d.rounded_rectangle((x + 4, h * 0.52, x + tw - 4, h * 0.66), radius=int(tw * 0.22),
                            fill=(shade - 8, shade - 14, shade - 32))
    im = im.filter(ImageFilter.GaussianBlur(1.2))
    a = np.asarray(im, dtype=np.float64) + rng.normal(0, 3.0, (h, w, 3))
    return Rendered(np.clip(a, 0, 255).astype(np.uint8), "intraoral_photo", confounders=["colour_photo"])


RADIOGRAPHS = {
    "panoramic": panoramic,
    "periapical": periapical,
    "bitewing": bitewing,
    "cephalometric": cephalometric,
    "cbct_screenshot": cbct_screenshot,
}
