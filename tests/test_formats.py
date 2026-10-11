"""Real-world formats: JPEG-compressed DICOM, implicit VR without preamble, multi-page TIFF."""
import io

import numpy as np
import pydicom
from PIL import Image
from pydicom.encaps import encapsulate
from pydicom.uid import ImplicitVRLittleEndian, JPEGBaseline8Bit

from dentdeid import PROFILES, DeIdentifier
from dentdeid.synth import names, overlay, render
from dentdeid.synth.corpus import _dicom

CFG = PROFILES["share"].with_(ocr="off", previews=False)


def _pan(seed=5):
    rng = np.random.default_rng(seed)
    ident = names.make_identity(rng)
    return rng, ident, overlay.apply_styles(render.panoramic(rng), ident, rng, ["header_strip"])


def _no_leak(path, ident):
    raw = path.read_bytes()
    return ident.family.encode() not in raw and ident.patient_id.encode() not in raw


def test_jpeg_baseline_dicom(tmp_path):
    rng, ident, r = _pan()
    ds = _dicom(r.image, ident, "PX", rng)
    for e in list(ds):
        if 0x6000 <= e.tag.group <= 0x601E:
            del ds[e.tag]
    buf = io.BytesIO()
    Image.fromarray(r.image).save(buf, "JPEG", quality=92)
    ds.PixelData = encapsulate([buf.getvalue()])
    ds["PixelData"].VR = "OB"
    ds.file_meta.TransferSyntaxUID = JPEGBaseline8Bit
    ds.LossyImageCompression = "01"
    ds.save_as(tmp_path / "j.dcm", enforce_file_format=True)
    rep = DeIdentifier(CFG).run([tmp_path / "j.dcm"], tmp_path / "o")
    fr = rep.files[0]
    assert fr.status in ("PASS", "REVIEW") and fr.actions["burned_in_text"]["regions_redacted"] >= 3
    out = tmp_path / "o" / fr.output
    d = pydicom.dcmread(out)
    assert d.file_meta.TransferSyntaxUID == "1.2.840.10008.1.2.1"      # re-written uncompressed
    assert d.LossyImageCompression == "01"                               # lossy history preserved
    assert _no_leak(out, ident)


def test_implicit_vr_without_preamble(tmp_path):
    rng, ident, r = _pan(6)
    ds = _dicom(r.image, ident, "PX", rng)
    ds.file_meta.TransferSyntaxUID = ImplicitVRLittleEndian
    ds.save_as(tmp_path / "i.dcm", enforce_file_format=False, implicit_vr=True, little_endian=True)
    rep = DeIdentifier(CFG).run([tmp_path / "i.dcm"], tmp_path / "o")
    assert rep.files[0].status in ("PASS", "REVIEW")
    assert _no_leak(tmp_path / "o" / rep.files[0].output, ident)


def test_multipage_tiff_keeps_first_page_and_warns(tmp_path):
    rng, ident, r = _pan(7)
    p2 = overlay.apply_styles(render.periapical(rng), ident, rng, ["corner_block"]).image
    Image.fromarray(r.image).save(tmp_path / "m.tif", save_all=True, append_images=[Image.fromarray(p2)])
    rep = DeIdentifier(CFG).run([tmp_path / "m.tif"], tmp_path / "o")
    fr = rep.files[0]
    assert fr.status == "REVIEW" and any(c.name == "multi-page-image" for c in fr.checks)
    out = tmp_path / "o" / fr.output
    with Image.open(out) as im:
        assert getattr(im, "n_frames", 1) == 1
    assert _no_leak(out, ident)


def test_output_folder_inside_input_is_ignored(tmp_path):
    rng, ident, r = _pan(8)
    Image.fromarray(r.image).save(tmp_path / "a.png")
    DeIdentifier(CFG).run([tmp_path], tmp_path / "out")
    rep = DeIdentifier(CFG).run([tmp_path], tmp_path / "out")          # second run must not re-ingest out/
    assert len(rep.files) == 1
