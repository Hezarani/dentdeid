#!/usr/bin/env python3
"""Write the Zenodo DOIs into README.md, CITATION.cff and the web app after a release.

    python scripts/set_doi.py --concept 10.5281/zenodo.1111111 --version-doi 10.5281/zenodo.1111112 \
                              [--dataset 10.5281/zenodo.2222222]

* concept DOI   – "all versions" DOI shown on the Zenodo record (cite this)
* version DOI   – the DOI of this particular release (v0.1.0)
* dataset DOI   – the DentDeID-Synth dataset record, if published
Also records the dataset as a related work in .zenodo.json (used for the next software release).
Safe to run more than once.
"""
import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--concept", required=True)
    ap.add_argument("--version-doi", required=True)
    ap.add_argument("--dataset")
    a = ap.parse_args()
    for d in (a.concept, a.version_doi, a.dataset):
        if d and not re.fullmatch(r"10\.\d{4,9}/\S+", d):
            raise SystemExit(f"not a DOI: {d}")

    # CITATION.cff
    cff = (ROOT / "CITATION.cff").read_text("utf-8")
    cff = re.sub(r"\ndoi:.*", "", cff)
    cff = re.sub(r"\nidentifiers:\n(?:  .*\n?)*", "\n", cff).rstrip() + "\n"
    cff += f"doi: {a.concept}\nidentifiers:\n"
    cff += f"  - type: doi\n    value: {a.concept}\n    description: \"Concept DOI — all versions (cite this)\"\n"
    cff += f"  - type: doi\n    value: {a.version_doi}\n    description: \"Version DOI — this release\"\n"
    if a.dataset:
        cff += f"  - type: doi\n    value: {a.dataset}\n    description: \"DentDeID-Synth benchmark dataset\"\n"
    (ROOT / "CITATION.cff").write_text(cff, "utf-8")

    # README badge + BibTeX + dataset line
    rd = (ROOT / "README.md").read_text("utf-8")
    badge = f"[![DOI](https://zenodo.org/badge/DOI/{a.concept}.svg)](https://doi.org/{a.concept})"
    rd = re.sub(r"\[!\[DOI\]\(https://zenodo\.org/badge/DOI/[^)]*\)\]\([^)]*\)\n?", "", rd)
    rd = rd.replace("[![DICOM PS3.15]", badge + "\n[![DICOM PS3.15]", 1)
    rd = re.sub(r"  url     = \{https://github.com/Hezarani/dentdeid\}(,\n  doi     = \{[^}]*\})?",
                f"  url     = {{https://github.com/Hezarani/dentdeid}},\n  doi     = {{{a.concept}}}", rd)
    rd = re.sub(r"\nDataset DOI: .*\n\n?", "\n", rd)
    if a.dataset:
        rd = rd.replace("## References & prior work",
                        f"Dataset DOI: [{a.dataset}](https://doi.org/{a.dataset}) (DentDeID-Synth v1.0, CC BY 4.0)\n\n"
                        "## References & prior work", 1)
    (ROOT / "README.md").write_text(rd, "utf-8")

    # web app footer
    ix = (ROOT / "docs" / "index.html").read_text("utf-8")
    ix = re.sub(r' · <a href="https://doi.org/[^"]*" rel="noopener">DOI [^<]*</a>', "", ix)
    ix = ix.replace('>How to cite</a></p>',
                    f'>How to cite</a> · <a href="https://doi.org/{a.concept}" rel="noopener">DOI {a.concept}</a></p>', 1)
    (ROOT / "docs" / "index.html").write_text(ix, "utf-8")
    # .zenodo.json: link the dataset (Zenodo reads this file at the next GitHub release)
    zp = ROOT / ".zenodo.json"
    z = json.loads(zp.read_text("utf-8"))
    rel = [r for r in z.get("related_identifiers", []) if r.get("resource_type") != "dataset"]
    if a.dataset:
        rel.append({"identifier": a.dataset, "relation": "isSupplementedBy", "resource_type": "dataset"})
    z["related_identifiers"] = rel
    zp.write_text(json.dumps(z, indent=2, ensure_ascii=False) + "\n", "utf-8")
    print("updated CITATION.cff, README.md, docs/index.html, .zenodo.json")


if __name__ == "__main__":
    main()
