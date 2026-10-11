import numpy as np
import pytest
from PIL import Image, ImageDraw

from dentdeid.imaging.textdetect import DetectParams, components, detect_text
from dentdeid.synth import names, overlay, render


def _covered(box, regions):
    x0, y0, x1, y1 = box
    m = np.zeros((y1 - y0, x1 - x0), bool)
    for r in regions:
        a0, b0, a1, b1 = max(x0, r.x0), max(y0, r.y0), min(x1, r.x1), min(y1, r.y1)
        if a1 > a0 and b1 > b0:
            m[b0 - y0:b1 - y0, a0 - x0:a1 - x0] = True
    return m.mean()


def test_components_match_opencv():
    cv2 = pytest.importorskip("cv2")
    rng = np.random.default_rng(0)
    for _ in range(20):
        m = rng.random((int(rng.integers(5, 90)), int(rng.integers(5, 90)))) < rng.uniform(0.05, 0.6)
        ours = sorted(components(m))
        _, _, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
        ref = sorted((int(s[0]), int(s[1]), int(s[0] + s[2]), int(s[1] + s[3]), int(s[4])) for s in st[1:])
        assert ours == ref


@pytest.mark.parametrize("kind,styles", [
    ("panoramic", ["header_strip", "footer_technique"]),
    ("periapical", ["corner_block"]),
    ("bitewing", ["white_band"]),
    ("cephalometric", ["corner_block"]),
    ("panoramic", ["corner_block", "colored_label"]),
])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_finds_all_standard_phi(kind, styles, seed):
    rng = np.random.default_rng(seed)
    ident = names.make_identity(rng)
    r = overlay.apply_styles(render.RADIOGRAPHS[kind](rng), ident, rng, styles)
    regions = detect_text(r.image)
    for t in r.text:
        if t.phi:
            assert _covered(t.bbox, regions) >= 0.9, (kind, t.category)


@pytest.mark.parametrize("seed", range(6))
def test_no_false_positives_on_dental_confounders(seed):
    """Restorations, crowns, implants, root fillings, brackets+wire and bone texture are not text."""
    rng = np.random.default_rng(100 + seed)
    for fn in (render.panoramic, render.periapical, render.bitewing):
        r = fn(rng)
        assert detect_text(r.image) == [], (fn.__name__, r.confounders)


def test_cbct_screenshot_labels_and_identity_found():
    rng = np.random.default_rng(5)
    ident = names.make_identity(rng)
    r = overlay.cbct_labels(render.cbct_screenshot(rng), ident, rng)
    regions = detect_text(r.image)
    assert all(_covered(t.bbox, regions) >= 0.9 for t in r.text)
    assert max(rr.area for rr in regions) < 0.02 * r.image.shape[0] * r.image.shape[1]


def test_scale_is_classified():
    img = Image.new("L", (400, 200), 20)
    d = ImageDraw.Draw(img)
    for k in range(20):                              # disconnected, evenly spaced thin ticks
        d.line([(50 + 8 * k, 40), (50 + 8 * k, 40 + (14 if k % 5 == 0 else 9))], fill=240, width=1)
    regs = detect_text(np.asarray(img))
    assert regs and all(r.kind == "scale" for r in regs)


def test_dark_text_on_light_band():
    img = Image.new("L", (600, 300), 40)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, 600, 30), fill=255)
    from PIL import ImageFont
    d.text((10, 6), "Patient: Kavo Temelson 1234567", font=ImageFont.load_default(size=16), fill=0)
    regs = detect_text(np.asarray(img))
    assert regs and regs[0].y0 < 30 and regs[0].source in ("dark", "mixed")


def test_deterministic_and_uint8_only():
    rng = np.random.default_rng(9)
    r = overlay.apply_styles(render.panoramic(rng), names.make_identity(rng), rng, ["header_strip"])
    assert detect_text(r.image) == detect_text(r.image.copy())
    with pytest.raises(TypeError):
        detect_text(r.image.astype(np.uint16))


def test_sensitive_pass_can_be_disabled():
    p = DetectParams(sensitive_pass=False)
    rng = np.random.default_rng(4)
    r = overlay.apply_styles(render.periapical(rng), names.make_identity(rng), rng, ["corner_block"])
    assert detect_text(r.image, p)
