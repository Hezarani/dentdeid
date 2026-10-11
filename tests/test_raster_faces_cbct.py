import numpy as np
import pytest
from PIL import Image

from dentdeid.dicom import cbct
from dentdeid.imaging import faces, raster
from dentdeid.synth.corpus import _exif_bytes, cbct_phantom


# ---------------- raster metadata ---------------------------------------------------------
def test_metadata_is_inventoried_and_removed(tmp_path, ident):
    rng = np.random.default_rng(1)
    src = tmp_path / "Kara_Zeynep_1990-01-02.jpg"
    Image.fromarray(np.full((80, 120, 3), 120, np.uint8)).save(src, "JPEG", exif=_exif_bytes(ident, rng),
                                                               comment=b"Patient Kara")
    arr, inv, texts = raster.load(src)
    assert inv["gps"] and "Artist" in inv["exif_tags"] and inv["total_items"] > 3
    assert any(ident.family in t for t in texts)
    out = tmp_path / "clean.jpg"
    raster.save_clean(arr, out, "JPEG")
    assert raster.residual_metadata(out) == []


def test_png_text_chunks_removed(tmp_path):
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    info.add_text("Patient", "Kara^Zeynep")
    src = tmp_path / "x.png"
    Image.fromarray(np.zeros((20, 20), np.uint8)).save(src, pnginfo=info)
    assert "Patient" in raster.inventory(Image.open(src))["other_info_keys"]
    arr, _, _ = raster.load(src)
    out = tmp_path / "y.png"
    raster.save_clean(arr, out, "PNG")
    assert raster.residual_metadata(out) == []


def test_exif_orientation_is_applied(tmp_path):
    ex = Image.Exif()
    ex[0x0112] = 6                                   # rotate 90° CW on display
    src = tmp_path / "o.jpg"
    Image.fromarray(np.zeros((40, 100, 3), np.uint8)).save(src, "JPEG", exif=ex.tobytes())
    arr, _, _ = raster.load(src)
    assert arr.shape[:2] == (100, 40)


def test_sixteen_bit_png_kept(tmp_path):
    a = (np.arange(64 * 64, dtype=np.uint16).reshape(64, 64) * 13) % 4096
    src = tmp_path / "r16.png"
    Image.fromarray(a).save(src)
    arr, _, _ = raster.load(src)
    assert arr.dtype == np.uint16 and np.array_equal(arr, a)
    assert raster.display8(arr).dtype == np.uint8


# ---------------- faces ---------------------------------------------------------------------
@pytest.fixture(scope="module")
def face_img():
    pytest.importorskip("cv2")
    data = pytest.importorskip("skimage.data")
    return data.astronaut()           # NASA public-domain portrait bundled with scikit-image


def test_face_detected_then_obscured(face_img):
    fs = faces.detect_faces(face_img)
    assert len(fs) == 1
    out, boxes = faces.apply_face_action(face_img, fs, "blur")
    assert boxes and faces.detect_faces(out) == []
    filled, _ = faces.apply_face_action(face_img, fs, "fill")
    b = boxes[0]
    assert filled[b[1]:b[3], b[0]:b[2]].max() == 0


def test_perioral_crop(face_img):
    fs = faces.detect_faces(face_img)
    crop, box = faces.apply_face_action(face_img, fs, "crop-perioral")
    assert crop.shape[0] < fs[0].h and crop.shape[1] < fs[0].w
    assert faces.detect_faces(crop) == []


def test_face_action_validation():
    with pytest.raises(ValueError):
        faces.apply_face_action(np.zeros((10, 10, 3), np.uint8), [], "eyebar")


# ---------------- CBCT ----------------------------------------------------------------------
def _dss(n, spacing=1.6, size=96):
    from pydicom.dataset import Dataset
    ds = Dataset()
    ds.PixelSpacing = [spacing, spacing]
    ds.Rows = ds.Columns = size
    ds.SliceThickness = spacing
    return [ds] * n


def test_cbct_face_risk_and_strip():
    rng = np.random.default_rng(0)
    vol = cbct_phantom(rng)
    risk = cbct.assess_face_risk(vol, _dss(vol.shape[0]))
    assert risk.level == "high"
    t_air, t_bone = cbct.thresholds(vol)
    assert 0 < t_air < 1015 and 1065 < t_bone < 2140      # between the phantom's tissue classes
    out, info = cbct.strip_soft_tissue(vol)
    soft = (out > t_air) & (out < t_bone)
    assert soft.sum() == 0
    assert (out >= 2100).sum() == (vol >= 2100).sum()          # bone and teeth untouched
    assert info["voxels_set_to_air"] > 0


def test_cbct_shell_reduces_surface_only():
    rng = np.random.default_rng(0)
    vol = cbct_phantom(rng)
    out, info = cbct.shell_soft_tissue(vol, 4)
    t_air, t_bone = cbct.thresholds(vol)
    before = ((vol > t_air) & (vol < t_bone)).sum()
    after = ((out > t_air) & (out < t_bone)).sum()
    assert 0 < after < before and info["voxels_set_to_air"] == before - after


def test_small_fov_is_low_risk():
    rng = np.random.default_rng(0)
    vol = cbct_phantom(rng, n=20, size=96)
    risk = cbct.assess_face_risk(vol, _dss(20, spacing=0.4))
    assert risk.level == "low"
