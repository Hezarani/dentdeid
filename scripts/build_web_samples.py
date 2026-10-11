#!/usr/bin/env python3
"""Regenerate the synthetic sample files used by the web app (docs/samples/).

Every identity is fake (DentDeID-Synth generator); the files deliberately carry burned-in
names, EXIF/GPS metadata, PNG text chunks and DICOM PHI so the demo has something to remove.
"""
from pathlib import Path

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from dentdeid.synth import names, overlay, render
from dentdeid.synth.corpus import _dicom, _exif_bytes

OUT = Path(__file__).resolve().parents[1] / "docs" / "samples"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(20260923)
    ident = names.make_identity(rng)
    pan = overlay.apply_styles(render.panoramic(rng, w=1400, h=700), ident, rng,
                               ["header_strip", "footer_technique", "markers"])
    Image.fromarray(pan.image).save(OUT / "sample-panoramic.jpg", "JPEG", quality=88,
                                    exif=_exif_bytes(ident, rng), comment=f"Patient {ident.family}".encode())
    ident2 = names.make_identity(rng)
    pa = overlay.apply_styles(render.periapical(rng), ident2, rng, ["corner_block", "colored_label"])
    info = PngInfo()
    info.add_text("Patient", ident2.dicom_name)
    info.add_text("Comment", f"ID {ident2.patient_id}")
    Image.fromarray(pa.image).save(OUT / "sample-periapical.png", pnginfo=info, compress_level=9)
    ident3 = names.make_identity(rng)
    cb = overlay.cbct_labels(render.cbct_screenshot(rng, w=1200, h=750), ident3, rng)
    Image.fromarray(cb.image).save(OUT / "sample-cbct-screenshot.png", compress_level=9)
    ident4 = names.make_identity(rng)
    bw = overlay.apply_styles(render.bitewing(rng), ident4, rng, ["white_band"])
    ds = _dicom(bw.image, ident4, "IO", rng)
    ds.save_as(OUT / "sample-bitewing.dcm", enforce_file_format=True)
    for f in sorted(OUT.iterdir()):
        print(f"{f.name}: {f.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
