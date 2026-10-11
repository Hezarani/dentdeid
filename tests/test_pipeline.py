"""End-to-end: the whole pipeline on the synthetic corpus."""
import pydicom
import pytest

from dentdeid import PROFILES, DeIdentifier, deidentify
from dentdeid.pipeline import add_user_known
from dentdeid.phi import KnownIdentifiers


@pytest.fixture(scope="module")
def run_share(corpus, tmp_path_factory):
    out = tmp_path_factory.mktemp("out_share")
    rep = DeIdentifier(PROFILES["share"].with_(ocr="off")).run([corpus], out)
    return rep, out


def all_phi(keys):
    s = set()
    for k in keys.values():
        for v in k["phi_strings"]:
            if len(v) >= 4:
                s.add(v)
    return s


def test_no_failures_and_layout(run_share):
    rep, out = run_share
    s = rep.summary()
    assert "FAIL" not in s and "ERROR" not in s
    assert s.get("PASS", 0) >= 14
    # every REVIEW has a reason a person must look: a colour photograph or the CBCT face risk
    for f in rep.files:
        if f.status == "REVIEW":
            assert {c.name for c in f.checks if c.result == "review"} <= {"text-in-photo", "recognizable-face-in-volume"}
    assert (out / "dentdeid-report.json").exists() and (out / "dentdeid-report.md").exists()
    assert list((out / "review").glob("*.png"))
    assert not list((out / "quarantine").iterdir())
    assert not list(out.glob(".tmp-*"))


def test_reports_contain_no_phi(run_share, keys, corpus):
    rep, out = run_share
    blob = (out / "dentdeid-report.json").read_text("utf-8") + (out / "dentdeid-report.md").read_text("utf-8")
    for phi in all_phi(keys):
        assert phi not in blob, phi
    assert str(corpus) not in blob                        # not even the input paths


def test_outputs_carry_no_fake_identity(run_share, keys):
    rep, out = run_share
    for f in (out / "clean").rglob("*"):
        if not f.is_file():
            continue
        assert not any(k["identity"]["family"] in f.name for k in keys.values())
        raw = f.read_bytes()
        for phi in all_phi(keys):
            assert phi.encode() not in raw, (f.name, phi)


def test_dicom_outputs_are_valid_and_flagged(run_share):
    rep, out = run_share
    dcm = [p for p in (out / "clean").glob("*.dcm")]
    assert dcm
    for p in dcm:
        ds = pydicom.dcmread(p)
        assert ds.PatientIdentityRemoved == "YES"
        codes = {i.CodeValue for i in ds.DeidentificationMethodCodeSequence}
        if ds.Modality == "XC":                              # photographs: faces, not burned-in text
            # "NO" (and code 113102) only when a face was actually obscured
            assert (ds.get("RecognizableVisualFeatures") == "NO") == ("113102" in codes)
        else:
            assert ds.BurnedInAnnotation == "NO" and "113101" in codes
        assert "113100" in codes
        _ = ds.pixel_array                                   # still decodable


def test_cbct_volume_flagged(run_share):
    rep, _ = run_share
    vols = [f for f in rep.files if f.kind.startswith("CT volume")]
    assert len(vols) == 1 and vols[0].actions["face_risk"]["level"] == "high"
    assert vols[0].status == "REVIEW"


def test_cbct_strip_mode(corpus, tmp_path):
    cfg = PROFILES["share"].with_(ocr="off", cbct="strip-soft-tissue", previews=False)
    rep = DeIdentifier(cfg).run([corpus / "cbct"], tmp_path)
    fr = rep.files[0]
    assert fr.actions["cbct_mitigation"]["mode"] == "strip-soft-tissue"
    ds = pydicom.dcmread(next((tmp_path / "clean").rglob("*.dcm")))
    assert ds.RecognizableVisualFeatures == "NO"
    assert "113102" in {i.CodeValue for i in ds.DeidentificationMethodCodeSequence}


def test_research_profile_with_key_is_reproducible(corpus, tmp_path):
    key = tmp_path / "project.key"
    cfg = PROFILES["research"].with_(ocr="off", previews=False)
    src = sorted((corpus / "dicom").glob("*.dcm"))[:2]
    DeIdentifier(cfg, key_file=key).run(src, tmp_path / "a")
    DeIdentifier(cfg, key_file=key).run(src, tmp_path / "b")
    a = sorted(p.name for p in (tmp_path / "a" / "clean").iterdir())
    b = sorted(p.name for p in (tmp_path / "b" / "clean").iterdir())
    assert a == b
    da = pydicom.dcmread(tmp_path / "a" / "clean" / a[0])
    db = pydicom.dcmread(tmp_path / "b" / "clean" / b[0])
    assert da.StudyInstanceUID == db.StudyInstanceUID and da.PatientAge == db.PatientAge != ""


def test_undecodable_pixels_are_quarantined(dicom_ds, tmp_path):
    from pydicom.encaps import encapsulate
    ds = dicom_ds
    ds.file_meta.TransferSyntaxUID = "1.2.840.10008.1.2.4.80"      # JPEG-LS: claims compression …
    ds.PixelData = encapsulate([b"\xff\xd8 not a real codestream \xff\xd9"])   # … but holds garbage
    src = tmp_path / "bad.dcm"
    ds.save_as(src, enforce_file_format=False)
    rep = DeIdentifier(PROFILES["share"].with_(ocr="off", previews=False)).run([src], tmp_path / "o")
    assert rep.files[0].status == "FAIL"
    assert list((tmp_path / "o" / "quarantine").iterdir()) and not list((tmp_path / "o" / "clean").iterdir())


def test_user_known_identifiers():
    k = KnownIdentifiers()
    for v in ("Zeynep Kara", "483920", "1990-01-02", "02.01.1990"):
        add_user_known(k, v)
    assert k.find("KARA ZEYNEP") and k.find("id 483920") and k.find("2 Ocak 1990")


def test_mapping_file_only_when_requested(corpus, tmp_path):
    src = sorted((corpus / "images").iterdir())[:1]
    deidentify(src, tmp_path / "x", "share", ocr="off", previews=False)
    assert not (tmp_path / "x" / "KEEP-PRIVATE").exists()
    deidentify(src, tmp_path / "y", "share", ocr="off", previews=False, write_mapping=True)
    assert (tmp_path / "y" / "KEEP-PRIVATE" / "mapping.csv").exists()


def test_photo_without_face_detector_fails_closed(corpus, tmp_path, monkeypatch):
    from dentdeid.imaging import faces
    monkeypatch.setattr(faces, "available", lambda: False)
    photos = [p for p in (corpus / "images").iterdir() if "photo" in p.name]
    rep = DeIdentifier(PROFILES["share"].with_(ocr="off", previews=False)).run(photos[:1], tmp_path)
    assert rep.files[0].status == "FAIL"
