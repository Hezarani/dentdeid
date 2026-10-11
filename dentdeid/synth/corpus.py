"""Build **DentDeID-Synth**: a synthetic dental de-identification test corpus with answer keys.

Every identity is fake. Nothing here comes from a real patient, so the corpus can be shared
openly and used by anyone to test a de-identification tool.

Layout::

    CORPUS/
      dicom/        IO (intra-oral), PX (panoramic), DX (ceph), OT (CBCT screenshot) and
                    XC (photo) objects with PHI in the header, private tags, overlays,
                    icons, nested sequences and File Meta
      images/       JPEG/PNG "exports" with burned-in text, EXIF/GPS/XMP-like metadata,
                    PNG text chunks, and patient names in the file name
      cbct/         CT-storage CBCT phantom series with a facial soft-tissue surface
      answer_keys/  one JSON per item: fake identity, PHI strings, burned-in boxes, …
      manifest.json
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian, generate_uid

from .._version import __version__
from . import overlay, render
from .names import Identity, make_identity

SOP = {
    "IO": "1.2.840.10008.5.1.4.1.1.1.3",     # Digital Intra-Oral X-Ray Image Storage – For Presentation
    "PX": "1.2.840.10008.5.1.4.1.1.1.1",     # Digital X-Ray Image Storage – For Presentation (Modality PX)
    "DX": "1.2.840.10008.5.1.4.1.1.1.1",
    "OT": "1.2.840.10008.5.1.4.1.1.7",       # Secondary Capture
    "XC": "1.2.840.10008.5.1.4.1.1.77.1.4",  # VL Photographic Image Storage
    "CT": "1.2.840.10008.5.1.4.1.1.2",       # CT Image Storage (CBCT exports)
}
KIND_MODALITY = {"panoramic": "PX", "periapical": "IO", "bitewing": "IO", "cephalometric": "DX",
                 "cbct_screenshot": "OT", "intraoral_photo": "XC"}
STYLE_SETS = {
    "panoramic": [["header_strip", "footer_technique", "markers"], ["corner_block", "footer_technique"],
                  ["white_band", "markers"], ["corner_block", "colored_label"]],
    "periapical": [["corner_block"], ["corner_block", "colored_label"], ["footer_technique"]],
    "bitewing": [["white_band"], ["corner_block", "markers"]],
    "cephalometric": [["corner_block", "footer_technique"], ["header_strip"]],
}
DICOM_PHI_FIELDS = ["PatientName", "PatientID", "PatientBirthDate", "PatientAddress", "PatientTelephoneNumbers",
                    "OtherPatientIDs", "InstitutionName", "InstitutionAddress", "ReferringPhysicianName",
                    "PerformingPhysicianName", "OperatorsName", "StationName", "DeviceSerialNumber",
                    "AccessionNumber", "StudyDescription", "ImageComments", "AdditionalPatientHistory",
                    "RequestAttributesSequence", "private(0029,xx10-11)", "Overlay(6000)", "IconImageSequence",
                    "FilterType[vendor misuse]", "FileMeta.SourceApplicationEntityTitle", "dates/times", "UIDs"]


def _uid(uid_rng) -> str:
    """Seeded UID (from a *separate* random stream, so UIDs never change the images), making a
    corpus built with the same seed byte-for-byte reproducible. ``None`` → random UID."""
    if uid_rng is None:
        return generate_uid()
    return generate_uid(entropy_srcs=[f"dentdeid-synth-{int(uid_rng.integers(0, 2**62))}"])


def _dicom(img: np.ndarray, ident: Identity, modality: str, rng, *, sixteen_bit=False, mono1=False,
           uid_rng=None) -> Dataset:
    ds = Dataset()
    fm = FileMetaDataset()
    fm.MediaStorageSOPClassUID = SOP[modality]
    ds.SOPInstanceUID = _uid(uid_rng)
    fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    fm.TransferSyntaxUID = ExplicitVRLittleEndian
    fm.ImplementationClassUID = "1.2.826.0.1.3680043.8.498.1"
    fm.SourceApplicationEntityTitle = "CLINIC_PACS"
    ds.file_meta = fm
    ds.preamble = b"CLINIC-EXPORT " + b"\x00" * 114
    ds.SOPClassUID = SOP[modality]
    ds.Modality = modality
    ds.StudyInstanceUID = _uid(uid_rng)
    ds.SeriesInstanceUID = _uid(uid_rng)
    ds.SpecificCharacterSet = "ISO_IR 192"
    ds.PatientName = ident.dicom_name
    ds.PatientID = ident.patient_id
    ds.PatientBirthDate = ident.birth_date
    ds.PatientSex = ident.sex
    ds.PatientAge = f"{ident.age_at_exam():03d}Y"
    ds.PatientAddress = ident.address
    ds.PatientTelephoneNumbers = ident.phone.replace(" ", "")[:16]
    ds.OtherPatientIDs = "H-" + ident.patient_id
    ds.InstitutionName = ident.clinic
    ds.InstitutionAddress = ident.address
    ds.ReferringPhysicianName = f"{ident.dentist_family}^{ident.dentist_given}"
    ds.PerformingPhysicianName = f"{ident.dentist_family}^{ident.dentist_given}"
    ds.OperatorsName = "Assist^Nora"
    ds.StationName = "OP-ROOM-2"
    ds.DeviceSerialNumber = f"SN{int(rng.integers(10000, 99999))}"
    ds.Manufacturer = "SynthDental"
    ds.ManufacturerModelName = "Model S"
    ds.AccessionNumber = ident.accession
    ds.StudyID = "1"
    ds.SeriesNumber = 1
    ds.InstanceNumber = 1
    for kw in ("StudyDate", "SeriesDate", "AcquisitionDate", "ContentDate", "InstanceCreationDate"):
        setattr(ds, kw, ident.exam_date)
    for kw in ("StudyTime", "SeriesTime", "AcquisitionTime", "ContentTime"):
        setattr(ds, kw, ident.exam_time)
    ds.StudyDescription = f"{modality} control - {ident.given} {ident.family}"
    ds.SeriesDescription = f"{modality} standard"
    b = ident.birth_date
    ds.ImageComments = f"DOB {b[6:8]}.{b[4:6]}.{b[0:4]}, call {ident.phone}"
    ds.AdditionalPatientHistory = f"Pat. {ident.family} reports sensitivity"
    ds.FilterType = f"AL {ident.patient_id}"[:16]          # vendor misuse of a technical field
    rq = Dataset()
    rq.RequestedProcedureDescription = f"{modality} for {ident.given} {ident.family}"
    rq.ScheduledPerformingPhysicianName = ds.ReferringPhysicianName
    rq.RequestedProcedureID = ident.accession
    ds.RequestAttributesSequence = Sequence([rq])
    code = Dataset()
    code.CodeValue, code.CodingSchemeDesignator, code.CodeMeaning = "T-54000", "SRT", "Tooth"
    ds.AnatomicRegionSequence = Sequence([code])
    ds.KVP = str(int(rng.integers(60, 90)))
    ds.ExposureTime = str(int(rng.integers(50, 16000)))
    ds.BodyPartExamined = "JAW"
    ds.ImagerPixelSpacing = [0.1, 0.1]
    ds.PixelSpacing = [0.1, 0.1]
    # private block with PHI
    ds.add_new(0x00290010, "LO", "SYNTHDENTAL_PRIV")
    ds.add_new(0x00291010, "LO", f"{ident.family}^{ident.given}")
    ds.add_new(0x00291011, "LO", ident.patient_id)
    # pixel data
    a = img
    if a.ndim == 3:
        ds.SamplesPerPixel = 3
        ds.PhotometricInterpretation = "RGB"
        ds.PlanarConfiguration = 0
        ds.BitsAllocated = ds.BitsStored = 8
        ds.HighBit = 7
    else:
        ds.SamplesPerPixel = 1
        if mono1:
            a = 255 - a
        if sixteen_bit:
            a = a.astype(np.uint16) * 16 + (a.astype(np.uint16) >> 4)   # 12-bit range
            ds.BitsAllocated, ds.BitsStored, ds.HighBit = 16, 12, 11
        else:
            ds.BitsAllocated, ds.BitsStored, ds.HighBit = 8, 8, 7
        ds.PhotometricInterpretation = "MONOCHROME1" if mono1 else "MONOCHROME2"
        # overlay plane carrying PHI-like bits (burned-in overlays are removed as a group)
        rows, cols = a.shape
        ds.add_new(0x60000010, "US", rows)
        ds.add_new(0x60000011, "US", cols)
        ds.add_new(0x60000040, "CS", "G")
        ds.add_new(0x60000050, "SS", [1, 1])
        ds.add_new(0x60000100, "US", 1)
        ds.add_new(0x60000102, "US", 0)
        bits = np.zeros(rows * cols, dtype=np.uint8)
        bits[: cols * 10] = 1
        ds.add_new(0x60003000, "OW", np.packbits(bits, bitorder="little").tobytes())
    ds.PixelRepresentation = 0
    ds.Rows, ds.Columns = a.shape[:2]
    ds.PixelData = np.ascontiguousarray(a).tobytes()
    # icon thumbnail
    icon = Dataset()
    th = np.asarray(Image.fromarray(img).convert("L").resize((64, 64)))
    icon.SamplesPerPixel, icon.PhotometricInterpretation = 1, "MONOCHROME2"
    icon.Rows, icon.Columns, icon.BitsAllocated, icon.BitsStored, icon.HighBit = 64, 64, 8, 8, 7
    icon.PixelRepresentation = 0
    icon.PixelData = th.tobytes()
    ds.IconImageSequence = Sequence([icon])
    return ds


def _exif_bytes(ident: Identity, rng) -> bytes:
    ex = Image.Exif()
    ex[0x010E] = f"{ident.given} {ident.family} - smile"        # ImageDescription
    ex[0x013B] = ident.dentist                                   # Artist
    ex[0x010F] = "SynthCam"                                      # Make
    ex[0x0110] = "SC-1"                                          # Model
    d = ident.exam_date
    stamp = f"{d[:4]}:{d[4:6]}:{d[6:8]} {ident.exam_time[:2]}:{ident.exam_time[2:4]}:{ident.exam_time[4:6]}"
    ex[0x0132] = stamp                                           # DateTime
    sub = ex.get_ifd(0x8769)
    sub[0x9003] = stamp                                          # DateTimeOriginal
    sub[0xA431] = f"BODY{int(rng.integers(100000, 999999))}"     # BodySerialNumber
    gps = ex.get_ifd(0x8825)
    gps[1], gps[2] = "N", (39.0, 55.0, 12.5)
    gps[3], gps[4] = "E", (32.0, 51.0, 3.2)
    return ex.tobytes()


def _save_raster(img: np.ndarray, path: Path, ident: Identity, rng, jpeg_quality: int | None) -> list[str]:
    im = Image.fromarray(img)
    meta = []
    if path.suffix.lower() in (".jpg", ".jpeg"):
        kw = {"quality": jpeg_quality or 92, "exif": _exif_bytes(ident, rng),
              "comment": f"Patient {ident.family}, {ident.patient_id}".encode()}
        if im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        im.save(path, "JPEG", **kw)
        meta = ["EXIF:ImageDescription", "EXIF:Artist", "EXIF:DateTime", "EXIF:DateTimeOriginal",
                "EXIF:BodySerialNumber", "EXIF:GPS", "JPEG:COM"]
    else:
        info = PngInfo()
        info.add_text("Patient", ident.dicom_name)
        info.add_text("Comment", f"ID {ident.patient_id} DOB {ident.birth_date}")
        info.add_itxt("Description", f"{ident.clinic} - {ident.dentist}")
        im.save(path, "PNG", pnginfo=info, exif=_exif_bytes(ident, rng))
        meta = ["PNG:tEXt:Patient", "PNG:tEXt:Comment", "PNG:iTXt:Description", "PNG:eXIf"]
    return meta


def cbct_phantom(rng, n: int = 56, size: int = 96, spacing: float = 1.6) -> np.ndarray:
    """Head-like phantom (stored values, intercept -1000): air, soft tissue with a nose/lips
    surface, a bony shell, and teeth — enough to exercise face-risk logic and soft-tissue removal."""
    z, y, x = np.mgrid[0:n, 0:size, 0:size].astype(np.float64)
    cz, cy, cx = n / 2, size / 2, size / 2
    vol = np.zeros((n, size, size), dtype=np.int32)                    # air (-1000 HU)
    head = ((x - cx) / (size * 0.40)) ** 2 + ((y - cy) / (size * 0.45)) ** 2 + ((z - cz) / (n * 0.55)) ** 2 <= 1
    nose = ((x - cx) / 5) ** 2 + ((y - (cy - size * 0.45)) / 6) ** 2 + ((z - cz * 1.1) / 6) ** 2 <= 1
    lips = ((x - cx) / 10) ** 2 + ((y - (cy - size * 0.43)) / 4) ** 2 + ((z - cz * 0.7) / 3) ** 2 <= 1
    soft = head | nose | lips
    vol[soft] = 1040 + rng.integers(-25, 25, soft.sum())               # ~ +40 HU
    inner = ((x - cx) / (size * 0.34)) ** 2 + ((y - cy) / (size * 0.39)) ** 2 + ((z - cz) / (n * 0.50)) ** 2 <= 1
    bone_shell = inner & ~(((x - cx) / (size * 0.30)) ** 2 + ((y - cy) / (size * 0.35)) ** 2 +
                           ((z - cz) / (n * 0.46)) ** 2 <= 1)
    vol[bone_shell] = 2200 + rng.integers(-60, 60, bone_shell.sum())   # ~ +1200 HU
    for k in range(10):                                                # teeth arc
        t = np.pi * (0.15 + 0.7 * k / 9)
        tx, ty = cx - np.cos(t) * size * 0.25, cy - np.sin(t) * size * 0.30
        tooth = ((x - tx) / 3) ** 2 + ((y - ty) / 3) ** 2 + ((z - cz * 0.8) / 5) ** 2 <= 1
        vol[tooth] = 3000
    return vol.astype(np.uint16)


def _cbct_series(out: Path, ident: Identity, rng, n=56, size=96, spacing=1.6, uid_rng=None) -> list[str]:
    vol = cbct_phantom(rng, n, size, spacing)
    study, series, frame = _uid(uid_rng), _uid(uid_rng), _uid(uid_rng)
    files = []
    out.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        ds = Dataset()
        fm = FileMetaDataset()
        fm.MediaStorageSOPClassUID = SOP["CT"]
        ds.SOPInstanceUID = _uid(uid_rng)
        fm.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
        fm.TransferSyntaxUID = ExplicitVRLittleEndian
        fm.SourceApplicationEntityTitle = "CBCT_WS"
        ds.file_meta = fm
        ds.SOPClassUID = SOP["CT"]
        ds.SpecificCharacterSet = "ISO_IR 192"
        ds.Modality = "CT"
        ds.StudyInstanceUID, ds.SeriesInstanceUID, ds.FrameOfReferenceUID = study, series, frame
        ds.PatientName, ds.PatientID, ds.PatientBirthDate, ds.PatientSex = ident.dicom_name, ident.patient_id, ident.birth_date, ident.sex
        ds.InstitutionName = ident.clinic
        ds.StudyDate = ds.SeriesDate = ds.AcquisitionDate = ds.ContentDate = ident.exam_date
        ds.StudyTime = ident.exam_time
        ds.StudyDescription = f"CBCT {ident.family}"
        ds.SeriesDescription = "CBCT 15x15"
        ds.ImageType = ["ORIGINAL", "PRIMARY", "AXIAL"]
        ds.InstanceNumber = i + 1
        ds.ImagePositionPatient = [0.0, 0.0, float(i * spacing)]
        ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
        ds.PixelSpacing = [spacing, spacing]
        ds.SliceThickness = spacing
        ds.RescaleIntercept, ds.RescaleSlope = -1000, 1
        ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
        ds.Rows = ds.Columns = size
        ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 12, 11, 0
        ds.PixelData = vol[i].tobytes()
        p = out / f"slice_{i + 1:03d}.dcm"
        ds.save_as(p, enforce_file_format=True)
        files.append(str(p))
    return files


def build(out_dir: str | Path, n: int = 24, seed: int = 2026, include_cbct: bool = True,
          hard_fraction: float = 0.3) -> dict:
    """Generate the corpus. ``n`` = number of 2-D items (each written as DICOM *and* raster)."""
    out = Path(out_dir)
    for sub in ("dicom", "images", "answer_keys"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    uid_rng = np.random.default_rng([seed, 7919])
    kinds = ["panoramic", "periapical", "bitewing", "cephalometric", "cbct_screenshot", "intraoral_photo"]
    items = []
    for i in range(n):
        kind = kinds[i % len(kinds)]
        ident = make_identity(rng)
        hard = kind not in ("intraoral_photo", "cbct_screenshot") and rng.random() < hard_fraction
        if kind == "intraoral_photo":
            r = render.intraoral_photo(rng)
        else:
            r = render.RADIOGRAPHS[kind](rng)
            if kind == "cbct_screenshot":
                r = overlay.cbct_labels(r, ident, rng)
            else:
                sets = STYLE_SETS[kind]
                styles = list(sets[int(rng.integers(0, len(sets)))])
                if hard:
                    styles.append("over_anatomy")
                r = overlay.apply_styles(r, ident, rng, styles, hard=hard)
        cid = f"c{i + 1:04d}"
        modality = KIND_MODALITY[kind]
        # DICOM
        ds = _dicom(r.image, ident, modality, rng, sixteen_bit=(kind == "panoramic" and i % 2 == 0),
                    mono1=(kind == "periapical" and i % 3 == 0), uid_rng=uid_rng)
        dpath = out / "dicom" / f"{cid}_{modality}.dcm"
        ds.save_as(dpath, enforce_file_format=True)
        # raster export (name-bearing file name)
        b = ident.birth_date
        jpeg = hard or kind == "intraoral_photo" or i % 4 == 1
        rname = f"{ident.family}_{ident.given}_{b[:4]}-{b[4:6]}-{b[6:8]}_{kind}{'.jpg' if jpeg else '.png'}"
        rpath = out / "images" / rname
        meta = _save_raster(r.image, rpath, ident, rng, 70 if hard else None)
        key = {
            "id": cid, "kind": kind, "difficulty": "hard" if hard else "standard",
            "dicom": str(dpath.relative_to(out)), "raster": str(rpath.relative_to(out)),
            "identity": ident.as_dict(), "phi_strings": ident.phi_strings(),
            "burned_in": [t.as_dict() for t in r.text], "scales": [list(s) for s in r.scales],
            "confounders": r.confounders, "dicom_phi_fields": DICOM_PHI_FIELDS, "raster_metadata_phi": meta,
            "dicom_encoding": {"sixteen_bit": kind == "panoramic" and i % 2 == 0,
                               "monochrome1": kind == "periapical" and i % 3 == 0},
        }
        (out / "answer_keys" / f"{cid}.json").write_text(json.dumps(key, indent=1, ensure_ascii=False), "utf-8")
        items.append(key)
    volumes = []
    if include_cbct:
        ident = make_identity(rng)
        files = _cbct_series(out / "cbct" / "v0001", ident, rng, uid_rng=uid_rng)
        vk = {"id": "v0001", "kind": "cbct_volume", "files": [str(Path(f).relative_to(out)) for f in files],
              "identity": ident.as_dict(), "phi_strings": ident.phi_strings(), "expected_face_risk": "high",
              "fov_mm": [96 * 1.6, 96 * 1.6, 56 * 1.6]}
        (out / "answer_keys" / "v0001.json").write_text(json.dumps(vk, indent=1, ensure_ascii=False), "utf-8")
        volumes.append(vk)
    manifest = {
        "name": "DentDeID-Synth", "version": "1.0", "generator": f"dentdeid {__version__}",
        "seed": seed, "n_items": len(items), "n_volumes": len(volumes),
        "license": "CC-BY-4.0",
        "note": "All identities, images and metadata are synthetic. No real patient data.",
        "items": [{"id": k["id"], "kind": k["kind"], "difficulty": k["difficulty"]} for k in items],
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), "utf-8")
    return manifest
