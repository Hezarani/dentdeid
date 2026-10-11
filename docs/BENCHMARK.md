# DentDeID-Synth benchmark (v0.1.0)

Reproduce:

```bash
dentdeid synth -o corpus -n 60 --seed 2026        # 60 cases × (DICOM + exported image) + 1 CBCT phantom
dentdeid bench corpus -o bench --ocr off
dentdeid bench corpus -o bench-ocr --ocr on       # needs Tesseract
```

The corpus is byte-for-byte the published **DentDeID-Synth v1.0** dataset (Zenodo). Profile `share`.
Text metrics use the 50 radiograph/screenshot cases (100 files); the 10 intra-oral photographs carry
no burned-in text. A PHI box counts as removed when ≥ 90% of its pixels are covered by redactions.
False-positive area is the redacted area outside every ground-truth text box, as a mean percentage
per image.

## Burned-in identifiers

| Subset | Removed (OCR off) | False-positive area | Removed (OCR on) | False-positive area |
|---|---|---|---|---|
| All | 358/370 (96.8%) | 0.126% | 358/370 (96.8%) | 0.209% |
| Standard layouts | 300/300 (100.0%) | 0.143% | 300/300 (100.0%) | 0.241% |
| Hard: small, faint text over anatomy, JPEG q70 | 58/70 (82.9%) | 0.038% | 58/70 (82.9%) | 0.042% |
| DICOM objects | 179/185 (96.8%) | 0.127% | 179/185 (96.8%) | 0.218% |
| Exported JPEG/PNG | 179/185 (96.8%) | 0.125% | 179/185 (96.8%) | 0.201% |

Non-PHI burned-in text (technique factors, labels, W/L) also removed: 134/134 — the default policy removes all text.
Run time for the 121 inputs on a 2-core cloud machine: 42 s with OCR off, 318 s with OCR on.

**Silent misses.** The hard subset has 8 cases (16 files). In 6 of them (12 files) one faint
identifier was missed, and all 12 files were marked PASS: no automated check (including OCR) noticed.
This is the main limitation of automatic pixel cleaning and the reason every output has a review image.

## Metadata, file names, faces, CBCT

| Check | Result |
|---|---|
| Output files containing the fake patient name, patient ID, birth date (all renderings), dentist name or accession number (every DICOM string attribute recursively, File Meta, preamble, image metadata, file name) | **0** |
| Status of all 121 inputs (OCR off) | PASS 100, REVIEW 21 (the 20 intra-oral photographs — every photograph is reviewed — and the CBCT phantom) |
| CBCT phantom (153.6 × 153.6 × 89.6 mm field of view, facial surface) | face-risk **high**, status REVIEW |
| Detector false positives on 18 confounder-only radiographs (restorations, crowns, root fillings, implants, brackets + wire, bone) | **0** (unit test, separate from the corpus) |
| Python vs JavaScript detector | identical boxes and image classes (unit tests) |

## Interpretation

- The corpus and the detector were developed together; treat these numbers as an **upper bound**.
- OCR did not improve recall here and increased false-positive area and run time; the `research`
  profile therefore leaves it off by default. Real burned-in fonts may be easier for OCR than the
  deliberately degraded hard cases, so OCR remains available (`--ocr on`).
- Adversarial cases found in the pre-release audit (colour screenshots, JPEG-in-DICOM metadata, hidden
  high bits, text on one frame of a stack, 16-bit exports, name-bearing file names …) are covered by
  `tests/test_audit_regressions.py` rather than by this benchmark.
- The next, most important step is validation on real, ethically approved multi-vendor dental data.
