"""Regression tests for the issues found by the independent pre-release audit (2026-09-24).

Every test builds a small synthetic file with a planted fake identifier, runs DentDeID and
checks that the identifier does not survive anywhere it should not (synthetic data only).
"""
from __future__ import annotations

import io
import json
import os
import unicodedata
from pathlib import Path

import numpy as np
import pydicom
import pytest
from PIL import Image, ImageDraw, ImageFont
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.encaps import encapsulate
from pydicom.sequence import Sequence
from pydicom.uid import (ExplicitVRBigEndian, ExplicitVRLittleEndian, JPEGBaseline8Bit,
                         generate_uid)

from dentdeid import PROFILES, DeIdentifier
from dentdeid.dicom.meta import MetaConfig, deidentify_dataset, srgb_icc_profile
from dentdeid.imaging import faces
from dentdeid.imaging.textdetect import image_class
from dentdeid.keys import Pseudonymizer, shift_dt
from dentdeid.phi import KnownIdentifiers, scrub_text
from dentdeid.synth import render

NAME, GIVEN, PID = "ZXQPATIENT", "ZXQGIVEN", "ZX998877"
DX = "1.2.840.10008.5.1.4.1.1.1.1"
SC = "1.2.840.10008.5.1.4.1.1.7"
XC = "1.2.840.10008.5.1.4.1.1.77.1.4"


def _font(size=16):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # pragma: no cover - old Pillow
        return ImageFont.load_default()


def _pano(seed=3) -> np.ndarray:
    r = render.RADIOGRAPHS["panoramic"](np.random.default_rng(seed))
    return r.image if r.image.ndim == 2 else r.image[..., 0]


def _burn(gray: np.ndarray, text: str, xy=(20, 12), value=255, size=18) -> np.ndarray:
    im = Image.new("L", (gray.shape[1], gray.shape[0]), 0)
    ImageDraw.Draw(im).text(xy, text, fill=255, font=_font(size))
    out = gray.copy()
    out[np.asarray(im) > 127] = value
    return out


def _ds(arr: np.ndarray, sop=DX, modality="DX", ts=ExplicitVRLittleEndian, photometric=None) -> Dataset:
    ds = Dataset()
    ds.file_meta = FileMetaDataset()
    ds.file_meta.TransferSyntaxUID = ts
    ds.file_meta.MediaStorageSOPClassUID = sop
    ds.SOPClassUID = sop
    ds.SOPInstanceUID = generate_uid()
    ds.file_meta.MediaStorageSOPInstanceUID = ds.SOPInstanceUID
    ds.StudyInstanceUID, ds.SeriesInstanceUID = generate_uid(), generate_uid()
    ds.Modality = modality
    ds.PatientName = f"{NAME}^{GIVEN}"
    ds.PatientID = PID
    ds.PatientBirthDate = "19710203"
    ds.StudyDate = "20240105"
    if arr is not None:
        pi = photometric or ("RGB" if arr.shape[-1] == 3 and arr.ndim >= 3 else "MONOCHROME2")
        ds.set_pixel_data(arr, pi, 8 if arr.dtype == np.uint8 else 12, generate_instance_uid=False)
    return ds


def _run(tmp_path, inputs, profile="share", **kw):
    out = tmp_path / "out"
    cfg = PROFILES[profile].with_(ocr="off", **kw)
    return DeIdentifier(cfg).run(inputs, out), out


def _bytes_contain(root: Path, needle: str) -> list[str]:
    hits = []
    for p in root.rglob("*"):
        if needle.lower() in p.name.lower():
            hits.append(p.name)
        if p.is_file() and needle.encode() in p.read_bytes():
            hits.append(str(p.relative_to(root)))
    return hits


def _text_covered(before: np.ndarray, after: np.ndarray, mask: np.ndarray) -> float:
    return float(np.mean(after[mask] != before[mask]))


# ---------------------------------------------------------------------------------------
# pixel data
# ---------------------------------------------------------------------------------------
def test_rgb_secondary_capture_of_a_radiograph_is_scanned(tmp_path):
    g = _burn(_pano(), f"{NAME} {PID} 03.02.1971")
    rgb = np.stack([g] * 3, axis=-1)
    ds = _ds(rgb, SC, "OT")
    ds.BurnedInAnnotation = "YES"
    ds.save_as(tmp_path / "sc.dcm", enforce_file_format=True)
    rep, out = _run(tmp_path, [tmp_path / "sc.dcm"])
    fr = rep.files[0]
    assert fr.actions["burned_in_text"]["image_class"] == "radiograph"
    assert fr.actions["burned_in_text"]["regions_redacted"] >= 1
    o = pydicom.dcmread(out / fr.output).pixel_array[..., 0]
    mask = _burn(np.zeros_like(g), f"{NAME} {PID} 03.02.1971", value=1) > 0
    assert _text_covered(g, o, mask) > 0.95
    assert list((out / "review").glob("*.png"))


def test_colour_screenshot_title_bar_is_found(tmp_path):
    g = _pano()
    im = Image.fromarray(np.stack([g] * 3, axis=-1))
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, im.width, 42], fill=(20, 60, 160))
    d.text((10, 12), "Ayse Yilmaz OPG 1971-02-03", fill=(255, 255, 255), font=_font(18))
    a = np.asarray(im)
    assert image_class(a) == "screenshot"
    Image.fromarray(a).save(tmp_path / "shot.png")
    rep, out = _run(tmp_path, [tmp_path / "shot.png"])
    fr = rep.files[0]
    assert fr.kind == "screenshot" and fr.actions["burned_in_text"]["regions_redacted"] >= 1
    clean = np.asarray(Image.open(out / fr.output))
    title = np.zeros(a.shape[:2], np.uint8)
    t = Image.fromarray(title)
    ImageDraw.Draw(t).text((10, 12), "Ayse Yilmaz OPG 1971-02-03", fill=255, font=_font(18))
    mask = np.asarray(t) > 127
    assert _text_covered(a[..., 0], clean[..., 0], mask) > 0.95


def test_exif_and_comments_inside_jpeg_pixel_data_do_not_survive(tmp_path):
    g = _pano()
    buf = io.BytesIO()
    exif = Image.Exif()
    exif[0x013B] = f"{NAME} {GIVEN}"                                # Artist
    Image.fromarray(g).save(buf, format="JPEG", quality=90, exif=exif.tobytes(), comment=f"ID {PID}".encode())
    ds = _ds(None)
    ds.file_meta.TransferSyntaxUID = JPEGBaseline8Bit
    ds.Rows, ds.Columns = g.shape
    ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 8, 8, 7, 0
    ds.PixelData = encapsulate([buf.getvalue()])
    ds.save_as(tmp_path / "jpeg.dcm", enforce_file_format=True)
    rep, out = _run(tmp_path, [tmp_path / "jpeg.dcm"])
    assert rep.files[0].status in ("PASS", "REVIEW")
    assert not _bytes_contain(out, NAME) and not _bytes_contain(out, PID)
    o = pydicom.dcmread(out / rep.files[0].output)
    assert not o.file_meta.TransferSyntaxUID.is_compressed


def test_hidden_high_bits_and_trailing_frames_are_dropped(tmp_path):
    base = (_pano().astype(np.uint16) * 8)                          # 12-bit data
    hidden = _burn(np.zeros_like(base), f"{NAME} {PID}", value=1)
    stored = base | (hidden << 15)                                   # a name in the unused bit 15
    ds = _ds(base.astype(np.uint16))
    ds.PixelData = stored.astype("<u2").tobytes() + _burn(np.zeros_like(base), NAME, value=4095).astype("<u2").tobytes()
    ds.save_as(tmp_path / "hb.dcm", enforce_file_format=True)
    rep, out = _run(tmp_path, [tmp_path / "hb.dcm"])
    o = pydicom.dcmread(out / rep.files[0].output)
    raw = np.frombuffer(o.PixelData, "<u2")
    assert raw.size == base.size                                     # exact length, no extra "frame"
    assert int((raw >> 12).max()) == 0                               # nothing above Bits Stored


def test_text_on_one_frame_of_a_long_stack_is_found(tmp_path):
    g = _pano()[:300, :600]
    frames = np.stack([g] * 30)
    frames[3] = 255                                                  # a bright frame that hides max projections
    frames[20] = _burn(g, f"{NAME} {PID} 03.02.1971", xy=(30, 120))
    ds = _ds(frames)
    ds.save_as(tmp_path / "mf.dcm", enforce_file_format=True)
    rep, out = _run(tmp_path, [tmp_path / "mf.dcm"])
    o = pydicom.dcmread(out / rep.files[0].output).pixel_array
    mask = _burn(np.zeros_like(g), f"{NAME} {PID} 03.02.1971", xy=(30, 120)) > 0
    assert _text_covered(frames[20], o[20], mask) > 0.95


def test_source_burned_in_yes_is_not_silently_overwritten(tmp_path):
    ds = _ds(_pano())
    ds.BurnedInAnnotation = "YES"
    ds.save_as(tmp_path / "bia.dcm", enforce_file_format=True)
    rep, out = _run(tmp_path, [tmp_path / "bia.dcm"])
    fr = rep.files[0]
    assert fr.status == "REVIEW" and any(c.name == "burned-in-declared" for c in fr.checks)
    assert pydicom.dcmread(out / fr.output).BurnedInAnnotation == "YES"


def test_text_policy_off_does_not_claim_clean_pixel_data(tmp_path):
    ds = _ds(_burn(_pano(), f"{NAME} {PID}"))
    ds.save_as(tmp_path / "off.dcm", enforce_file_format=True)
    rep, out = _run(tmp_path, [tmp_path / "off.dcm"], text_policy="off")
    o = pydicom.dcmread(out / rep.files[0].output)
    assert o.BurnedInAnnotation == "YES"
    assert "113101" not in {i.CodeValue for i in o.DeidentificationMethodCodeSequence}


def test_16_bit_png_with_text_on_a_bright_band(tmp_path):
    a = (_pano().astype(np.uint16) * 180)
    a[0:40, :] = 50000
    a = _burn(a, f"{NAME} {PID}", xy=(20, 10), value=65535)
    Image.fromarray(a).save(tmp_path / "x16.png")
    rep, out = _run(tmp_path, [tmp_path / "x16.png"])
    clean = np.asarray(Image.open(out / rep.files[0].output))
    mask = _burn(np.zeros_like(a), f"{NAME} {PID}", xy=(20, 10), value=1) > 0
    assert _text_covered(a, clean, mask) > 0.95


def test_big_endian_dicom_with_text_is_processed(tmp_path):
    g = _burn(_pano(), f"{NAME} {PID}").astype(np.uint16) * 16
    ds = _ds(None, ts=ExplicitVRBigEndian)
    ds.Rows, ds.Columns = g.shape
    ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
    ds.BitsAllocated, ds.BitsStored, ds.HighBit, ds.PixelRepresentation = 16, 12, 11, 0
    ds.PixelData = g.astype(">u2").tobytes()
    pydicom.dcmwrite(tmp_path / "be.dcm", ds, enforce_file_format=True, implicit_vr=False, little_endian=False)
    rep, out = _run(tmp_path, [tmp_path / "be.dcm"])
    assert rep.files[0].status in ("PASS", "REVIEW")
    assert pydicom.dcmread(out / rep.files[0].output).file_meta.TransferSyntaxUID == ExplicitVRLittleEndian


# ---------------------------------------------------------------------------------------
# attributes
# ---------------------------------------------------------------------------------------
def _code(value, scheme, meaning):
    c = Dataset()
    c.CodeValue, c.CodingSchemeDesignator, c.CodeMeaning = value, scheme, meaning
    return c


def test_sequences_with_action_d_are_not_kept(tmp_path):
    ds = _ds(_pano())
    inst = _code("HOSP-99317", "L", "Cankaya Branch")
    ds.InstitutionCodeSequence = Sequence([inst])
    op = Dataset()
    op.PersonIdentificationCodeSequence = Sequence([_code("EMP-0042133", "L", "Nurse Demirkol")])
    ds.OperatorIdentificationSequence = Sequence([op])
    for opts in (frozenset(), frozenset({"retain_longitudinal_modified_dates", "clean_descriptors"})):
        d = Dataset(ds)
        d.file_meta = ds.file_meta
        deidentify_dataset(d, MetaConfig(options=opts), Pseudonymizer(b"k" * 32))
        text = str(d)
        assert "HOSP-99317" not in text and "Demirkol" not in text and "EMP-0042133" not in text


def test_clean_structured_content_follows_table_e341():
    ds = _ds(_pano())
    items = []
    for code, vt, attr, value in (("121031", "DATE", "Date", "19290524"),       # Subject Birth Date → X
                                  ("113873", "TEXT", "TextValue", "Hacettepe"),  # Organization Name → X
                                  ("121106", "TEXT", "TextValue", "Seen with Dr. Obradovic, referred by Hamdi Tunc")):
        it = Dataset()
        it.ValueType = vt
        it.ConceptNameCodeSequence = Sequence([_code(code, "DCM", "x")])
        setattr(it, attr, value)
        items.append(it)
    ds.AcquisitionContextSequence = Sequence(items)
    cfg = MetaConfig(options=frozenset({"retain_longitudinal_modified_dates", "clean_descriptors",
                                        "clean_structured_content"}))
    deidentify_dataset(ds, cfg, Pseudonymizer(b"k" * 32))
    text = str(ds)
    for bad in ("19290524", "Hacettepe", "Obradovic", "Hamdi"):
        assert bad not in text


def test_codes_are_not_mistaken_for_ids():
    ds = _ds(_pano())
    ds.AnatomicRegionSequence = Sequence([_code("91609006", "SCT", "Mandible")])
    deidentify_dataset(ds, MetaConfig(allowlist=True), Pseudonymizer(b"k" * 32))
    assert ds.AnatomicRegionSequence[0].CodeValue == "91609006"


def test_binary_url_and_icc_values(tmp_path):
    ds = _ds(np.stack([_pano()] * 3, axis=-1), XC, "XC")
    ds.RetrieveURL = f"https://pacs.example/wado?patientID={PID}"
    ds.ICCProfile = b"\x00\x00\x02\x00desc" + NAME.encode() + b"\x00" * 64
    ds.add_new(0x0016002B, "OB", f"OWNER=Sunrise;USER={NAME}".encode() + b"\x00")   # Maker Note
    ds.StationAETitle = "SDC_OPROOM2"
    for prof in ("share", "research", "basic"):
        d = Dataset(ds)
        d.file_meta = ds.file_meta
        cfg = PROFILES[prof].meta
        deidentify_dataset(d, cfg, Pseudonymizer(b"k" * 32))
        raw = io.BytesIO()
        d.save_as(raw, enforce_file_format=True)
        b = raw.getvalue()
        assert NAME.encode() not in b and PID.encode() not in b and b"SDC_OPROOM2" not in b, prof
        if "ICCProfile" in d:
            assert bytes(d.ICCProfile) == srgb_icc_profile()


def test_standard_uids_are_not_replaced():
    ds = _ds(_pano())
    ds.SynchronizationFrameOfReferenceUID = "1.2.840.10008.15.1.1"
    deidentify_dataset(ds, MetaConfig(), Pseudonymizer(b"k" * 32))
    assert ds.get("SynchronizationFrameOfReferenceUID", "1.2.840.10008.15.1.1") == "1.2.840.10008.15.1.1"


def test_encrypted_attributes_sequence_removed():
    ds = _ds(_pano())
    item = Dataset()
    item.EncryptedContentTransferSyntaxUID = ExplicitVRLittleEndian
    item.EncryptedContent = b"\x01\x02\x03\x04"
    ds.EncryptedAttributesSequence = Sequence([item])
    deidentify_dataset(ds, MetaConfig(options=frozenset({"retain_longitudinal_modified_dates"})),
                       Pseudonymizer(b"k" * 32))
    assert "EncryptedAttributesSequence" not in ds


def test_short_datetimes_are_shifted_and_timezone_dropped():
    assert shift_dt("2021", 400) == "2019"
    assert shift_dt("20210315101112+0300", 1) == "20210314101112"


def test_issuer_of_patient_id_separates_patients():
    p = Pseudonymizer(b"k" * 32)
    a, b = _ds(None), _ds(None)
    a.PatientID = b.PatientID = "1001"
    a.IssuerOfPatientID, b.IssuerOfPatientID = "CLINIC-A", "CLINIC-B"
    ra, _ = deidentify_dataset(a, MetaConfig(), p)
    rb, _ = deidentify_dataset(b, MetaConfig(), p)
    assert ra.patient_pseudonym != rb.patient_pseudonym


# ---------------------------------------------------------------------------------------
# text matching
# ---------------------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["Pano HansMueller 3rd February 1971", "OPG ZX 998 877", "Y i l m a z",
                                  "Yilmazova", "03.II.1971", "٠٣.٠٢.١٩٧١", "born in Feb 1971",
                                  unicodedata.normalize("NFD", "Ayşe Yılmaz")])
def test_known_identifier_evasions(text):
    k = KnownIdentifiers()
    k.add_name("MÜLLER^Hans", "patient-name")
    k.add_name("Yılmaz^Ayşe", "patient-name")
    k.add_id("ZX998877", "patient-id")
    k.add_date("19710203", "birth-date")
    clean, found = scrub_text(text, k, True)
    assert found
    for bad in ("Mueller", "998", "Yil", "1971", "II", "Ay"):
        assert bad.lower() not in clean.lower() or bad == "Ay" and "ay" not in clean.lower()


@pytest.mark.parametrize("text", ["Teeth 11 12 13 14 15 extracted", "crowns 21-22-23-24-25"])
def test_tooth_number_lists_are_not_phone_numbers(text):
    assert scrub_text(text, None, True)[0] == text


def test_clean_descriptors_standard_example():
    assert scrub_text("IO control - 55F Dr. Smithson", None, True, descriptors=True)[0] == "IO control"


# ---------------------------------------------------------------------------------------
# batch robustness, names, paths, reports
# ---------------------------------------------------------------------------------------
def test_bad_files_never_stop_the_batch(tmp_path):
    inp = tmp_path / "in"
    inp.mkdir()
    (inp / "zero.dcm").write_bytes(b"")
    (inp / "note.dcm").write_text("not dicom at all", encoding="utf-8")
    Image.fromarray(_pano()).save(inp / "photo_export.dcm", format="JPEG")
    dd = Dataset()
    dd.file_meta = FileMetaDataset()
    dd.file_meta.MediaStorageSOPClassUID = "1.2.840.10008.1.3.10"
    dd.file_meta.MediaStorageSOPInstanceUID = generate_uid()
    dd.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
    dd.FileSetID = "ZXQPATIENT"
    dd.DirectoryRecordSequence = Sequence([])
    dd.save_as(inp / "DICOMDIR", enforce_file_format=True)
    _ds(_pano()).save_as(inp / "good.dcm", enforce_file_format=True)
    rep, out = _run(tmp_path, [inp])
    statuses = sorted(f.status for f in rep.files)
    assert "PASS" in statuses or "REVIEW" in statuses
    assert (out / "dentdeid-report.json").exists()
    assert not list(out.glob(".tmp-*"))
    assert not _bytes_contain(out / "clean", "ZXQPATIENT")


def test_modality_cannot_escape_the_output_folder(tmp_path):
    inp = tmp_path / "in"
    inp.mkdir()
    for i in range(3):
        ds = _ds(_pano()[:64, :64].astype(np.uint16), "1.2.840.10008.5.1.4.1.1.2", f"CT/../../../ESCAPED")
        ds.ImagePositionPatient = [0, 0, float(i)]
        ds.PixelSpacing = [0.5, 0.5]
        ds.save_as(inp / f"s{i}.dcm", enforce_file_format=True)
    ds = _ds(_pano())
    ds.Modality = NAME
    ds.save_as(inp / "m.dcm", enforce_file_format=True)
    rep, out = _run(tmp_path, [inp])
    assert not (tmp_path / "ESCAPED").exists() and not list(tmp_path.glob("**/ESCAPED*"))
    assert not _bytes_contain(out, NAME)


def test_no_rename_still_renames_identifying_names(tmp_path):
    inp = tmp_path / "in"
    inp.mkdir()
    for n in [unicodedata.normalize("NFD", "Şükrü Öztürk"), "YilmazAyse", NAME, "patient-12345"]:
        Image.fromarray(_pano()[:100, :200]).save(inp / f"{n}.png")
    Image.fromarray(_pano()[:100, :200]).save(inp / "pano_left.png")
    rep, out = _run(tmp_path, [inp], rename=False)
    names = sorted(p.name for p in (out / "clean").iterdir())
    assert "pano_left.png" in names
    assert all(n.startswith("IMG_renamed") or n == "pano_left.png" for n in names)


def test_output_folder_must_not_be_the_input(tmp_path):
    Image.fromarray(_pano()).save(tmp_path / "a.png")
    with pytest.raises(ValueError):
        DeIdentifier(PROFILES["share"].with_(ocr="off")).run([tmp_path], tmp_path)


def test_mapping_is_private_and_review_names_do_not_collide(tmp_path):
    inp = tmp_path / "in"
    inp.mkdir()
    _ds(_pano()).save_as(inp / "case1.dcm", enforce_file_format=True)
    Image.fromarray(_pano()).save(inp / "case1.png")
    rep, out = _run(tmp_path, [inp], rename=False, write_mapping=True)
    if os.name == "posix":
        assert (os.stat(out / "KEEP-PRIVATE" / "mapping.csv").st_mode & 0o077) == 0
    assert len(list((out / "review").glob("*.png"))) == 2


def test_reports_carry_exception_types_only(tmp_path):
    ds = _ds(_pano())
    ds.PhotometricInterpretation = NAME                               # undecodable, value quoted by decoders
    ds.save_as(tmp_path / "bad.dcm", enforce_file_format=True)
    rep, out = _run(tmp_path, [tmp_path / "bad.dcm"])
    for f in ("dentdeid-report.json", "dentdeid-report.md"):
        assert NAME not in (out / f).read_text("utf-8")


def test_photo_without_a_face_does_not_claim_rvf_no(tmp_path):
    rng = np.random.default_rng(1)
    rgb = render.intraoral_photo(rng).image
    ds = _ds(rgb, XC, "XC")
    ds.save_as(tmp_path / "xc.dcm", enforce_file_format=True)
    rep, out = _run(tmp_path, [tmp_path / "xc.dcm"])
    o = pydicom.dcmread(out / rep.files[0].output)
    codes = {i.CodeValue for i in o.DeidentificationMethodCodeSequence}
    assert (o.get("RecognizableVisualFeatures") == "NO") == ("113102" in codes)


def test_face_detector_availability_requires_cascades(monkeypatch):
    import cv2
    assert faces.available() in (True, False)
    monkeypatch.delattr(cv2, "CascadeClassifier", raising=False)
    assert faces.available() is False


def test_metadata_names_are_searched_in_file_names(tmp_path):
    im = Image.fromarray(_pano()[:120, :240])
    exif = Image.Exif()
    exif[0x010E] = "Hasta Adi: Ayse Yilmaz"                          # ImageDescription
    im.save(tmp_path / "Ayse Yilmaz.jpg", exif=exif.tobytes())
    rep, out = _run(tmp_path, [tmp_path / "Ayse Yilmaz.jpg"], rename=False)
    assert rep.files[0].output.startswith("clean/IMG_renamed")
    data = json.loads((out / "dentdeid-report.json").read_text("utf-8"))
    assert "Yilmaz" not in json.dumps(data)


# ---------------------------------------------------------------------------------------
# second verification round (2026-09-30)
# ---------------------------------------------------------------------------------------
def _software_ui(chrome, sidebar_bg, fg, cast=None, name_colour=None):
    """A synthetic dental-imaging program window: coloured chrome, a sidebar with the patient's
    name, and a greyscale panoramic panel."""
    W, H = 1100, 640
    im = Image.new("RGB", (W, H), chrome)
    d = ImageDraw.Draw(im)
    d.rectangle([0, 0, W, 28], fill=(0, 84, 166))
    d.text((10, 6), "DentView 5.2 - Imaging", fill=(255, 255, 255), font=_font(14))
    d.rectangle([0, 28, 240, H], fill=sidebar_bg)
    d.text((12, 60), f"Patient: {NAME} Ayse", fill=name_colour or fg, font=_font(15))
    d.text((12, 84), "DOB: 03.02.1971", fill=fg, font=_font(15))
    pano = _pano()[:420, :700]
    im.paste(Image.fromarray(pano).convert("RGB"), (320, 120))
    a = np.asarray(im).astype(np.int16)
    if cast is not None:
        a = np.clip(a + np.array(cast), 0, 255)
    return a.astype(np.uint8)


@pytest.mark.parametrize("case", ["bluegrey", "light", "cast_jpeg", "red_name"])
def test_common_screenshot_styles_are_scanned(tmp_path, case):
    if case == "bluegrey":
        a = _software_ui((30, 39, 51), (38, 50, 66), (225, 230, 240))
    elif case == "light":
        a = _software_ui((234, 242, 251), (255, 255, 255), (30, 41, 59))
    elif case == "cast_jpeg":
        a = _software_ui((48, 48, 48), (215, 215, 215), (20, 20, 20), cast=(0, 1, 5))
    else:
        a = _software_ui((240, 240, 240), (255, 255, 255), (30, 30, 30), name_colour=(220, 38, 38))
    src = tmp_path / ("s.jpg" if case == "cast_jpeg" else "s.png")
    Image.fromarray(a).save(src, **({"quality": 85} if case == "cast_jpeg" else {}))
    assert image_class(np.asarray(Image.open(src).convert("RGB"))) == "screenshot"
    rep, out = _run(tmp_path, [src])
    assert rep.files[0].actions["burned_in_text"]["regions_redacted"] >= 1
    clean = np.asarray(Image.open(out / rep.files[0].output).convert("L")).astype(int)
    m = Image.new("L", (a.shape[1], a.shape[0]), 0)
    ImageDraw.Draw(m).text((12, 60), f"Patient: {NAME} Ayse", fill=255, font=_font(15))
    mask = np.asarray(m) > 127
    assert float((clean[mask] <= 12).mean()) > 0.95          # the name is inside a black box


def test_photographs_are_never_passed_silently(tmp_path):
    rgb = render.intraoral_photo(np.random.default_rng(2)).image
    Image.fromarray(rgb).save(tmp_path / "photo.jpg")
    rep, _ = _run(tmp_path, [tmp_path / "photo.jpg"])
    fr = rep.files[0]
    assert fr.kind == "photo" and fr.status == "REVIEW"
    assert any(c.name == "text-in-photo" for c in fr.checks)


def test_float_pixel_data_survives(tmp_path):
    ds = _ds(None)
    g = _pano()[:128, :160].astype(np.float32) / 255.0
    ds.Rows, ds.Columns = g.shape
    ds.SamplesPerPixel, ds.PhotometricInterpretation = 1, "MONOCHROME2"
    ds.BitsAllocated = 32
    ds.FloatPixelData = g.tobytes()
    ds.save_as(tmp_path / "f.dcm", enforce_file_format=True)
    for prof in ("share", "basic"):
        out = tmp_path / prof
        rep = DeIdentifier(PROFILES[prof].with_(ocr="off")).run([tmp_path / "f.dcm"], out)
        fr = rep.files[0]
        assert fr.status in ("PASS", "REVIEW"), (prof, fr.status, fr.notes, fr.checks)
        o = pydicom.dcmread(out / fr.output)
        assert o.pixel_array.shape == g.shape


def test_rerunning_on_dentdeid_output_is_clean(tmp_path):
    inp = tmp_path / "in"
    inp.mkdir()
    _ds(_burn(_pano(), f"{NAME} {PID}")).save_as(inp / "a.dcm", enforce_file_format=True)
    rep1, out1 = _run(tmp_path, [inp])
    rep2 = DeIdentifier(PROFILES["share"].with_(ocr="off")).run([out1 / "clean"], tmp_path / "again")
    assert all(f.status in ("PASS", "REVIEW") for f in rep2.files)
    # an earlier output folder inside the input folder is not processed again
    rep3 = DeIdentifier(PROFILES["share"].with_(ocr="off")).run([tmp_path], tmp_path / "third")
    assert len([f for f in rep3.files if f.source_type == "dicom"]) == 1
