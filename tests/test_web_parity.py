"""Web app ↔ Python parity: the browser's detector (docs/detector.js) and 16-bit mapping
(docs/png16.js) must give the same boxes, the same image class and the same pixels as the
Python library, so the zero-install page behaves like ``dentdeid run``."""
import json
import shutil
import struct
import subprocess
import zlib
from dataclasses import fields
from pathlib import Path

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont
from pydicom.dataset import Dataset

from dentdeid import cli
from dentdeid.dicom.pixels import PixelData, read_pixels, to_display8
from dentdeid.imaging import raster
from dentdeid.imaging.textdetect import ALGORITHM_VERSION, DetectParams, detect_text, image_class, merge_regions
from dentdeid.synth import names, overlay, render

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "_parity_runner.js"
NODE = shutil.which("node") or next((p for p in ("/opt/node22/bin/node",) if Path(p).exists()), None)
pytestmark = pytest.mark.skipif(NODE is None, reason="node.js not installed")


def run_js(jobs, tmp_path, name="jobs.json"):
    jf = tmp_path / name
    jf.write_text(json.dumps(jobs))
    return json.loads(subprocess.check_output([NODE, str(RUNNER), str(jf)], timeout=600))


def dump(a: np.ndarray, f: Path) -> dict:
    a = np.ascontiguousarray(a)
    f.write_bytes(a.tobytes())
    return {"file": str(f), "width": a.shape[1], "height": a.shape[0], "channels": 1 if a.ndim == 2 else a.shape[2]}


def regions(rs) -> list[dict]:
    return [r.as_dict() for r in rs]


# ------------------------------------------------------------------------------------ images
def _font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def screenshot(rng, bar=(45, 95, 168), label=(255, 204, 0), chrome=0.0) -> np.ndarray:
    """A greyscale panoramic inside coloured viewer chrome: a blue title bar with the patient's
    name, a coloured annotation on the radiograph, and (``chrome``) a coloured side panel."""
    ident = names.make_identity(rng)
    g = render.panoramic(rng).image
    h, w = g.shape
    im = Image.fromarray(np.stack([g] * 3, -1))
    d = ImageDraw.Draw(im)
    d.rectangle((0, 0, w, 34), fill=bar)
    d.text((12, 8), f"{ident.family}, {ident.given}  DOB {ident.birth_date[6:]}.{ident.birth_date[4:6]}.{ident.birth_date[:4]}",
           fill=(255, 255, 255), font=_font(18))
    d.text((int(w * 0.55), int(h * 0.62)), f"{ident.family} 3.2 mm", fill=label, font=_font(16))
    if chrome:
        d.rectangle((0, 34, int(w * chrome), h), fill=(214, 226, 240))
        for i in range(8):
            d.rectangle((10, 50 + 40 * i, 30, 70 + 40 * i), fill=[(230, 60, 60), (60, 160, 90), (240, 170, 30)][i % 3])
    return np.asarray(im).copy()


@pytest.fixture(scope="module")
def images(tmp_path_factory) -> list[tuple[str, np.ndarray]]:
    rng = np.random.default_rng(2026)
    out: list[tuple[str, np.ndarray]] = []
    # 1) the synthetic renderers with the usual burned-in text layouts (grey and RGB)
    combos = [("panoramic", ["header_strip", "footer_technique"]), ("periapical", ["corner_block"]),
              ("bitewing", ["white_band"]), ("cephalometric", ["corner_block", "markers"]),
              ("cbct_screenshot", ["corner_block"]), ("panoramic", ["corner_block", "colored_label"]),
              ("periapical", ["colored_label"]), ("panoramic", ["over_anatomy"])]
    for kind, styles in combos:
        r = overlay.apply_styles(render.RADIOGRAPHS[kind](rng), names.make_identity(rng), rng, styles,
                                 hard="over_anatomy" in styles)
        out.append((f"render-{kind}-{'+'.join(styles)}", r.image))
    photo = overlay.apply_styles(render.intraoral_photo(rng), names.make_identity(rng), rng, ["corner_block"])
    out.append(("render-intraoral-photo", photo.image))
    # 2) `dentdeid synth -o <tmp> -n 6`: rasters as the pipeline sees them, DICOM first frames
    syn = tmp_path_factory.mktemp("synth")
    assert cli.main(["synth", "-o", str(syn), "-n", "6"]) == 0
    for p in sorted((syn / "images").glob("*")):
        arr, _, _ = raster.load(p)
        out.append((f"synth-{p.name}", raster.display8(arr)))
    import pydicom
    for p in sorted((syn / "dicom").glob("*.dcm")):
        ds = pydicom.dcmread(p)
        pd = read_pixels(ds)
        fr = pd.array[0] if pd.frames > 1 else pd.array
        out.append((f"synth-{p.name}", to_display8(fr, pd, ds)))
    # 3) colour screenshots: a radiograph inside coloured software chrome
    out.append(("screenshot-blue-bar", screenshot(rng)))
    out.append(("screenshot-red-label", screenshot(rng, bar=(20, 120, 90), label=(230, 40, 40))))
    out.append(("screenshot-side-panel", screenshot(rng, chrome=0.18)))
    out.append(("screenshot-mostly-chrome", screenshot(rng, chrome=0.62)))
    # 4) natural colour photographs (bundled with scikit-image, no download)
    try:
        from skimage import data as skd
        for n in ("astronaut", "chelsea", "coffee", "rocket"):
            try:
                out.append((f"skimage-{n}", np.ascontiguousarray(getattr(skd, n)()[..., :3])))
            except Exception:  # pragma: no cover - dataset not bundled in this version
                pass
    except ImportError:  # pragma: no cover
        pass
    return out


# ------------------------------------------------------------------------------------ tests
def test_version_and_defaults_match(tmp_path):
    meta = run_js([{"op": "meta"}], tmp_path)[0]
    assert meta["ALGORITHM_VERSION"] == ALGORITHM_VERSION
    py = {f.name: getattr(DetectParams(), f.name) for f in fields(DetectParams)}
    assert meta["DEFAULTS"] == py


def test_boxes_and_image_class_match_python(images, tmp_path):
    assert len(images) >= 20
    jobs, expected = [], []
    for i, (name, a) in enumerate(images):
        job = dump(a, tmp_path / f"{i}.bin")
        job["op"] = "both"
        jobs.append(job)
        expected.append({"imageClass": image_class(a), "regions": regions(detect_text(a)),
                         "regionsForced": regions(detect_text(a, force_color=True))})
    got = run_js(jobs, tmp_path)
    for (name, _), e, g in zip(images, expected, got):
        assert g == e, name
    classes = {e["imageClass"] for e in expected}
    assert classes == {"radiograph", "screenshot", "photo"}, classes
    assert sum(len(e["regions"]) for e in expected) > 20
    # the forced colour pass matters: a coloured label next to coloured chrome is only found with it
    assert any(e["regionsForced"] != e["regions"] for e in expected if e["imageClass"] == "screenshot")


def test_screenshot_title_bar_and_label_are_found(images):
    """The web app scans screenshots with force_color, like the pipeline."""
    a = dict(images)["screenshot-blue-bar"]
    assert image_class(a) == "screenshot"
    found = detect_text(a, force_color=True)
    assert any(r.y0 < 34 for r in found)                                   # the name in the title bar
    h, w = a.shape[:2]
    assert any(r.source in ("color", "mixed") and r.y0 > h * 0.5 for r in found)   # the coloured label


def _numpy_d8_cases():
    rng = np.random.default_rng(5)
    a = rng.integers(100, 900, (300, 420)).astype(np.uint16)
    a[20:32, 30:300] = 4095                                                # text at the top of the range
    return [("12bit-uniform", rng.integers(0, 4096, (240, 320)).astype(np.uint16)),
            ("narrow", rng.normal(1000, 40, (200, 260)).clip(0, 65535).astype(np.uint16)),
            ("constant", np.full((40, 50), 777, np.uint16)),
            ("full-range", rng.integers(0, 65536, (123, 77)).astype(np.uint16)),
            ("text-at-max", a),
            ("single-outlier", np.pad(np.ones((1, 1), np.uint16), ((0, 9), (0, 9)))),
            ("rgb16", rng.integers(0, 4096, (60, 80, 3)).astype(np.uint16))]


def test_display8_matches_python(tmp_path):
    jobs, expected = [], []
    for i, (name, a) in enumerate(_numpy_d8_cases()):
        f = tmp_path / f"d{i}.u16"
        a.astype("<u2").tofile(f)
        jobs.append({"op": "display8", "file": str(f), "out": str(tmp_path / f"d{i}.out")})
        expected.append((name, raster.display8(a)))
    # DICOM windowing (rescale slope/intercept, signed values) = dicom.pixels.to_display8
    rng = np.random.default_rng(9)
    for k, (slope, inter) in enumerate([("1", "0"), ("0.5", "-100"), ("2.5", "1024"), ("-1", "0")]):
        a = (rng.integers(-1024, 3000, (150, 200))).astype(np.int16)
        a[5:15, 10:120] = 3071
        ds = Dataset()
        ds.RescaleSlope, ds.RescaleIntercept = slope, inter
        exp = to_display8(a, PixelData(a, 1, "MONOCHROME2", False), ds)
        f = tmp_path / f"s{k}.i16"
        a.astype("<i2").tofile(f)
        jobs.append({"op": "display8", "file": str(f), "signed": True, "slope": float(slope), "intercept": float(inter),
                     "out": str(tmp_path / f"s{k}.out")})
        expected.append((f"dicom-{slope}-{inter}", exp))
    run_js(jobs, tmp_path)
    for j, (name, e) in zip(jobs, expected):
        got = np.fromfile(j["out"], np.uint8).reshape(e.shape)
        assert np.array_equal(got, e), (name, int((got != e).sum()))


def _write_png16(path: Path, arr: np.ndarray, color_type: int):
    """A 16-bit PNG whose rows cycle through all five scanline filters (Paeth included)."""
    h, w = arr.shape[:2]
    ch = 1 if arr.ndim == 2 else arr.shape[2]
    raw = np.ascontiguousarray(arr.astype(">u2")).view(np.uint8).reshape(h, w * ch * 2).astype(np.int32)
    bpp = 2 * ch
    prev = np.zeros(raw.shape[1], np.int32)
    body = bytearray()
    for y in range(h):
        line, ft = raw[y], y % 5
        left = np.concatenate([np.zeros(bpp, np.int32), line[:-bpp]])
        ul = np.concatenate([np.zeros(bpp, np.int32), prev[:-bpp]])
        if ft == 0:
            f = line
        elif ft == 1:
            f = line - left
        elif ft == 2:
            f = line - prev
        elif ft == 3:
            f = line - (left + prev) // 2
        else:
            p = left + prev - ul
            pa, pb, pc = abs(p - left), abs(p - prev), abs(p - ul)
            f = line - np.where((pa <= pb) & (pa <= pc), left, np.where(pb <= pc, prev, ul))
        body.append(ft)
        body += (f & 255).astype(np.uint8).tobytes()
        prev = line

    def chunk(t, data):
        return struct.pack(">I", len(data)) + t + data + struct.pack(">I", zlib.crc32(t + data) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 16, color_type, 0, 0, 0)) +
                     chunk(b"IDAT", zlib.compress(bytes(body), 6)) + chunk(b"IEND", b""))


def test_png16_decode_display_and_detection_match_pipeline(tmp_path):
    """A dark 12-bit export: the app's own PNG decoder + display8 + both detection views give the
    pipeline's pixels, image class and (merged) boxes — and the burned-in text is found."""
    rng = np.random.default_rng(3)
    r = overlay.apply_styles(render.panoramic(rng), names.make_identity(rng), rng, ["header_strip", "footer_technique"])
    g12 = r.image.astype(np.uint16) * 16 + rng.integers(0, 16, r.image.shape).astype(np.uint16)
    files = []
    f = tmp_path / "pillow16.png"
    Image.fromarray(g12).save(f)                                            # written by Pillow
    files.append(f)
    f = tmp_path / "filters16.png"
    _write_png16(f, g12, 0)                                                 # every filter type
    files.append(f)
    f = tmp_path / "ga16.png"                                               # grey + alpha
    _write_png16(f, np.stack([g12, np.full_like(g12, 65535)], -1), 4)
    files.append(f)
    jobs = [{"op": "png16", "png": str(p), "out": str(tmp_path / (p.stem + ".out"))} for p in files]
    got = run_js(jobs, tmp_path)
    for p, j, gv in zip(files, jobs, got):
        arr = g12
        views = raster.detection_views(arr)
        disp = np.fromfile(j["out"], np.uint8).reshape(views[0].shape)
        assert np.array_equal(disp, views[0]), p.name
        assert np.array_equal(np.fromfile(j["out"] + ".lin", np.uint8).reshape(views[1].shape), views[1]), p.name
        cls = image_class(views[0])
        exp = merge_regions([x for v in views for x in detect_text(v, force_color=(cls == "screenshot"))])
        assert gv["imageClass"] == cls == "radiograph"
        assert gv["regions"] == regions(exp), p.name
        for t in r.text:                                                    # the burned-in PHI is covered
            if t.phi:
                x0, y0, x1, y1 = t.bbox
                assert any(e.x0 <= x0 + 2 and e.y0 <= y0 + 2 and e.x1 >= x1 - 2 and e.y1 >= y1 - 2 for e in exp), t.category


def test_png16_rgb_is_mapped_over_colour_samples(tmp_path):
    rng = np.random.default_rng(4)
    a = rng.integers(0, 4096, (50, 70, 3)).astype(np.uint16)
    f = tmp_path / "rgb16.png"
    _write_png16(f, a, 2)
    got = run_js([{"op": "png16", "png": str(f), "out": str(tmp_path / "rgb16.out")}], tmp_path)[0]
    assert got["channels"] == 3 and got["viewChannels"] == 3
    disp = np.fromfile(tmp_path / "rgb16.out", np.uint8).reshape(a.shape)
    assert np.array_equal(disp, raster.display8(a))
