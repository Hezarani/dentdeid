#!/usr/bin/env python3
"""Build the DentDeID-Synth v1.0 dataset release (a zip for Zenodo, separate from the software).

    python scripts/make_dataset_release.py [--out dist-dataset]
        -> dist-dataset/DentDeID-Synth-v1.0-partNofM.zip + SHA256SUMS + zenodo-dataset-metadata.json

The output is byte-for-byte reproducible (fixed seed, no dates in the data, fixed zip timestamps).

The corpus is fully synthetic and regenerable (`dentdeid synth -n 60 --seed 2026`); publishing
the files with their own DOI lets other groups benchmark their de-identification tools on exactly
the same data and cite it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dentdeid import __version__  # noqa: E402
from dentdeid.synth.corpus import build  # noqa: E402

NAME = "DentDeID-Synth"
VERSION = "1.0"
N_ITEMS = 60
SEED = 2026

README = f"""# {NAME} v{VERSION}

A **fully synthetic** benchmark for de-identifying dental images. No real patient, clinic or
dentist appears anywhere: identities are assembled from neutral syllables, images are drawn
procedurally, and IDs carry no national-ID checksums.

Generated with DentDeID {__version__} (`dentdeid synth -n {N_ITEMS} --seed {SEED}`).

## Contents

| Folder | What | Count |
|---|---|---|
| `dicom/` | DICOM objects: IO (periapical, bitewing), PX (panoramic), DX (cephalometric), OT (CBCT screenshot), XC (intra-oral photo). PHI in the header (names, IDs, birth date, address, phone, clinic, dentist, accession, descriptions, comments), a private vendor block, overlay planes, icon images, nested request sequences, dates/times, UIDs, File Meta source AE title, a non-empty preamble, and a vendor-misused technical field. Includes 16-bit (12-bit stored) and MONOCHROME1 variants. | {N_ITEMS} |
| `images/` | The same pictures as JPEG/PNG "exports": burned-in text, EXIF (description, artist, date, camera serial, **GPS**), JPEG comments, PNG text chunks, and patient names + birth dates in the **file names**. | {N_ITEMS} |
| `cbct/v0001/` | A CBCT phantom series (CT storage, 56 slices, 96×96, 1.6 mm) with a facial soft-tissue surface (nose, lips), a bony shell and teeth. | 56 |
| `answer_keys/` | One JSON per item: the fake identity, every PHI string, each burned-in text box (with `phi` true/false and a category), calibration scales, confounders present, and difficulty. | {N_ITEMS + 1} |
| `manifest.json` | Seed, generator version, item list. | 1 |
| `LICENSE.txt` | CC BY 4.0 notice. | 1 |
| `SHA256SUMS.txt` | Checksums of every file. | 1 |

**Difficulty.** 8 of the 40 radiographs (panoramic, periapical, bitewing, cephalometric) are *hard*:
small (9–12 px), low-contrast text drawn over teeth and bone, saved as JPEG quality 70. CBCT
screenshots and intra-oral photographs are never hard.

**Confounders** a text detector must not redact: metal restorations, prosthetic crowns,
root-canal fillings, implants with threads, orthodontic brackets on an arch-wire, trabecular bone,
panel borders, calibration rulers.

**Download:** the dataset is split into standalone parts (`…-partNofM.zip`) to stay under upload
limits. Unzip **all** parts into the same folder; part 1 holds this README, the manifest, the
answer keys and the checksums.

This is exactly the corpus used for the results in DentDeID's `docs/BENCHMARK.md`, so those numbers
can be reproduced on these files: `dentdeid bench DentDeID-Synth-v1.0 -o results`.

## Suggested metrics

1. Burned-in PHI recall: share of `phi: true` boxes ≥ 90% covered by your redactions (overall,
   standard vs hard, DICOM vs export).
2. False-positive area: redacted pixels outside any ground-truth text box.
3. Residual PHI: the fake patient name, patient ID, birth date (in 30+ spellings), dentist name and
   accession number searched in every output attribute, metadata field and file name (should be
   zero); `phi_strings` lists further values (address, phone, clinic …) for stricter checks.
4. CBCT: whether the facial re-identification risk is flagged.

The reference implementation of these metrics is `dentdeid bench` (https://github.com/Hezarani/dentdeid).

## Limitations

The images are schematic and were designed together with the DentDeID detector: scores on this set
are an upper bound, not evidence of real-world performance.

## Licence

Creative Commons Attribution 4.0 International (CC BY 4.0) — https://creativecommons.org/licenses/by/4.0/

## Citation

Please cite both the dataset (Zenodo DOI) and the software:
Boustani Hezarani, H. (2026). DentDeID-Synth: a synthetic benchmark for de-identifying dental images (v{VERSION}). Zenodo.
"""

ZENODO = {
    "title": f"{NAME}: a synthetic benchmark for de-identifying dental images",
    "version": VERSION,
    "upload_type": "dataset",
    "description": (
        "A fully synthetic benchmark for de-identification of dental images: 60 cases written both as DICOM "
        "(intra-oral, panoramic, cephalometric, CBCT screenshot, intra-oral photograph) with PHI in headers, "
        "private tags, overlays and icons, and as JPEG/PNG exports with burned-in text, EXIF/GPS metadata, PNG "
        "text chunks and identifying file names; plus a CBCT phantom series with a facial soft-tissue surface. "
        "Every item has a JSON answer key (fake identity, PHI strings, burned-in text boxes, confounders, "
        "difficulty). Contains no real patient data. Generated with DentDeID (seed 2026); it is the exact corpus "
        "behind the published DentDeID v0.1.0 benchmark. Distributed as standalone zip parts: unzip all parts into "
        "one folder. Synthetic results are an upper bound: the corpus and the DentDeID detector were developed "
        "together."
    ),
    "access_right": "open",
    "license": "cc-by-4.0",
    "creators": [{"name": "Boustani Hezarani, Hossein", "orcid": "0009-0007-8481-0498",
                  "affiliation": "Independent researcher, Ankara, Türkiye"}],
    "keywords": ["de-identification", "dental imaging", "synthetic data", "benchmark", "DICOM",
                 "burned-in text", "privacy", "panoramic radiograph", "CBCT", "dentistry"],
    "related_identifiers": [{"identifier": "https://github.com/Hezarani/dentdeid",
                             "relation": "isSupplementTo", "resource_type": "software"}],
}


LICENSE_TXT = f"""{NAME} v{VERSION} — synthetic dental imaging de-identification benchmark
Copyright (c) 2026 Hossein Boustani Hezarani

Licensed under the Creative Commons Attribution 4.0 International License (CC BY 4.0).
You may share and adapt the material for any purpose, provided you give appropriate credit,
link to the licence and indicate if changes were made.
Full licence text: https://creativecommons.org/licenses/by/4.0/legalcode

All identities, images and metadata in this dataset are synthetic. It contains no real patient data.
"""
_ZIP_TIME = (2026, 1, 1, 0, 0, 0)          # fixed timestamp: identical zips on every rebuild


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build the DentDeID-Synth dataset release zips.")
    ap.add_argument("--out", default=str(ROOT / "dist-dataset"), help="output folder (default: dist-dataset/)")
    a = ap.parse_args(argv)
    out = Path(a.out)
    work = out / f"{NAME}-v{VERSION}"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    build(work, n=N_ITEMS, seed=SEED, include_cbct=True)
    (work / "README.md").write_text(README, "utf-8")
    (work / "LICENSE.txt").write_text(LICENSE_TXT, "utf-8")
    sums = []
    for f in sorted(p for p in work.rglob("*") if p.is_file()):
        sums.append(f"{hashlib.sha256(f.read_bytes()).hexdigest()}  {f.relative_to(work).as_posix()}")
    (work / "SHA256SUMS.txt").write_text("\n".join(sums) + "\n", "utf-8")
    # Split into standalone zips, each well under common upload limits (<= ~25 MB); unzipping
    # all parts into one folder recreates the dataset. Part 1 always carries README/manifest/keys.
    for old in out.glob(f"{NAME}-v{VERSION}*.zip"):
        old.unlink()
    files = sorted(p for p in work.rglob("*") if p.is_file())
    head = [f for f in files if f.parent == work or f.parts[-2] == "answer_keys"]
    rest = [f for f in files if f not in head]
    parts, cur, size = [], list(head), sum(f.stat().st_size for f in head)
    limit = 42_000_000  # uncompressed bytes per part (zips come out at ~20-25 MB)
    for f in rest:
        if cur and size + f.stat().st_size > limit:
            parts.append(cur)
            cur, size = [], 0
        cur.append(f)
        size += f.stat().st_size
    if cur:
        parts.append(cur)
    for i, group in enumerate(parts, start=1):
        zpath = out / f"{NAME}-v{VERSION}-part{i}of{len(parts)}.zip"
        with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
            for f in group:
                info = zipfile.ZipInfo(f"{NAME}-v{VERSION}/{f.relative_to(work).as_posix()}", _ZIP_TIME)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                z.writestr(info, f.read_bytes())
        print(f"wrote {zpath.name} ({zpath.stat().st_size / 1e6:.1f} MB, {len(group)} files)")
    (out / "zenodo-dataset-metadata.json").write_text(json.dumps(ZENODO, indent=2, ensure_ascii=False), "utf-8")
    print(f"wrote {out / 'zenodo-dataset-metadata.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
