"""Independent re-checks of every written output (the "second pair of eyes").

Each check re-opens the **written file** — not the in-memory object — so that a bug in the
writer cannot hide a leak.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pydicom

from .config import Config
from .dicom.meta import IMPLEMENTATION_CLASS_UID, residual_identifiers, srgb_icc_profile
from .dicom.meta import _BINARY_VRS, _KEEP_URL_KWS, _STRUCTURAL_BINARY
from .dicom.pixels import detection_views, read_pixels
from .imaging import faces as _faces
from .imaging import ocr as _ocr
from .imaging import raster
from .imaging.textdetect import Region, detect_text, merge_regions
from .phi import KnownIdentifiers, find_patterns, name_like_words
from .report import Check


def allowed_kinds(cfg: Config) -> set[str]:
    """Harvested identifier kinds that an enabled option is *supposed* to keep."""
    opts = cfg.meta.options
    k: set[str] = set()
    if "retain_longitudinal_full_dates" in opts:
        k.add("known:exam-date")
    if "retain_institution_identity" in opts:
        k.add("known:institution")
    if "retain_device_identity" in opts:
        k.add("known:device-serial")
    return k


def _inside(r: Region, boxes, tol: int = 2) -> bool:
    for (x0, y0, x1, y1) in boxes:
        if r.x0 >= x0 - tol and r.y0 >= y0 - tol and r.x1 <= x1 + tol and r.y1 <= y1 + tol:
            return True
    return False


def residual_text(disp8, cfg: Config, redacted, kept) -> list[Region]:
    """Text still detectable in the output that is neither inside a redaction nor deliberately kept.

    ``disp8`` is one 8-bit view or a list of views of the same image.
    """
    views = disp8 if isinstance(disp8, (list, tuple)) else [disp8]
    allowed = list(redacted) + [(r.x0, r.y0, r.x1, r.y1) for r in kept]
    left = []
    for r in merge_regions([x for v in views for x in detect_text(v, cfg.detect)]):
        if r.kind == "scale" and cfg.keep_scales:
            continue
        if not _inside(r, allowed):
            left.append(r)
    return left


def ocr_check(disp8: np.ndarray, known: KnownIdentifiers, enabled: bool) -> Check:
    if not enabled:
        return Check("ocr-verification", "skipped", "OCR not available/enabled")
    gray = disp8 if disp8.ndim == 2 else disp8[..., :3].mean(axis=2).astype(np.uint8)
    words = _ocr.find_words(gray, min_conf=55.0)
    text = " ".join(w[0] for w in words)
    hits = known.find(text) if known else []
    if hits:
        return Check("ocr-verification", "fail",
                     f"OCR reads harvested identifiers in the output ({', '.join(sorted({h.kind for h in hits}))})")
    pat = sorted({f.kind for f in find_patterns(text) if f.kind not in ("time",)})
    if name_like_words(text):
        pat.append("name-like-words")
    if pat:
        return Check("ocr-verification", "review", f"OCR reads identifier-like text ({', '.join(pat)})")
    return Check("ocr-verification", "pass", f"{len(words)} OCR words, none identifying")


def verify_dicom(path: Path, known: KnownIdentifiers, cfg: Config, redacted, kept, ocr_on: bool,
                 pixel_checked: bool) -> list[Check]:
    checks: list[Check] = []
    try:
        ds = pydicom.dcmread(path)
    except Exception as e:  # pragma: no cover
        return [Check("readable", "fail", f"cannot re-read output: {type(e).__name__}")]
    checks.append(Check("readable", "pass"))
    priv = [e for e in ds.iterall() if e.tag.is_private]
    checks.append(Check("private-attributes", "fail" if priv else "pass",
                        f"{len(priv)} private attributes remain" if priv else "none"))
    flag = str(ds.get("PatientIdentityRemoved", ""))
    checks.append(Check("patient-identity-removed", "pass" if flag == "YES" else "fail", flag or "missing"))
    ok_kinds = allowed_kinds(cfg)
    res = [h for h in residual_identifiers(ds, known) if not set(h["kinds"]) <= ok_kinds]
    checks.append(Check("residual-identifiers", "fail" if res else "pass",
                        ("; ".join(f"{h['keyword'] or h['tag']}: {','.join(h['kinds'])}" for h in res))[:300]
                        if res else f"{len(known)} harvested identifier forms searched"))
    fm = ds.file_meta
    fm_bad = [k for k in ("SourceApplicationEntityTitle", "SendingApplicationEntityTitle",
                          "ReceivingApplicationEntityTitle", "PrivateInformation") if k in fm]
    if str(fm.get("ImplementationClassUID", "")) != IMPLEMENTATION_CLASS_UID:
        fm_bad.append("ImplementationClassUID")
    if ds.preamble not in (None, b"\x00" * 128):
        fm_bad.append("preamble")
    checks.append(Check("file-meta-replaced", "fail" if fm_bad else "pass", ", ".join(fm_bad)))
    extra = [e for e in ds if e.tag.group in range(0x6000, 0x6020, 2) or e.tag.group == 0x0004
             or e.keyword in ("IconImageSequence", "OriginalAttributesSequence", "DigitalSignaturesSequence",
                              "EncryptedAttributesSequence")]
    checks.append(Check("overlays-icons-signatures", "fail" if extra else "pass",
                        ", ".join(e.keyword or str(e.tag) for e in extra)))
    opaque = []
    for e in ds.iterall():
        kw = e.keyword or ""
        if e.VR == "UR" and kw not in _KEEP_URL_KWS and e.value:
            opaque.append(kw or str(e.tag))
        elif e.VR in _BINARY_VRS and kw not in _STRUCTURAL_BINARY and e.value:
            if kw == "ICCProfile" and bytes(e.value) == srgb_icc_profile():
                continue
            opaque.append(kw or str(e.tag))
    checks.append(Check("urls-and-binary-values", "fail" if opaque else "pass",
                        ", ".join(sorted(set(opaque)))[:300] if opaque else "none left"))
    if pixel_checked and ("PixelData" in ds):
        ts = ds.file_meta.get("TransferSyntaxUID")
        if ts is None or ts.is_compressed:
            checks.append(Check("pixel-data-rewritten", "fail",
                                "pixel data is still encapsulated (embedded JPEG segments could carry metadata)"))
        else:
            checks.append(Check("pixel-data-rewritten", "pass", "uncompressed, exact length"))
        try:
            pd = read_pixels(ds)
        except Exception as e:  # pragma: no cover
            checks.append(Check("burned-in-text", "fail", f"cannot decode output pixels: {type(e).__name__}"))
            return checks
        if pd is not None:
            frames = [pd.array] if pd.frames <= 1 else [pd.array[i] for i in range(pd.frames)]
            left: list[Region] = []
            disp0 = None
            bad_frames = 0
            for fr in frames:                               # every frame, not a sample
                views = detection_views(fr, pd, ds)
                disp0 = views[0] if disp0 is None else disp0
                lf = residual_text(views, cfg, redacted, kept)
                bad_frames += bool(lf)
                left += lf
            where = f" on {bad_frames} of {len(frames)} frames" if len(frames) > 1 else ""
            checks.append(Check("burned-in-text", "review" if left else "pass",
                                f"{len(left)} text-like region(s) still detected{where}" if left
                                else f"no residual text detected ({len(frames)} frame(s))"))
            if disp0 is not None:
                checks.append(ocr_check(disp0, known, ocr_on))
    return checks


def verify_raster(path: Path, known: KnownIdentifiers, cfg: Config, is_photo: bool, redacted, kept,
                  ocr_on: bool, faces_expected: bool) -> list[Check]:
    checks: list[Check] = []
    left_meta = raster.residual_metadata(path)
    checks.append(Check("metadata-removed", "fail" if left_meta else "pass",
                        ", ".join(left_meta) if left_meta else "no EXIF/XMP/ICC/comments/text chunks"))
    arr, _, _ = raster.load(path)
    views = raster.detection_views(arr)
    disp = views[0]
    if not is_photo or cfg.text_in_photos:
        left = residual_text(views, cfg, redacted, kept)
        checks.append(Check("burned-in-text", "review" if left else "pass",
                            f"{len(left)} text-like region(s) still detected" if left else "no residual text detected"))
    if faces_expected and cfg.faces != "off" and _faces.available():
        rgb = disp if disp.ndim == 3 else np.stack([disp] * 3, axis=-1)
        fs = _faces.detect_faces(rgb)
        checks.append(Check("faces", "fail" if fs else "pass",
                            f"{len(fs)} face(s) still detectable" if fs else "no detectable face"))
    checks.append(ocr_check(disp, known, ocr_on))
    return checks
