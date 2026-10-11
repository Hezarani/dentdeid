"""The DentDeID pipeline: discover → classify → de-identify → verify → report.

Output folder layout::

    OUT/
      clean/            de-identified files that passed (PASS/REVIEW)
      quarantine/       files that FAILED a check or could not be fully processed — NOT safe
      review/           preview PNGs with every redaction outlined (for the human check)
      dentdeid-report.json / .md
      KEEP-PRIVATE/     only with write_mapping=True: original ↔ new names (re-identifying!)

One file that cannot be processed never stops the batch: it is recorded as ERROR (nothing is
written for it) and the report is always written.
"""
from __future__ import annotations

import csv
import os
import re
import shutil
import unicodedata
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

import numpy as np
import pydicom
from PIL import Image, ImageDraw

from . import verify as _verify
from .config import Config, get_profile
from .dicom import cbct as _cbct
from .dicom import meta as _meta
from .dicom import pixels as _pixels
from .dicom import ps315
from .imaging import faces as _faces
from .imaging import ocr as _ocr
from .imaging import raster
from .imaging.textdetect import Region, detect_text, image_class, merge_regions, to_gray
from .keys import Pseudonymizer, load_or_create_key
from .phi import KnownIdentifiers, find_patterns, find_phi, is_safe_burned_in_text, name_like_words
from .report import BatchReport, Check, FileReport

# Single-frame CT slices are grouped into volumes by series.
CT_SOP_CLASSES = {"1.2.840.10008.5.1.4.1.1.2"}
# Multi-frame volume objects (assessed for facial re-identification risk like a CT series).
VOLUME_SOP_CLASSES = {
    "1.2.840.10008.5.1.4.1.1.2",            # CT (multi-frame by some vendors)
    "1.2.840.10008.5.1.4.1.1.2.1",          # Enhanced CT
    "1.2.840.10008.5.1.4.1.1.2.2",          # Legacy Converted Enhanced CT
    "1.2.840.10008.5.1.4.1.1.13.1.2",       # X-Ray 3D Craniofacial (dental CBCT)
}
DICOMDIR_SOP_CLASS = "1.2.840.10008.1.3.10"
# upper-case words that are safe to keep in a file name with rename=False
_SAFE_NAME_WORDS = {"pano", "ceph", "cbct", "bitewing", "opg", "dicom", "scan", "xray", "image", "photo", "left",
                    "right", "upper", "lower", "view", "export", "copy", "final", "draft", "case", "test"}
_MODALITY = re.compile(r"^[A-Z0-9]{1,16}$")


def _modality(ds) -> str:
    """Modality code for names/reports — only a plain code, never free text from the file."""
    m = str(ds.get("Modality", "") or "").strip().upper()
    return m if _MODALITY.match(m) and len(m) <= 4 else "OT"


def _err(e: BaseException) -> str:
    """Exception *type* only: messages can quote values from the file (e.g. a name)."""
    return type(e).__name__


def add_user_known(known: KnownIdentifiers, value: str) -> None:
    v = value.strip()
    if not v:
        return
    digits = re.sub(r"\D", "", v)
    if re.fullmatch(r"\d{4}[-./]?\d{2}[-./]?\d{2}", v):
        known.add_date(digits, "user-date")
    elif re.fullmatch(r"\d{1,2}[-./]\d{1,2}[-./]\d{4}", v):
        d, m, y = re.split(r"[-./]", v)
        known.add_date(f"{y}{int(m):02d}{int(d):02d}", "user-date")
    elif any(c.isdigit() for c in v):
        known.add_id(v, "user-id")
    else:
        known.add_name(v, "user-name")


def add_metadata_known(known: KnownIdentifiers, texts: list[str]) -> None:
    """Identifiers typed into an image's metadata (EXIF description/artist, PNG text …) are
    searched for in its pixels (OCR) and file name too."""
    for t in texts:
        t = unicodedata.normalize("NFC", t).strip()
        if not t or len(t) > 2000:
            continue
        for m in re.finditer(r"[A-ZÇĞİÖŞÜÄÉ][a-zçğıöşüäéß]{2,}(?:[ _-]+[A-ZÇĞİÖŞÜÄÉ][a-zçğıöşüäéß]{2,})+", t):
            if name_like_words(m.group(0)):
                known.add_text(m.group(0).replace("_", " "), "metadata-name")   # the whole phrase only
        for m in re.finditer(r"(?<![\w])(?=[\w-]*\d)[A-Za-z0-9][\w-]{4,}(?![\w])", t):
            tok = m.group(0)
            if sum(c.isdigit() for c in tok) >= 4:
                known.add_id(tok, "metadata-id")
        for f in find_patterns(t):
            if f.kind == "date":
                d = re.sub(r"\D", "", t[f.start:f.end])
                if len(d) == 8 and d[:2] in ("19", "20"):
                    known.add_date(d, "metadata-date")


def is_dicom(path: Path) -> bool:
    try:
        with open(path, "rb") as fh:
            head = fh.read(132)
    except OSError:
        # unreadable: a DICOM by name is reported as an ERROR, not silently skipped
        return path.suffix.lower() in (".dcm", ".dicom", ".dic") or path.name.upper() == "DICOMDIR"
    if len(head) == 132 and head[128:132] == b"DICM":
        return True
    if path.suffix.lower() in (".dcm", ".dicom", ".dic") or path.name.upper() == "DICOMDIR":
        return True       # read (and reported) by the DICOM path, even if it turns out broken
    return False


def previous_outputs(inputs: Iterable[str | Path]) -> set[Path]:
    """Folders written by an earlier DentDeID run (they hold a dentdeid-report.json)."""
    found: set[Path] = set()
    for p in map(Path, inputs):
        if p.is_dir():
            found |= {q.parent.resolve() for q in p.rglob("dentdeid-report.json")}
    return found


def discover(inputs: Iterable[str | Path]) -> list[Path]:
    """All files under ``inputs`` — except hidden files and earlier DentDeID output folders."""
    inputs = list(inputs)
    skip = previous_outputs(inputs)
    out: list[Path] = []
    for p in map(Path, inputs):
        if p.is_dir():
            out += sorted(q for q in p.rglob("*") if q.is_file() and not q.name.startswith(".")
                          and not any(s in q.resolve().parents for s in skip))
        elif p.is_file():
            out.append(p)
    seen, uniq = set(), []
    for p in out:
        r = p.resolve()
        if r not in seen:
            seen.add(r)
            uniq.append(p)
    return uniq


def _preview(arr: np.ndarray, boxes_red, boxes_face, kept, path: Path, candidates=()) -> None:
    """Review image: red = blacked out, orange = face, green = kept on purpose, blue = text-like
    area that was NOT removed automatically (look at it)."""
    a = raster.display8(arr) if arr.dtype != np.uint8 else arr
    im = Image.fromarray(a).convert("RGB")
    scale = min(1.0, 1400 / max(im.size))
    if scale < 1:
        im = im.resize((int(im.width * scale), int(im.height * scale)), Image.BILINEAR)
    d = ImageDraw.Draw(im)
    s = lambda b: [int(b[0] * scale), int(b[1] * scale), max(int(b[0] * scale), int(b[2] * scale) - 1),  # noqa: E731
                   max(int(b[1] * scale), int(b[3] * scale) - 1)]
    for b in boxes_red:
        d.rectangle(s(b), outline=(255, 59, 48), width=2)
    for b in boxes_face:
        d.rectangle(s(b), outline=(255, 159, 10), width=3)
    for r in kept:
        d.rectangle(s((r.x0, r.y0, r.x1, r.y1)), outline=(52, 199, 89), width=2)
    for r in candidates:
        d.rectangle(s((r.x0, r.y0, r.x1, r.y1)), outline=(10, 132, 255), width=2)
    im.save(path, format="PNG", compress_level=3)


def _save(ds, path: Path) -> None:
    """Write ``ds`` in the encoding its (new) File Meta transfer syntax names — e.g. a
    big-endian source whose pixels were re-written is saved as Explicit VR Little Endian."""
    ts = ds.file_meta.TransferSyntaxUID
    pydicom.dcmwrite(path, ds, enforce_file_format=True, implicit_vr=ts.is_implicit_VR,
                     little_endian=ts.is_little_endian)


def _review_name(name: str) -> str:
    p = Path(name)
    return f"{p.stem}_{p.suffix.lstrip('.') or 'x'}.png"


class DeIdentifier:
    """Run DentDeID over files and folders.

    >>> deid = DeIdentifier("share")                        # doctest: +SKIP
    >>> report = deid.run(["./exports"], "./deidentified")   # doctest: +SKIP
    """

    def __init__(self, config: Config | str = "share", *, key: bytes | None = None,
                 key_file: str | Path | None = None):
        self.cfg = get_profile(config) if isinstance(config, str) else config
        persistent = False
        if key_file is not None:
            key = load_or_create_key(key_file)
            persistent = True
        elif key is not None:
            persistent = True
        self.pseudo = Pseudonymizer(key)
        self.persistent = persistent
        self.user_known = KnownIdentifiers()
        for k in self.cfg.known:
            add_user_known(self.user_known, k)
        if self.cfg.ocr == "on" and not _ocr.available():
            raise RuntimeError("OCR requested but Tesseract/pytesseract is not available")
        self.ocr_on = self.cfg.ocr == "on" or (self.cfg.ocr == "auto" and _ocr.available())
        if self.cfg.text_policy == "phi-only" and not self.ocr_on:
            raise RuntimeError("the phi-only text policy needs OCR, which is not available")
        self.faces_on = _faces.available()
        self._counters: dict[str, int] = {}
        self._used_names: set[str] = set()

    # ------------------------------------------------------------------ helpers -------
    def settings(self) -> dict:
        c = self.cfg
        return {
            "profile": c.profile, "ps315_edition": ps315.EDITION,
            "ps315_options": sorted(c.meta.options), "dental_allowlist": c.meta.allowlist,
            "text_policy": c.text_policy, "keep_scales": c.keep_scales, "text_in_photos": c.text_in_photos,
            "faces": c.faces, "cbct": c.cbct, "ocr": "on" if self.ocr_on else "off",
            "face_detector": "opencv-haar" if self.faces_on else "unavailable",
            "output_format": c.output_format, "rename": c.rename, "strict": c.strict,
            "user_identifiers_supplied": len(c.known), "detector": asdict(c.detect),
        }

    def _name(self, prefix: str, kind: str, ext: str) -> str:
        kind = re.sub(r"[^A-Za-z0-9]+", "-", kind).strip("-") or "img"
        key = f"{prefix}_{kind}"
        while True:
            self._counters[key] = self._counters.get(key, 0) + 1
            name = f"{key}_{self._counters[key]:04d}{ext}"
            if name not in self._used_names:
                self._used_names.add(name)
                return name

    @staticmethod
    def _stem_text(path: Path) -> str:
        return unicodedata.normalize("NFC", path.stem).replace("_", " ")

    def _filename_findings(self, path: Path, known: KnownIdentifiers) -> list[str]:
        stem = self._stem_text(path)
        kinds = sorted({f.kind for f in find_phi(stem, known)})
        if name_like_words(stem):
            kinds.append("name-like-words")
        return kinds

    def _keep_name(self, path: Path, known: KnownIdentifiers, ext: str) -> str:
        """``rename=False``: keep the original name unless it looks identifying."""
        stem = unicodedata.normalize("NFC", path.stem)
        text = stem.replace("_", " ")
        caps = [w for w in re.findall(r"[A-Za-z]{4,}", stem) if w.isupper() and w.lower() not in _SAFE_NAME_WORDS]
        risky = (re.fullmatch(r"[A-Za-z0-9._-]{1,80}", stem) is None       # non-ASCII (names), spaces …
                 or re.search(r"\d{4,}", stem) is not None                  # IDs, dates
                 or bool(caps)                                              # SURNAME-style words
                 or bool(find_phi(text, known)) or name_like_words(text))
        if risky:
            return self._name("IMG", "renamed", ext)
        name = stem + ext
        if name in self._used_names:
            return self._name(stem, "dup", ext)
        self._used_names.add(name)
        return name

    def _decider(self, known: KnownIdentifiers):
        cfg, ocr_on = self.cfg, self.ocr_on

        def decide(disp: np.ndarray, regions: list[Region]):
            regions = list(regions)
            gray = to_gray(disp)[0]
            if ocr_on and cfg.text_policy != "off":
                for (_t, _c, box) in _ocr.find_words(gray):
                    x0, y0, x1, y1 = box
                    area = max(1, (x1 - x0) * (y1 - y0))
                    cov = 0
                    for r in regions:
                        ix = max(0, min(x1, r.x1) - max(x0, r.x0))
                        iy = max(0, min(y1, r.y1) - max(y0, r.y0))
                        cov += ix * iy
                    if cov < area // 2:
                        pad = max(2, (y1 - y0) // 4)
                        h, w = gray.shape
                        regions.append(Region(max(0, x0 - pad), max(0, y0 - pad), min(w, x1 + pad), min(h, y1 + pad),
                                              "text", "ocr", "medium", 1))
            kept = [r for r in regions if cfg.keep_scales and r.kind == "scale"]
            if cfg.text_policy == "off":
                kept = list(regions)
            elif cfg.text_policy == "phi-only":
                for r in regions:
                    if r in kept or r.kind != "text":
                        continue
                    txt, conf = _ocr.read_region(gray, r)
                    if txt and conf >= 70 and is_safe_burned_in_text(txt) and not find_phi(txt, known):
                        kept.append(r)
            return [r for r in regions if r not in kept], kept
        return decide

    # ------------------------------------------------------------------ run -----------
    def run(self, inputs: Iterable[str | Path], out_dir: str | Path) -> BatchReport:
        inputs = list(inputs)
        out = Path(out_dir)
        out_resolved = out.resolve()
        for i in map(Path, inputs):
            ir = i.resolve()
            if ir == out_resolved or out_resolved in ir.parents:
                raise ValueError("the output folder must not be the input folder or contain it; "
                                 "choose a new, separate output folder")
        for sub in ("clean", "quarantine") + (("review",) if self.cfg.previews else ()):
            (out / sub).mkdir(parents=True, exist_ok=True)
        report = BatchReport(self.cfg.profile, self.settings(), self.pseudo.fingerprint(), self.persistent)
        mapping: list[tuple[str, str]] = []
        try:
            self._run(inputs, out, out_resolved, report, mapping)
        finally:
            report.write(out)
            self._write_mapping(out, mapping)
        return report

    def _write_mapping(self, out: Path, mapping) -> None:
        if not (self.cfg.write_mapping and mapping):
            return
        priv = out / "KEEP-PRIVATE"
        priv.mkdir(exist_ok=True)
        try:
            os.chmod(priv, 0o700)
        except OSError:  # pragma: no cover - e.g. some network drives
            pass
        path = priv / "mapping.csv"
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["original_path", "output"])
            w.writerows(mapping)
        try:
            os.chmod(path, 0o600)
        except OSError:  # pragma: no cover
            pass
        (priv / "README.txt").write_text(
            "This folder links de-identified files back to the originals. It is re-identifying\n"
            "information: store it separately and securely, never share it with the data.\n", encoding="utf-8")

    def _guard(self, report: BatchReport, idx: int, source_type: str, srcs: list[Path], fn) -> None:
        """Run one unit of work; any unexpected failure becomes an ERROR entry, not a crash."""
        try:
            fr = fn()
        except Exception as e:  # noqa: BLE001 - one bad file must never stop the batch
            fr = FileReport(idx, self.pseudo.token("|".join(map(str, srcs))), source_type, status="ERROR")
            fr.notes.append(f"could not be processed ({_err(e)}); nothing was written for it")
        report.files.append(fr)
        for s in srcs:
            report.sources[str(s)] = fr

    def _run(self, inputs, out: Path, out_resolved: Path, report: BatchReport, mapping) -> None:
        skipped_outputs = previous_outputs(inputs) - {out_resolved}
        if skipped_outputs:
            report.settings["earlier_dentdeid_output_folders_skipped"] = len(skipped_outputs)
        files = discover(inputs)
        files = [f for f in files if out_resolved not in f.resolve().parents]

        dicoms, rasters, others = [], [], []
        for f in files:
            if is_dicom(f):
                dicoms.append(f)
            elif f.suffix.lower() in raster.RASTER_SUFFIXES:
                rasters.append(f)
            else:
                others.append(f)

        idx = 0
        # group single-frame CT slices into volumes by series; triage the rest
        series: dict[str, list[tuple[Path, pydicom.Dataset]]] = {}
        singles: list[tuple[Path, pydicom.Dataset | None]] = []
        for f in dicoms:
            try:
                h = pydicom.dcmread(f, stop_before_pixels=True, force=True)
                frames = int(h.get("NumberOfFrames", 1) or 1)
            except Exception:
                singles.append((f, None))
                continue
            sop = str(h.get("SOPClassUID", "") or "")
            if sop in CT_SOP_CLASSES and frames == 1 and "ImagePositionPatient" in h:
                series.setdefault(str(h.get("SeriesInstanceUID", "") or f.parent), []).append((f, h))
            else:
                singles.append((f, h))

        for f, h in singles:
            idx += 1
            self._guard(report, idx, "dicom", [f], lambda f=f, h=h, i=idx: self._one_dicom(f, i, out, mapping, h))
        for members in series.values():
            idx += 1
            srcs = [m[0] for m in members]
            self._guard(report, idx, "dicom", srcs, lambda m=members, i=idx: self._ct_series(m, i, out, mapping))
        for f in rasters:
            idx += 1
            self._guard(report, idx, "raster", [f], lambda f=f, i=idx: self._one_raster(f, i, out, mapping))
        for f in others:
            idx += 1
            fr = FileReport(idx, self.pseudo.token(str(f)), "unknown", status="SKIPPED")
            fr.notes.append("not a DICOM or supported image file; not copied")
            report.files.append(fr)
        for tmp in out.glob(".tmp-*"):          # never leave half-written files behind
            shutil.rmtree(tmp, ignore_errors=True) if tmp.is_dir() else tmp.unlink(missing_ok=True)

    # ------------------------------------------------------------------ DICOM ---------
    def _finish(self, fr: FileReport, tmp: Path, out: Path, name: str, mapping, src: Path) -> None:
        status = fr.finalize()
        dest_dir = out / ("quarantine" if status == "FAIL" else "clean")
        dest = dest_dir / name
        shutil.move(str(tmp), dest)
        fr.output = str(dest.relative_to(out))
        mapping.append((str(src), fr.output))

    def _skip_non_image(self, fr: FileReport, header) -> bool:
        """DICOMDIRs, reports, documents and presentation states are not images: skipped."""
        sop = str(header.get("SOPClassUID", "") or "")
        fm_sop = str(getattr(header, "file_meta", {}).get("MediaStorageSOPClassUID", "") or "")
        if DICOMDIR_SOP_CLASS in (sop, fm_sop) or "DirectoryRecordSequence" in header:
            fr.status = "SKIPPED"
            fr.kind = "DICOMDIR"
            fr.notes.append("DICOMDIR (media directory) not copied — it indexes the original, identified "
                            "files (PS3.15 E.1.1); create a new one from the de-identified files if needed")
            return True
        if not any(k in header for k in ("PixelData", "FloatPixelData", "DoubleFloatPixelData", "Rows")):
            fr.status = "SKIPPED"
            fr.kind = "non-image"
            fr.notes.append("non-image DICOM object (structured report, document, presentation state …) — "
                            "not de-identified and not copied")
            return True
        return False

    def _one_dicom(self, path: Path, idx: int, out: Path, mapping, header=None) -> FileReport:
        fr = FileReport(idx, self.pseudo.token(str(path)), "dicom")
        try:
            ds = pydicom.dcmread(path, force=True)
            _ = ds.get("SOPClassUID")
        except Exception as e:
            fr.status = "ERROR"
            fr.notes.append(f"unreadable DICOM ({_err(e)}); nothing was written")
            return fr
        if self._skip_non_image(fr, ds):
            return fr
        if not str(ds.get("SOPInstanceUID", "") or "").strip() or not str(ds.get("SOPClassUID", "") or "").strip():
            fr.status = "ERROR"
            fr.notes.append("not a valid DICOM image (SOP Class/Instance UID missing); nothing was written")
            return fr
        modality = _modality(ds)
        fr.kind = modality
        known = KnownIdentifiers()
        known.update(self.user_known)
        fname_kinds = self._filename_findings(path, _meta.harvest_identifiers(ds, KnownIdentifiers()))
        mres, known = _meta.deidentify_dataset(ds, self.cfg.meta, self.pseudo, known)
        fr.actions["attributes"] = mres.as_dict()
        fr.notes += mres.notes
        if mres.counts.get("clean:name_like_descriptor_kept"):
            fr.add("descriptor-text", "review", "a kept description contains capitalised words that could be a "
                   "name — check the Study/Series/Protocol descriptions")
        if fname_kinds:
            fr.actions["source_filename_contained"] = fname_kinds

        # pixels ------------------------------------------------------------------------
        face_boxes: list = []
        po = _pixels.clean_pixel_data(ds, self.cfg.detect, self._decider(known),
                                      photos_redact=self.cfg.text_in_photos)
        pixel_checked = po.status == "cleaned"
        redacted, kept = po.redacted_boxes, po.kept
        if po.status == "undecodable":
            fr.add("pixel-data", "fail" if self.cfg.strict else "review",
                   f"pixel data could not be decoded ({po.detail}); burned-in text NOT checked")
            if "BurnedInAnnotation" in ds or po.source_burned_in:
                ds.BurnedInAnnotation = "YES"
        elif po.status == "no_pixels" and "Rows" in ds:
            fr.add("pixel-data", "fail", "an image object without pixel data after processing — not a valid output")
        elif po.status == "cleaned":
            fr.kind = f"{modality} {po.image_class}"
            fr.actions["burned_in_text"] = {
                "image_class": po.image_class, "strategy": po.strategy, "frames": po.frames,
                "regions_detected": len(po.regions), "regions_redacted": len(redacted),
                "regions_kept": len(kept), "candidates_not_redacted": len(po.candidates),
                "boxes": [list(b) for b in redacted],
                "confidence": sorted({r.confidence for r in po.regions}),
            }
            self._text_checks(fr, po.regions, kept, po.candidates,
                              photo_unscanned=(po.image_class == "photo" and not self.cfg.text_in_photos))
            self._burned_in_flag(ds, fr, mres, po)
            if po.image_class == "photo" or (po.image_class == "screenshot" and self.faces_on
                                             and self.cfg.faces != "off"):
                self._dicom_photo_faces(ds, fr, mres, face_boxes)
            elif po.frames > 1 and str(ds.get("SOPClassUID", "")) in VOLUME_SOP_CLASSES:
                self._volume_risk(ds, fr, mres)
        # save to a temp name inside the output folder, verify, then place
        ext = ".dcm"
        prefix = mres.patient_pseudonym or "DDID"
        name = self._name(prefix, modality, ext) if self.cfg.rename else self._keep_name(path, known, ext)
        tmp = out / f".tmp-{fr.source_ref}{ext}"
        _meta.replace_file_meta(ds, str(ds.file_meta.get("TransferSyntaxUID", "")) or None)
        _save(ds, tmp)
        fr.checks += _verify.verify_dicom(tmp, known, self.cfg, redacted, list(kept) + list(po.candidates),
                                          self.ocr_on, pixel_checked)
        if self.cfg.previews and pixel_checked:
            try:
                d2 = pydicom.dcmread(tmp)
                pd = _pixels.read_pixels(d2)
                if pd is not None:
                    fr0 = pd.array if pd.frames <= 1 else pd.array[0]
                    _preview(_pixels.to_display8(fr0, pd, d2), redacted, face_boxes, kept,
                             out / "review" / _review_name(name), po.candidates)
            except Exception:  # pragma: no cover - preview is best effort
                fr.add("preview", "review", "the review image could not be written")
        self._finish(fr, tmp, out, name, mapping, path)
        return fr

    def _text_checks(self, fr: FileReport, regions, kept, candidates, photo_unscanned: bool = False) -> None:
        if any(r.confidence == "low" for r in regions):
            fr.add("low-confidence-regions", "review", "a low-confidence text region was redacted — check it")
        text_kept = [r for r in kept if r.kind != "scale"]
        if text_kept:
            fr.add("text-not-redacted", "review", f"{len(text_kept)} detected text region(s) left in place (policy)")
        if photo_unscanned:
            # photographs are never passed silently: burned-in text is not removed automatically
            fr.add("text-in-photo", "review",
                   (f"{len(candidates)} text-like area(s) outlined in blue in the preview were NOT removed; "
                    if candidates else "no text-like area found, but ")
                   + "text in colour photographs is not removed automatically — check the image "
                     "(or use --text-in-photos)")

    def _burned_in_flag(self, ds, fr: FileReport, mres, po) -> None:
        """Burned In Annotation / Clean Pixel Data (113101) are only claimed when true."""
        text_kept = [r for r in po.kept if r.kind != "scale"]
        if po.image_class == "photo" and not self.cfg.text_in_photos:
            return                                   # not cleaned: keep whatever the source said
        if po.source_burned_in == "YES":
            # the source says so; nothing automatic can confirm that *all* of it is gone, so the
            # flag stays YES (and Clean Pixel Data is not claimed) until a person has checked
            fr.add("burned-in-declared", "review",
                   "the source file declares burned-in annotation — confirm in the preview that all of it was removed"
                   + ("" if po.redacted_boxes else " (no text was found automatically)"))
            ds.BurnedInAnnotation = "YES"
            return
        if text_kept:
            ds.BurnedInAnnotation = "YES"
            return
        ds.BurnedInAnnotation = "NO"
        _meta.add_method(ds, mres, "clean_pixel_data", self.cfg.meta)

    def _dicom_photo_faces(self, ds, fr: FileReport, mres, face_boxes: list) -> None:
        declared = str(ds.get("RecognizableVisualFeatures", "") or "").upper()
        if self.cfg.faces == "off":
            fr.add("faces", "review", "face handling is off for a colour photograph")
            ds.RecognizableVisualFeatures = "YES"
            return
        if not self.faces_on:
            fr.add("faces", "fail" if self.cfg.strict else "review",
                   "colour photograph but face detection is unavailable (OpenCV Haar cascades not found)")
            ds.RecognizableVisualFeatures = "YES"
            return
        pd = _pixels.read_pixels(ds)
        rgb = _pixels.to_display8(pd.array if pd.frames <= 1 else pd.array[0], pd, ds)
        fs = _faces.detect_faces(rgb)
        new, boxes = self._obscure_faces(rgb, fs, fr)
        face_boxes += boxes
        if fs:
            pd.photometric = "RGB"
            _pixels.write_pixels(ds, new.astype(np.uint8), pd)
            ds.BitsAllocated = 8
            ds.RecognizableVisualFeatures = "NO"
            _meta.add_method(ds, mres, "clean_recognizable_visual_features", self.cfg.meta)
        elif declared == "YES":
            fr.add("recognizable-features-declared", "review",
                   "the source says the image shows recognizable features, but no face was detected — check it")

    def _volume_risk(self, ds, fr: FileReport, mres) -> None:
        pd = _pixels.read_pixels(ds)
        vol = pd.array
        risk = _cbct.assess_face_risk(vol, [ds])
        fr.actions["face_risk"] = risk.as_dict()
        stripped = False
        if self.cfg.cbct != "flag":
            if self.cfg.cbct == "strip-soft-tissue":
                vol2, info = _cbct.strip_soft_tissue(vol)
                stripped = True
            else:
                vol2, info = _cbct.shell_soft_tissue(vol, int(round(self.cfg.shell_mm / _cbct.pixel_spacing_mm(ds))))
            fr.actions["cbct_mitigation"] = info
            fr.notes.append("EXPERIMENTAL CBCT mitigation applied; not validated against face-recognition attacks.")
            _pixels.write_pixels(ds, vol2.astype(vol.dtype), pd)
        if stripped:
            ds.RecognizableVisualFeatures = "NO"
            _meta.add_method(ds, mres, "clean_recognizable_visual_features", self.cfg.meta)
        elif risk.level in ("high", "moderate"):
            ds.RecognizableVisualFeatures = "YES"
            fr.add("recognizable-face-in-volume", "review",
                   f"{risk.level} risk: {risk.reason}" + (" (shell mitigation applied)" if self.cfg.cbct == "shell" else ""))

    def _obscure_faces(self, rgb: np.ndarray, fs, fr: FileReport):
        fr.actions["faces"] = {"detected": len(fs), "action": self.cfg.faces if fs else "none-needed"}
        if not fs:
            fr.add("faces", "pass", "no face detected (Haar cascades) — check the preview")
            return rgb, []
        new, boxes = _faces.apply_face_action(rgb, fs, self.cfg.faces)
        if self.cfg.faces == "blur":
            again = _faces.detect_faces(new)
            if again:  # escalate: a face that is still detectable after blurring gets filled
                for f in again:
                    b = _faces.expand(f, new.shape)
                    new[b[1]:b[3], b[0]:b[2]] = 0
                    boxes.append(b)
                fr.notes.append("face still detectable after blur — escalated to solid fill")
        fr.add("faces", "review", f"{len(fs)} face(s) obscured ({self.cfg.faces}) — confirm in the preview")
        return new, boxes

    # ------------------------------------------------------------------ CT volumes -----
    def _ct_series(self, members, idx: int, out: Path, mapping) -> FileReport:
        fr = FileReport(idx, self.pseudo.token("|".join(str(m[0]) for m in members)), "dicom")

        def sort_key(m):
            ipp = m[1].get("ImagePositionPatient")
            try:
                z = float(ipp[2]) if ipp is not None and len(ipp) >= 3 else 0.0
            except (TypeError, ValueError):
                z = 0.0
            try:
                n = int(m[1].get("InstanceNumber", 0) or 0)
            except (TypeError, ValueError):
                n = 0
            return (z, n)
        members = sorted(members, key=sort_key)
        try:
            dss = [pydicom.dcmread(p, force=True) for p, _ in members]
            vol = np.stack([d.pixel_array for d in dss])
        except Exception as e:
            fr.status = "ERROR"
            fr.notes.append(f"could not assemble the CT/CBCT series ({_err(e)}); nothing was written")
            return fr
        modality = _modality(dss[0])
        fr.kind = f"{modality} volume ({len(dss)} slices)"
        risk = _cbct.assess_face_risk(vol, dss)
        fr.actions["face_risk"] = risk.as_dict()
        stripped = False
        if self.cfg.cbct != "flag":
            if self.cfg.cbct == "strip-soft-tissue":
                vol2, info = _cbct.strip_soft_tissue(vol)
                stripped = True
            else:
                vol2, info = _cbct.shell_soft_tissue(vol, int(round(self.cfg.shell_mm / _cbct.pixel_spacing_mm(dss[0]))))
            fr.actions["cbct_mitigation"] = info
            fr.notes.append("EXPERIMENTAL CBCT mitigation applied; not validated against face-recognition attacks.")
            for d, sl in zip(dss, vol2):
                pd = _pixels.read_pixels(d)
                _pixels.write_pixels(d, sl.astype(pd.array.dtype), pd)
        if risk.level in ("high", "moderate") and not stripped:
            fr.add("recognizable-face-in-volume", "review",
                   f"{risk.level} risk: {risk.reason}" + (" (shell mitigation applied)" if self.cfg.cbct == "shell" else ""))
        # burned-in text: every slice is examined on its own
        decide = _pixels.redact_all(self.cfg.keep_scales) if self.cfg.text_policy != "off" else \
            (lambda _d, regs: ([], list(regs)))
        outputs = []
        known_all = KnownIdentifiers()
        known_all.update(self.user_known)
        boxes_by_slice: list[list[tuple[int, int, int, int]]] = []
        kept_by_slice: list[list[Region]] = []
        n_regions = 0
        series_dir = None
        for n, (d, (src, _)) in enumerate(zip(dss, members), start=1):
            known = KnownIdentifiers()
            known.update(self.user_known)
            mres, known = _meta.deidentify_dataset(d, self.cfg.meta, self.pseudo, known)
            known_all.update(known)
            po = _pixels.clean_pixel_data(d, self.cfg.detect, decide)
            if po.status != "cleaned":
                raise RuntimeError("slice pixel data could not be decoded")
            n_regions += len(po.regions)
            boxes_by_slice.append(list(po.redacted_boxes))
            kept_by_slice.append(list(po.kept))
            slice_fr = FileReport(0, "", "")
            self._burned_in_flag(d, slice_fr, mres, po)
            for c in slice_fr.checks:
                if all(c.name != x.name for x in fr.checks):
                    fr.checks.append(Check(c.name, c.result, c.detail + f" (e.g. slice {n})"))
            if stripped:
                d.RecognizableVisualFeatures = "NO"
                _meta.add_method(d, mres, "clean_recognizable_visual_features", self.cfg.meta)
            elif risk.level in ("high", "moderate"):
                d.RecognizableVisualFeatures = "YES"
            if n == 1:
                fr.actions["attributes"] = mres.as_dict()
                series_dir = f"{mres.patient_pseudonym}_{modality}_{self.pseudo.token(str(d.SeriesInstanceUID), 'series', 8)}"
            outputs.append((d, src, n))
        all_boxes = sorted({b for bs in boxes_by_slice for b in bs})
        fr.actions["burned_in_text"] = {"strategy": "per-slice", "slices": len(dss), "regions_detected": n_regions,
                                        "slices_with_redactions": sum(1 for b in boxes_by_slice if b),
                                        "boxes": [list(b) for b in all_boxes]}
        if any(kept_by_slice) and self.cfg.text_policy == "off":
            fr.add("text-not-redacted", "review", "detected text left in place (policy off)")
        dest_tmp = out / f".tmp-{fr.source_ref}"
        dest_tmp.mkdir(exist_ok=True)
        for d, src, n in outputs:
            _meta.replace_file_meta(d, str(d.file_meta.get("TransferSyntaxUID", "")) or None)
            _save(d, dest_tmp / f"IMG_{n:04d}.dcm")
        # verify EVERY slice (headers and residual burned-in text)
        agg: dict[tuple[str, str], list[int]] = {}
        for n in range(1, len(outputs) + 1):
            for c in _verify.verify_dicom(dest_tmp / f"IMG_{n:04d}.dcm", known_all, self.cfg, boxes_by_slice[n - 1],
                                          kept_by_slice[n - 1], False, True):
                if c.name == "ocr-verification":
                    continue
                agg.setdefault((c.name, c.result), []).append(n)
        for (name, result), slices in sorted(agg.items()):
            detail = f"{len(slices)} of {len(outputs)} slices" + ("" if result == "pass" else f" (e.g. slice {slices[0]})")
            fr.checks.append(Check(name, result, detail))
        fr.checks.append(Check("ocr-verification", "skipped", "not run on volumes"))
        status = fr.finalize()
        dest_root = (out / ("quarantine" if status == "FAIL" else "clean")).resolve()
        dest = (dest_root / re.sub(r"[^A-Za-z0-9_-]+", "-", series_dir or "volume")).resolve()
        if dest_root not in dest.parents:          # never write outside the output folder
            raise RuntimeError("unsafe output path")
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(dest_tmp), dest)
        fr.output = str(dest.relative_to(out.resolve())) + "/"
        for _, src, n in outputs:
            mapping.append((str(src), f"{fr.output}IMG_{n:04d}.dcm"))
        if self.cfg.previews:
            try:
                mid = len(outputs) // 2
                pd = _pixels.read_pixels(outputs[mid][0])
                _preview(_pixels.to_display8(pd.array, pd, outputs[mid][0]), all_boxes, [], [],
                         out / "review" / (dest.name + ".png"))
            except Exception:  # pragma: no cover
                fr.add("preview", "review", "the review image could not be written")
        return fr

    # ------------------------------------------------------------------ images --------
    def _one_raster(self, path: Path, idx: int, out: Path, mapping) -> FileReport:
        fr = FileReport(idx, self.pseudo.token(str(path)), "raster")
        try:
            arr, inv, texts = raster.load(path)
        except Exception as e:
            fr.status = "ERROR"
            fr.notes.append(f"unreadable image ({_err(e)}); nothing was written")
            return fr
        known = KnownIdentifiers()
        known.update(self.user_known)
        add_metadata_known(known, texts)
        views = raster.detection_views(arr)
        disp = views[0]
        cls = image_class(disp, self.cfg.detect)
        is_photo = cls == "photo"
        fr.kind = cls
        meta_hits = sorted({f.kind for t in texts for f in find_phi(t, self.user_known)} |
                           ({"name-like-words"} if any(name_like_words(t) for t in texts) else set()))
        fr.actions["metadata"] = {
            "items_removed": inv["total_items"], "gps_removed": inv["gps"], "xmp_removed": inv["xmp"],
            "icc_removed": inv["icc_profile"], "identifier_kinds_found_in_metadata": meta_hits,
        }
        fname = self._filename_findings(path, known)
        if fname:
            fr.actions["source_filename_contained"] = fname
        if inv.get("frames", 1) > 1:
            fr.add("multi-page-image", "review",
                   f"only the first of {inv['frames']} pages/frames was kept; the others were dropped")
        redacted, kept, face_boxes, candidates = [], [], [], []
        work = np.array(arr, copy=True)
        regs = merge_regions([r for v in views for r in detect_text(v, self.cfg.detect, force_color=(cls != "radiograph"))])
        if is_photo and not self.cfg.text_in_photos:
            candidates = regs
            fr.actions["burned_in_text"] = {"image_class": cls, "regions_detected": 0, "regions_redacted": 0,
                                            "regions_kept": 0, "candidates_not_redacted": len(candidates), "boxes": []}
            self._text_checks(fr, [], [], candidates, photo_unscanned=True)
        else:
            red, kept = self._decider(known)(disp, regs)
            fill = 0 if work.ndim == 3 else work.min()
            for r in red:
                work[r.y0:r.y1, r.x0:r.x1, ...] = fill
                redacted.append((r.x0, r.y0, r.x1, r.y1))
            fr.actions["burned_in_text"] = {"image_class": cls, "regions_detected": len(red) + len(kept),
                                            "regions_redacted": len(red), "regions_kept": len(kept),
                                            "boxes": [list(b) for b in redacted],
                                            "confidence": sorted({r.confidence for r in red + kept})}
            self._text_checks(fr, red + kept, kept, [])
        faces_expected = False
        if is_photo or (cls == "screenshot" and self.faces_on and self.cfg.faces != "off"):
            if self.cfg.faces == "off":
                fr.add("faces", "review", "face handling is off for a colour photograph")
            elif not self.faces_on:
                fr.add("faces", "fail" if self.cfg.strict else "review",
                       "colour photograph but face detection is unavailable (OpenCV Haar cascades not found)")
            else:
                faces_expected = True
                fs = _faces.detect_faces(work)
                work, boxes = self._obscure_faces(work, fs, fr)
                face_boxes = boxes
                if fs and self.cfg.faces == "crop-perioral":
                    face_boxes = []
                    candidates = []                  # coordinates no longer apply to the crop
        fmt, ext = raster.output_format(path.suffix, self.cfg.output_format)
        name = self._name("IMG", cls, ext) if self.cfg.rename else self._keep_name(path, known, ext)
        tmp = out / f".tmp-{fr.source_ref}{ext}"
        raster.save_clean(work, tmp, fmt)
        fr.checks += _verify.verify_raster(tmp, known, self.cfg, is_photo, redacted, list(kept) + list(candidates),
                                           self.ocr_on, faces_expected and self.cfg.faces != "crop-perioral")
        if self.cfg.previews:
            try:
                _preview(work, redacted, face_boxes, kept, out / "review" / _review_name(name), candidates)
            except Exception:  # pragma: no cover
                fr.add("preview", "review", "the review image could not be written")
        self._finish(fr, tmp, out, name, mapping, path)
        return fr


def deidentify(inputs, out_dir, profile: str | Config = "share", **kw) -> BatchReport:
    """One-call convenience wrapper: ``dentdeid.deidentify(["in/"], "out/", "research", key_file="k.key")``."""
    key_file = kw.pop("key_file", None)
    key = kw.pop("key", None)
    cfg = get_profile(profile) if isinstance(profile, str) else profile
    if kw:
        cfg = cfg.with_(**kw)
    return DeIdentifier(cfg, key=key, key_file=key_file).run(inputs if isinstance(inputs, (list, tuple)) else [inputs], out_dir)
