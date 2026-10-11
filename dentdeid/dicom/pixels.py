"""Clean Pixel Data for DICOM images: find burned-in text, change the stored values.

The pixel data of every decodable image is **always re-written** from the decoded values,
uncompressed and little-endian, at exactly the length the image needs. That also removes
what a viewer never shows but a file can still carry: identifiers in unused high bits of the
stored values (retired overlays), bytes after the last frame, and EXIF/COM segments inside
JPEG-compressed pixel data.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
from pydicom.dataset import Dataset

from pydicom.uid import ExplicitVRLittleEndian

from ..imaging.textdetect import DetectParams, Region, detect_text, image_class, merge_regions


@dataclass
class PixelData:
    array: np.ndarray             # stored values; (H,W), (H,W,3), (F,H,W) or (F,H,W,3)
    frames: int
    photometric: str
    color: bool


@dataclass
class PixelOutcome:
    status: str                                   # cleaned | no_pixels | undecodable
    frames: int = 0
    strategy: str = ""                            # single | per-frame
    image_class: str = ""                         # radiograph | screenshot | photo
    regions: list[Region] = field(default_factory=list)
    redacted_boxes: list[tuple[int, int, int, int]] = field(default_factory=list)
    kept: list[Region] = field(default_factory=list)
    candidates: list[Region] = field(default_factory=list)   # photos: proposed, not redacted
    source_burned_in: str = ""                    # BurnedInAnnotation of the source ("YES"/"NO"/"")
    detail: str = ""


def read_pixels(ds: Dataset) -> PixelData | None:
    if "PixelData" not in ds and "FloatPixelData" not in ds and "DoubleFloatPixelData" not in ds:
        return None
    arr = np.asarray(ds.pixel_array)  # may raise if no decoder is available for the transfer syntax
    frames = int(ds.get("NumberOfFrames", 1) or 1)
    pi = str(ds.get("PhotometricInterpretation", "MONOCHROME2"))
    spp = int(ds.get("SamplesPerPixel", 1) or 1)
    color = spp == 3
    # Only the declared frames are image data; anything beyond them (bytes appended after the
    # last frame, which some decoders return as extra frames) is dropped, never carried over.
    single_ndim = 3 if color else 2
    if arr.ndim > single_ndim:
        arr = arr[0] if frames <= 1 else arr[:frames]
    if color and pi.startswith("YBR"):
        pi = "RGB"  # pydicom converts YBR to RGB when decoding
    return PixelData(np.asarray(arr), frames, pi, color)


def linear8(frame: np.ndarray, pd: PixelData) -> np.ndarray | None:
    """A plain linear 8-bit view of >8-bit greyscale stored values (None if not needed)."""
    if pd.color or frame.dtype == np.uint8:
        return None
    a = frame.astype(np.int64)
    a = a - a.min()
    top = int(a.max())
    if top <= 255:
        return None
    out = (a * 255 // top).astype(np.uint8)
    return 255 - out if pd.photometric == "MONOCHROME1" else out


def detection_views(frame: np.ndarray, pd: PixelData, ds: Dataset) -> list[np.ndarray]:
    views = [to_display8(frame, pd, ds)]
    lin = linear8(frame, pd)
    if lin is not None:
        views.append(lin)
    return views


def to_display8(frame: np.ndarray, pd: PixelData, ds: Dataset) -> np.ndarray:
    """Map one frame's stored values to uint8 for detection (window if present, else stretch)."""
    if pd.color:
        a = frame
        if a.dtype != np.uint8:
            a = np.clip(a.astype(np.float64) * 255.0 / max(1.0, float(a.max())), 0, 255).astype(np.uint8)
        return a
    a = frame.astype(np.float64)
    slope = float(ds.get("RescaleSlope", 1) or 1)
    inter = float(ds.get("RescaleIntercept", 0) or 0)
    a = a * slope + inter
    lo, hi = np.percentile(a, 0.5), np.percentile(a, 99.9)
    # burned-in text is usually written at (or near) the maximum stored value: keep the top open
    hi = max(hi, float(a.max())) if a.max() - hi < 0.5 * (hi - lo) else hi
    if hi <= lo:
        hi = lo + 1.0
    out = np.clip((a - lo) * 255.0 / (hi - lo) + 0.5, 0, 255).astype(np.uint8)
    if pd.photometric == "MONOCHROME1":
        out = 255 - out
    return out


def fill_value(pd: PixelData):
    """Stored value that *displays* black."""
    if pd.color:
        return 0
    if pd.photometric == "MONOCHROME1":
        return pd.array.max()
    return pd.array.min()


def write_pixels(ds: Dataset, arr: np.ndarray, pd: PixelData) -> None:
    """Replace Pixel Data with ``arr`` — native little-endian, exact length."""
    for kw, dt in (("FloatPixelData", "<f4"), ("DoubleFloatPixelData", "<f8")):
        if kw in ds:                                   # float images keep their own element
            fm = getattr(ds, "file_meta", None)
            if fm is not None and fm.get("TransferSyntaxUID") is not None and (
                    fm.TransferSyntaxUID.is_compressed or not fm.TransferSyntaxUID.is_little_endian):
                fm.TransferSyntaxUID = ExplicitVRLittleEndian
            ds[kw].value = np.ascontiguousarray(arr, dtype=dt).tobytes()
            return
    bits = int(ds.get("BitsStored", 8 if arr.dtype == np.uint8 else 16))
    if arr.dtype == np.uint8:
        bits = min(bits, 8)
    fm = getattr(ds, "file_meta", None)
    ts = fm.get("TransferSyntaxUID") if fm is not None else None
    if ts is None or ts.is_compressed or not ts.is_little_endian:
        # decoded pixels are written uncompressed; big-endian/deflated/encapsulated sources
        # (and whatever their JPEG streams carried) are converted to Explicit VR Little Endian
        from pydicom.dataset import FileMetaDataset
        if fm is None:
            ds.file_meta = fm = FileMetaDataset()
        fm.TransferSyntaxUID = ExplicitVRLittleEndian
    ds.set_pixel_data(arr, photometric_interpretation=pd.photometric, bits_stored=bits,
                      generate_instance_uid=False)


Decider = Callable[[np.ndarray, list[Region]], tuple[list[Region], list[Region]]]


def redact_all(keep_scales: bool = False) -> Decider:
    """Default policy: redact every detected region (optionally keeping scales/rulers)."""
    def decide(_disp: np.ndarray, regions: list[Region]) -> tuple[list[Region], list[Region]]:
        kept = [r for r in regions if keep_scales and r.kind == "scale"]
        return [r for r in regions if r not in kept], kept
    return decide


def clean_pixel_data(ds: Dataset, params: DetectParams | None = None,
                     decide: Decider | None = None, *, photos_redact: bool = False) -> PixelOutcome:
    """Detect and black out burned-in text in ``ds`` **in place** (Clean Pixel Data Option).

    Every frame is examined separately (text on one frame of a stack is found even if the
    other frames would hide it in a projection), on the windowed view and — for >8-bit
    greyscale — on a plain linear view too. ``decide(display8, regions) -> (to_redact, kept)``
    lets callers apply an OCR-based policy; the default redacts everything detected.
    Colour *photographs* (see :func:`image_class`) only get candidate regions for review,
    unless ``photos_redact``. The caller sets Burned In Annotation from the outcome.
    """
    decide = decide or redact_all(False)
    source_bia = str(ds.get("BurnedInAnnotation", "") or "").strip().upper()
    try:
        pd = read_pixels(ds)
    except Exception as e:  # decoder missing, corrupt data …
        return PixelOutcome("undecodable", detail=type(e).__name__, source_burned_in=source_bia)
    if pd is None:
        return PixelOutcome("no_pixels", source_burned_in=source_bia)
    arr = np.array(pd.array, copy=True)
    value = fill_value(pd)
    frames = [arr] if pd.frames <= 1 else [arr[i] for i in range(pd.frames)]
    first = to_display8(frames[0], pd, ds)
    cls = image_class(first, params)
    out = PixelOutcome("cleaned", frames=pd.frames, strategy="single" if pd.frames <= 1 else "per-frame",
                       image_class=cls, source_burned_in=source_bia)
    for fr in frames:                           # views into ``arr``: edits land in the array
        views = detection_views(fr, pd, ds)
        regs = merge_regions([r for v in views for r in detect_text(v, params, force_color=(cls != "radiograph"))])
        if cls == "photo" and not photos_redact:
            out.candidates += regs
            continue
        red, kept = decide(views[0], regs)
        out.regions += red + kept
        out.kept += kept
        for r in red:
            fr[r.y0:r.y1, r.x0:r.x1, ...] = value
            out.redacted_boxes.append((r.x0, r.y0, r.x1, r.y1))
    write_pixels(ds, arr, pd)
    return out
