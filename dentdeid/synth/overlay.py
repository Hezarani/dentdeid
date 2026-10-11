"""Burned-in text layouts commonly seen on exported dental images (vendor-neutral)."""
from __future__ import annotations

import numpy as np
from PIL import Image, ImageDraw

from .names import Identity
from .render import Rendered, TextItem, draw_text

STYLES = ("header_strip", "corner_block", "footer_technique", "white_band", "colored_label", "markers",
          "over_anatomy")


def _fmt_date(yyyymmdd: str, rng) -> str:
    y, m, d = yyyymmdd[:4], yyyymmdd[4:6], yyyymmdd[6:8]
    return [f"{d}.{m}.{y}", f"{y}-{m}-{d}", f"{m}/{d}/{y}", f"{d}/{m}/{y}"][int(rng.integers(0, 4))]


def _fmt_time(hhmmss: str) -> str:
    return f"{hhmmss[:2]}:{hhmmss[2:4]}"


def apply_styles(r: Rendered, ident: Identity, rng: np.random.Generator, styles: list[str],
                 hard: bool = False) -> Rendered:
    """Draw the requested burned-in text styles onto ``r`` (returns a new Rendered).

    ``hard=True`` makes the text harder to find: smaller fonts, lower intensity, no
    protective black band, and (with ``over_anatomy``) text placed over the teeth/bone.
    """
    color_needed = "colored_label" in styles
    arr = r.image
    if color_needed and arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    im = Image.fromarray(arr)
    mode_rgb = im.mode == "RGB"
    h, w = arr.shape[:2]
    base = max(11, int(min(h, w) * rng.uniform(0.022, 0.034)))
    if hard:
        base = max(9, int(min(h, w) * rng.uniform(0.014, 0.020)))
    items: list[TextItem] = list(r.text)

    def white(v=None):
        if v is None:
            v = int(rng.integers(165, 205)) if hard else int(rng.integers(205, 256))
        return (v, v, v) if mode_rgb else v

    def black():
        return (0, 0, 0) if mode_rgb else 0

    if "header_strip" in styles:
        band = int(base * 2.2)
        if not hard:
            ImageDraw.Draw(im).rectangle((0, 0, w, band), fill=black())
        y = (band - base) // 2 - 1
        x = int(w * 0.02)
        it = draw_text(im, (x, y), ident.display_name, base, white(), items, True, "name")
        it2 = draw_text(im, (it.bbox[2] + base, y), f"DOB: {_fmt_date(ident.birth_date, rng)}", base, white(),
                        items, True, "dob")
        draw_text(im, (it2.bbox[2] + base, y), f"ID: {ident.patient_id}", base, white(), items, True, "id")
        right = f"{ident.clinic}  {_fmt_date(ident.exam_date, rng)} {_fmt_time(ident.exam_time)}"
        f_w = ImageDraw.Draw(im).textlength(right, font=__import__("PIL.ImageFont", fromlist=["x"]).load_default(size=base))
        draw_text(im, (int(w - f_w - w * 0.02), y), right, base, white(), items, True, "clinic")

    if "corner_block" in styles:
        size = max(11, int(base * 0.9))
        x, y = int(w * 0.02), int(h * 0.02) + (int(base * 2.4) if "header_strip" in styles or "white_band" in styles else 0)
        lines = [(ident.display_name, "name"), (f"Pat.ID {ident.patient_id}", "id"),
                 (f"Geb. {_fmt_date(ident.birth_date, rng)}", "dob"),
                 (f"{_fmt_date(ident.exam_date, rng)}  {_fmt_time(ident.exam_time)}", "date")]
        for text, cat in lines[: int(rng.integers(2, 5))]:
            it = draw_text(im, (x, y), text, size, white(), items, True, cat)
            y = it.bbox[3] + max(3, size // 3)

    if "footer_technique" in styles:
        size = max(11, int(base * 0.85))
        y = h - int(size * 1.8)
        tech = f"{int(rng.integers(60, 91))} kV  {int(rng.integers(4, 16))} mA  {rng.uniform(0.1, 16):.1f} s"
        draw_text(im, (int(w * 0.02), y), tech, size, white(), items, False, "technique")
        dt = f"{_fmt_date(ident.exam_date, rng)} {_fmt_time(ident.exam_time)}"
        f = __import__("PIL.ImageFont", fromlist=["x"]).load_default(size=size)
        tw = ImageDraw.Draw(im).textlength(dt, font=f)
        draw_text(im, (int(w - tw - w * 0.02), y), dt, size, white(), items, True, "date")

    if "white_band" in styles:
        band = int(base * 2.2)
        ImageDraw.Draw(im).rectangle((0, 0, w, band), fill=white(255))
        y = (band - base) // 2 - 1
        it = draw_text(im, (int(w * 0.015), y), f"Patient: {ident.given} {ident.family}", base, black(),
                       items, True, "name")
        draw_text(im, (it.bbox[2] + base * 2, y), f"{ident.patient_id}   {_fmt_date(ident.exam_date, rng)}",
                  base, black(), items, True, "id")

    if "colored_label" in styles and mode_rgb:
        d = ImageDraw.Draw(im)
        yellow = (255, 230, 0)
        x0, y0 = int(w * rng.uniform(0.35, 0.5)), int(h * rng.uniform(0.35, 0.55))
        x1 = x0 + int(w * 0.12)
        d.line([(x0, y0), (x1, y0 - int(h * 0.05))], fill=yellow, width=2)
        size = max(11, int(base * 0.85))
        draw_text(im, (x1 + 6, y0 - int(h * 0.05) - size), f"{rng.uniform(3, 25):.1f} mm", size, yellow, items,
                  False, "label")
        green = (60, 255, 90)
        draw_text(im, (int(w * 0.62), int(h * 0.20)), f"Pt: {ident.family} {ident.given[0]}.", size, green,
                  items, True, "name")

    if "markers" in styles:
        size = max(14, int(base * 1.3))
        draw_text(im, (int(w * 0.03), int(h * 0.45)), "R", size, white(250), items, False, "marker")
        draw_text(im, (int(w * 0.95), int(h * 0.45)), "L", size, white(250), items, False, "marker")

    if "over_anatomy" in styles:
        size = max(9, int(base * 0.9))
        x, y = int(w * rng.uniform(0.30, 0.55)), int(h * rng.uniform(0.35, 0.60))
        draw_text(im, (x, y), f"{ident.family} {ident.given}  {_fmt_date(ident.birth_date, rng)}", size, white(),
                  items, True, "name")

    out = np.asarray(im)
    return Rendered(out, r.kind, items, list(r.scales), list(r.confounders))


def cbct_labels(r: Rendered, ident: Identity, rng) -> Rendered:
    im = Image.fromarray(r.image)
    h, w = r.image.shape[:2]
    items: list[TextItem] = list(r.text)
    size = max(12, int(h * 0.02))
    pw = w // 2 - 12
    for (px, py), lab in zip([(8, 8), (w // 2 + 4, 8), (8, h // 2 + 4), (w // 2 + 4, h // 2 + 4)],
                             ["Axial", "Coronal", "Sagittal", "3D view"]):
        draw_text(im, (px + pw - 7 * size // 2 - 8, py + 6), lab, size, 230, items, False, "label")
    y = 16
    for text, cat in [(ident.display_name, "name"), (f"ID {ident.patient_id}", "id"),
                      (f"*{_fmt_date(ident.birth_date, rng)}", "dob"), (ident.clinic, "clinic")]:
        it = draw_text(im, (18, y), text, size, 240, items, True, cat)
        y = it.bbox[3] + 5
    draw_text(im, (w // 2 + 4 + pw - 170, h - 34), "W 4000 L 1000", size, 220, items, False, "technique")
    return Rendered(np.asarray(im), r.kind, items, list(r.scales), list(r.confounders))
