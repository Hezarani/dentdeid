"""Score DentDeID (or any tool's output) against the DentDeID-Synth answer keys.

Metrics
-------
* **Burned-in PHI recall** — share of PHI text boxes (names, dates of birth, IDs, clinic,
  exam date) whose pixels are ≥ 90% covered by redaction boxes. Reported overall and by
  difficulty (standard / hard). Non-PHI text (technique factors, labels, markers) is scored
  separately so over-redaction is visible, not hidden.
* **False-positive area** — share of image pixels redacted outside any ground-truth text box.
* **Residual PHI in metadata** — every fake identifier string searched for in every string
  attribute of every output DICOM (recursively) and in raster metadata. Must be 0.
* **Face-risk flag** for the CBCT phantom.

Because the corpus and detector were developed together, synthetic scores are an **upper
bound**. They show the pipeline works as designed; they do not prove real-world recall.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np
import pydicom

from .config import Config, get_profile
from .imaging import raster
from .phi import KnownIdentifiers
from .pipeline import DeIdentifier


def _coverage(box, redactions) -> float:
    x0, y0, x1, y1 = box
    if x1 <= x0 or y1 <= y0:
        return 1.0
    m = np.zeros((y1 - y0, x1 - x0), dtype=bool)
    for (a0, b0, a1, b1) in redactions:
        ix0, iy0, ix1, iy1 = max(x0, a0), max(y0, b0), min(x1, a1), min(y1, b1)
        if ix1 > ix0 and iy1 > iy0:
            m[iy0 - y0:iy1 - y0, ix0 - x0:ix1 - x0] = True
    return float(m.mean())


def _fp_area(shape, gt_boxes, redactions, pad=6) -> float:
    h, w = shape
    red = np.zeros((h, w), dtype=bool)
    for (a0, b0, a1, b1) in redactions:
        red[max(0, b0):min(h, b1), max(0, a0):min(w, a1)] = True
    gt = np.zeros((h, w), dtype=bool)
    for (x0, y0, x1, y1) in gt_boxes:
        gt[max(0, y0 - pad):min(h, y1 + pad), max(0, x0 - pad):min(w, x1 + pad)] = True
    return float((red & ~gt).sum()) / (h * w)


def _strings_in_dicom(path: Path) -> list[str]:
    ds = pydicom.dcmread(path)
    vals: list[str] = []
    for e in ds.iterall():
        if e.VR in ("PN", "LO", "SH", "ST", "LT", "UT", "UC", "DA", "DT", "CS", "AE") and e.value not in (None, ""):
            vals.append(str(e.value))
    for e in ds.file_meta:
        if e.VR in ("AE", "SH", "LO", "UI"):
            vals.append(str(e.value))
    vals.append(ds.preamble.decode("latin-1") if ds.preamble else "")
    return vals


def run(corpus: str | Path, out_dir: str | Path, config: Config | str = "share") -> dict:
    corpus, out = Path(corpus), Path(out_dir)
    cfg = get_profile(config) if isinstance(config, str) else config
    t0 = time.time()
    deid = DeIdentifier(cfg)
    rep = deid.run([corpus / "dicom", corpus / "images", corpus / "cbct"], out / "deid")
    elapsed = time.time() - t0
    keys = {p.stem: json.loads(p.read_text("utf-8")) for p in sorted((corpus / "answer_keys").glob("*.json"))}
    pairs = []
    ct_reports = []
    for k in keys.values():
        if k["kind"] == "cbct_volume":
            fr = rep.sources.get(str(corpus / k["files"][0]))
            if fr is not None:
                ct_reports.append(fr)
            continue
        for field_ in ("dicom", "raster"):
            src = corpus / k[field_]
            fr = rep.sources.get(str(src))
            if fr is not None:
                pairs.append((k, fr))

    rows = []
    residual_hits = 0
    residual_detail = []
    for key, fr in pairs:
        boxes = [tuple(b) for b in fr.actions.get("burned_in_text", {}).get("boxes", [])]
        out_path = out / "deid" / fr.output if fr.output else None
        # image shape
        if fr.source_type == "dicom":
            ds = pydicom.dcmread(out_path, stop_before_pixels=True)
            shape = (int(ds.Rows), int(ds.Columns))
        else:
            arr, _, _ = raster.load(out_path)
            shape = arr.shape[:2]
        gt_phi = [tuple(t["bbox"]) for t in key["burned_in"] if t["phi"]]
        gt_non = [tuple(t["bbox"]) for t in key["burned_in"] if not t["phi"] and t["category"] != "marker"]
        cov_phi = [_coverage(b, boxes) for b in gt_phi]
        cov_non = [_coverage(b, boxes) for b in gt_non]
        fp = _fp_area(shape, [tuple(t["bbox"]) for t in key["burned_in"]], boxes)
        # residual PHI strings anywhere in the output's metadata / file name
        known = KnownIdentifiers()
        ident = key["identity"]
        known.add_name(f"{ident['family']}^{ident['given']}", "patient-name")
        known.add_id(ident["patient_id"], "patient-id")
        known.add_date(ident["birth_date"], "birth-date")
        known.add_name(f"{ident['dentist_family']}^{ident['dentist_given']}", "staff-name")
        known.add_id(ident["accession"], "accession")
        texts = [fr.output or ""]
        if out_path is not None:
            if fr.source_type == "dicom":
                texts += _strings_in_dicom(out_path)
            else:
                from PIL import Image
                with Image.open(out_path) as im:
                    texts += raster.text_metadata(im)
        hits = sorted({h.kind for t in texts for h in known.find(t)})
        if hits:
            residual_hits += 1
            residual_detail.append({"id": key["id"], "type": fr.source_type, "kinds": hits})
        rows.append({
            "id": key["id"], "type": fr.source_type, "kind": key["kind"], "difficulty": key["difficulty"],
            "status": fr.status, "phi_boxes": len(gt_phi), "phi_covered": sum(c >= 0.9 for c in cov_phi),
            "nonphi_boxes": len(gt_non), "nonphi_redacted": sum(c >= 0.9 for c in cov_non),
            "fp_area": round(fp, 5), "metadata_residual": hits,
        })

    def agg(sel):
        n = sum(r["phi_boxes"] for r in sel)
        c = sum(r["phi_covered"] for r in sel)
        return {"phi_boxes": n, "covered": c, "recall": round(c / n, 4) if n else None,
                "mean_fp_area": round(float(np.mean([r["fp_area"] for r in sel])), 5) if sel else None,
                "files": len(sel)}

    radiographs = [r for r in rows if r["kind"] != "intraoral_photo"]
    ct = []
    for fr in ct_reports:
        ct.append({"status": fr.status, "face_risk": fr.actions.get("face_risk", {}).get("level")})
    result = {
        "profile": cfg.profile, "ocr": "on" if deid.ocr_on else "off", "seconds": round(elapsed, 1),
        "files": len(rep.files), "status": rep.summary(),
        "burned_in_text": {
            "all": agg(radiographs),
            "standard": agg([r for r in radiographs if r["difficulty"] == "standard"]),
            "hard": agg([r for r in radiographs if r["difficulty"] == "hard"]),
            "dicom": agg([r for r in radiographs if r["type"] == "dicom"]),
            "raster": agg([r for r in radiographs if r["type"] == "raster"]),
            "nonphi_text_redacted": f"{sum(r['nonphi_redacted'] for r in radiographs)}/{sum(r['nonphi_boxes'] for r in radiographs)}",
        },
        "metadata": {"files_with_residual_phi": residual_hits, "details": residual_detail},
        "cbct": ct,
        "rows": rows,
    }
    out.mkdir(parents=True, exist_ok=True)
    (out / "benchmark.json").write_text(json.dumps(result, indent=1), "utf-8")
    (out / "BENCHMARK.md").write_text(markdown(result), "utf-8")
    return result


def markdown(r: dict) -> str:
    b = r["burned_in_text"]

    def line(name, a):
        rec = "—" if a["recall"] is None else f"{100 * a['recall']:.1f}%"
        fp = "—" if a["mean_fp_area"] is None else f"{100 * a['mean_fp_area']:.3f}%"
        return f"| {name} | {a['files']} | {a['covered']}/{a['phi_boxes']} | {rec} | {fp} |"

    L = [
        "# DentDeID-Synth benchmark", "",
        f"Profile **{r['profile']}**, OCR **{r['ocr']}**, {r['files']} inputs in {r['seconds']} s. "
        f"Status: {', '.join(f'{k} {v}' for k, v in sorted(r['status'].items()))}.", "",
        "## Burned-in identifiers (radiographs & screenshots)", "",
        "| Subset | Files | PHI boxes ≥90% covered | Recall | Mean false-positive area |",
        "|---|---|---|---|---|",
        line("All", b["all"]), line("Standard", b["standard"]), line("Hard", b["hard"]),
        line("DICOM", b["dicom"]), line("Exported images", b["raster"]), "",
        f"Non-PHI text (technique factors, labels) also redacted: {b['nonphi_text_redacted']} "
        "(the default policy removes all burned-in text).", "",
        "## Identifiers left in metadata / file names", "",
        f"Files with any residual fake identifier: **{r['metadata']['files_with_residual_phi']}**", "",
        "## CBCT phantom", "",
    ]
    for c in r["cbct"]:
        L.append(f"- face-risk flag: **{c['face_risk']}**, status {c['status']}")
    L += ["", "> Synthetic results are an upper bound: the corpus and the detector were developed "
              "together. Real-world recall must be measured on real, ethically approved data.", ""]
    return "\n".join(L)
