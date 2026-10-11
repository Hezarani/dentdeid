"""Ordinary image files (JPEG / PNG / TIFF / BMP / WebP / GIF) — the way most dental images
actually travel: exports from practice software, screenshots and phone photos.

Hidden identifiers live in: EXIF (camera serial numbers, capture time, *GPS position*,
Artist, ImageDescription, UserComment), XMP packets, IPTC, JPEG comments, PNG text chunks
(``tEXt``/``iTXt``/``zTXt``), TIFF tags, and the **file name**. DentDeID never edits metadata
in place: it decodes the pixels, applies the EXIF orientation (so the picture still looks
right once the orientation tag is gone), and writes a brand-new file containing pixels only.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import ExifTags, Image, ImageOps

RASTER_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif", ".jfif"}
# Keys Pillow may report on a freshly written file that carry no identifying information.
HARMLESS_INFO_KEYS = {"jfif", "jfif_version", "jfif_unit", "jfif_density", "dpi", "progressive",
                      "progression", "adobe", "adobe_transform", "compression", "interlace", "gamma",
                      "aspect", "loop", "duration", "background", "transparency", "lossless", "resolution",
                      "srgb"}
_TEXT_TAGS = {"ImageDescription", "Artist", "Copyright", "UserComment", "XPComment", "XPAuthor", "XPTitle",
              "XPSubject", "XPKeywords", "DocumentName", "PageName", "HostComputer", "CameraOwnerName",
              "BodySerialNumber", "LensSerialNumber", "Software", "Make", "Model", "ImageUniqueID"}
# Free-text tags a person types (patient names end up here); their values are added to the
# identifiers searched for in the burned-in text and the file name.
PERSONAL_TEXT_TAGS = {"ImageDescription", "Artist", "Copyright", "UserComment", "XPComment", "XPAuthor",
                      "XPTitle", "XPSubject", "XPKeywords", "DocumentName", "PageName", "CameraOwnerName"}


def _tag_name(tag: int) -> str:
    return ExifTags.TAGS.get(tag, f"0x{tag:04X}")


def inventory(im: Image.Image) -> dict:
    """What metadata a file carries — names and counts only, never values."""
    exif = im.getexif()
    tags = sorted({_tag_name(t) for t in exif})
    sub = []
    gps = False
    try:
        sub = sorted({_tag_name(t) for t in exif.get_ifd(ExifTags.IFD.Exif)})
    except Exception:  # pragma: no cover
        pass
    try:
        gps = bool(exif.get_ifd(ExifTags.IFD.GPSInfo))
    except Exception:  # pragma: no cover
        gps = False
    info_keys = sorted(k for k in im.info.keys() if k not in HARMLESS_INFO_KEYS and k != "exif")
    return {
        "exif_tags": tags,
        "exif_subifd_tags": sub,
        "gps": gps,
        "xmp": bool(im.info.get("xmp") or im.info.get("XML:com.adobe.xmp")),
        "icc_profile": "icc_profile" in im.info,
        "other_info_keys": info_keys,
        "total_items": len(tags) + len(sub) + len(info_keys),
    }


def text_metadata(im: Image.Image, personal_only: bool = False) -> list[str]:
    """Textual metadata values (for PHI scanning only — never written to reports).

    ``personal_only`` keeps free-text fields a person types (EXIF description/artist/comment,
    PNG text chunks, JPEG comments) and skips camera make/model/software strings.
    """
    tags = PERSONAL_TEXT_TAGS if personal_only else _TEXT_TAGS
    vals: list[str] = []
    exif = im.getexif()
    for t, v in exif.items():
        if _tag_name(t) in tags:
            vals.append(_as_text(v))
    try:
        for t, v in exif.get_ifd(ExifTags.IFD.Exif).items():
            if _tag_name(t) in tags or (not personal_only and _tag_name(t).startswith("DateTime")):
                vals.append(_as_text(v))
    except Exception:  # pragma: no cover
        pass
    for k, v in im.info.items():
        if k in HARMLESS_INFO_KEYS or k in ("exif", "icc_profile"):
            continue
        if personal_only and k in ("xmp", "XML:com.adobe.xmp"):
            continue
        if isinstance(v, bytes):
            vals.append(v.decode("utf-8", "ignore"))
        elif isinstance(v, str):
            vals.append(v)
    return [v for v in vals if v and v.strip()]


def _as_text(v) -> str:
    if isinstance(v, bytes):
        if v[:8] in (b"ASCII\x00\x00\x00", b"UNICODE\x00", b"\x00" * 8):   # EXIF UserComment header
            body = v[8:]
            return body.decode("utf-16", "ignore") if v[:7] == b"UNICODE" else body.decode("utf-8", "ignore")
        try:
            return v.decode("utf-16-le").rstrip("\x00") if len(v) % 2 == 0 and b"\x00" in v else v.decode("utf-8", "ignore")
        except UnicodeDecodeError:
            return v.decode("utf-8", "ignore")
    if isinstance(v, tuple):                                   # XP* tags come as byte tuples
        try:
            return bytes(v).decode("utf-16-le").rstrip("\x00")
        except Exception:
            return str(v)
    return str(v)


def load(path: str | Path) -> tuple[np.ndarray, dict, list[str]]:
    """Decode an image to an array with orientation applied.

    Returns ``(array, inventory, personal_text_metadata_values)``. Arrays are ``uint8`` greyscale,
    ``uint8`` RGB, or ``uint16`` greyscale for 16-bit PNG/TIFF radiograph exports.
    """
    with Image.open(path) as im:
        inv = inventory(im)
        inv["frames"] = int(getattr(im, "n_frames", 1) or 1)
        texts = text_metadata(im, personal_only=True)
        im.load()
        try:
            im = ImageOps.exif_transpose(im)
        except Exception:  # pragma: no cover - corrupt orientation tag
            pass
        mode = im.mode
        if mode in ("I;16", "I;16B", "I;16L"):
            arr = np.asarray(im, dtype=np.uint16)
        elif mode == "I":
            a = np.asarray(im)
            arr = np.clip(a, 0, 65535).astype(np.uint16) if a.max() > 255 else a.astype(np.uint8)
        elif mode in ("L", "1"):
            arr = np.asarray(im.convert("L"))
        elif mode in ("LA",):
            arr = np.asarray(im.convert("L"))
        else:
            if "A" in mode or mode == "P":
                rgba = im.convert("RGBA")
                bg = Image.new("RGBA", rgba.size, (0, 0, 0, 255))
                im = Image.alpha_composite(bg, rgba)
            arr = np.asarray(im.convert("RGB"))
    return np.ascontiguousarray(arr), inv, texts


def display8(arr: np.ndarray) -> np.ndarray:
    """8-bit view for detection and previews.

    16-bit greys are stretched between their 0.5 and 99.9 percentiles, **keeping the top of
    the range open** when the brightest values sit just above it — burned-in text is usually
    written at (or near) the maximum value and must not be clipped into a white band.
    (The web app implements the same mapping.)
    """
    if arr.dtype == np.uint8:
        return arr
    a = arr.astype(np.float64)
    lo, hi = float(np.percentile(a, 0.5)), float(np.percentile(a, 99.9))
    top = float(a.max())
    if top - hi < 0.5 * (hi - lo):
        hi = max(hi, top)
    if hi <= lo:
        hi = lo + 1
    return np.clip((a - lo) * 255.0 / (hi - lo) + 0.5, 0, 255).astype(np.uint8)


def linear8(arr: np.ndarray) -> np.ndarray | None:
    """Plain linear 16→8-bit view (what an ordinary viewer shows), or None for 8-bit images."""
    if arr.dtype == np.uint8:
        return None
    a = arr.astype(np.int64)
    a = a - a.min()
    top = int(a.max())
    if top <= 255:
        return None
    return (a * 255 // top).astype(np.uint8)


def detection_views(arr: np.ndarray) -> list[np.ndarray]:
    views = [display8(arr)]
    lin = linear8(arr)
    if lin is not None:
        views.append(lin)
    return views


def output_format(suffix: str, requested: str = "same") -> tuple[str, str]:
    """Return ``(PIL format, extension)`` for the clean copy."""
    s = suffix.lower()
    if requested == "png":
        return "PNG", ".png"
    if requested == "jpeg":
        return "JPEG", ".jpg"
    if s in (".jpg", ".jpeg", ".jfif"):
        return "JPEG", ".jpg"
    if s == ".webp":
        return "WEBP", ".webp"
    if s in (".tif", ".tiff"):
        return "TIFF", ".tif"
    return "PNG", ".png"   # png, bmp, gif → lossless PNG


def save_clean(arr: np.ndarray, path: str | Path, fmt: str, jpeg_quality: int = 95) -> None:
    """Write pixels only — no EXIF/XMP/ICC/comments/text chunks."""
    a = arr
    if a.dtype == np.uint16 and fmt in ("JPEG", "WEBP"):
        a = display8(a)
    im = Image.fromarray(a)
    kw: dict = {}
    if fmt == "JPEG":
        kw = {"quality": jpeg_quality, "subsampling": 0, "optimize": True}
    elif fmt == "PNG":
        kw = {"compress_level": 6}
    elif fmt == "WEBP":
        kw = {"lossless": True}
    elif fmt == "TIFF":
        kw = {"compression": "tiff_deflate"}
    im.save(path, format=fmt, **kw)


def residual_metadata(path: str | Path) -> list[str]:
    """Names of any metadata items remaining in a written file (should be empty)."""
    with Image.open(path) as im:
        inv = inventory(im)
    left = inv["exif_tags"] + inv["exif_subifd_tags"] + inv["other_info_keys"]
    if inv["gps"]:
        left.append("GPS")
    if inv["xmp"]:
        left.append("XMP")
    if inv["icc_profile"]:
        left.append("ICC")
    # Pillow's TIFF writer always records basic structural tags; they carry no identity.
    structural = {"ImageWidth", "ImageLength", "BitsPerSample", "Compression", "PhotometricInterpretation",
                  "StripOffsets", "SamplesPerPixel", "RowsPerStrip", "StripByteCounts", "PlanarConfiguration",
                  "XResolution", "YResolution", "ResolutionUnit", "Orientation", "ExtraSamples", "SampleFormat",
                  "Predictor", "TileWidth", "TileLength", "TileOffsets", "TileByteCounts", "FillOrder"}
    return [k for k in left if k not in structural]
