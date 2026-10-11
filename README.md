<p align="center">
  <img src="https://raw.githubusercontent.com/Hezarani/dentdeid/main/assets/banner.png" alt="DentDeID — share the case, not the patient" width="100%">
</p>

# DentDeID

**Open, tested de-identification for dental images.** DentDeID removes what identifies a patient
from dental radiographs, CBCT, screenshots and clinical photographs — DICOM headers (per **DICOM
PS3.15, 2026d**), **burned-in text**, **EXIF/GPS metadata**, **file names** and **faces** — and tells
you exactly what it did. Its reports are designed never to contain a patient identifier.

[![PyPI](https://img.shields.io/pypi/v/dentdeid.svg)](https://pypi.org/project/dentdeid/)
[![Python](https://img.shields.io/badge/python-3.10–3.13-blue.svg)](https://pypi.org/project/dentdeid/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/Hezarani/dentdeid/blob/main/LICENSE)
[![Tests](https://github.com/Hezarani/dentdeid/actions/workflows/tests.yml/badge.svg)](https://github.com/Hezarani/dentdeid/actions/workflows/tests.yml)
[![Live demo](https://img.shields.io/badge/demo-in%20your%20browser-0a7c86.svg)](https://hezarani.github.io/dentdeid/)
[![DICOM PS3.15](https://img.shields.io/badge/DICOM-PS3.15%202026d-0a7c86.svg)](https://github.com/Hezarani/dentdeid/blob/main/docs/COMPLIANCE.md)

> ⚠️ **De-identified is not anonymous.** Teeth, restorations and implants are identifying — in 2026
> forensic studies, deep-learning systems ranked the right person first for 65–75% of panoramic
> radiographs when searching research galleries of about 200 people. DentDeID removes *direct*
> identifiers; share the result only with consent or under a data-use agreement, and **look at every
> image before it leaves your hands.** Not legal advice — see [docs/COMPLIANCE.md](https://github.com/Hezarani/dentdeid/blob/main/docs/COMPLIANCE.md).

---

## Why this exists

Dental images leak identity in ways general medical tools don't cover. They often travel as
**JPEG/PNG exports and screenshots** rather than clean DICOM — in March 2026 the American Dental
Association told the US Department of Health and Human Services that dental "images are often
transmitted as screenshots, PDFs, or secondary captures, stripping essential metadata". A single
export can carry the patient's name **burned into the pixels**, the clinic's **GPS position** in EXIF,
and the date of birth in the **file name**.

Mature open tools exist for general radiology (pydicom *deid*, dicognito, RSNA CTP, Presidio, MEDE),
and the MICCAI 2024 MIDI-B challenge benchmarked de-identification pipelines on oncology imaging. To
our knowledge none is designed for or validated on **dental imaging**: intra-oral and panoramic
layouts, colour measurement labels on greyscale films, crowns and implants that fool text detectors,
orthodontic face photos, or CBCT volumes that can be rendered into a face. DentDeID fills that gap and
is designed to be **cited, audited and extended**. Background: [docs/EVIDENCE.md](https://github.com/Hezarani/dentdeid/blob/main/docs/EVIDENCE.md) ·
papers it builds on: [References & prior work](#references--prior-work).

## Two ways to use it

### 1. In your browser — for clinicians sharing a case

**[hezarani.github.io/dentdeid](https://hezarani.github.io/dentdeid/)** — drop a radiograph, photo,
screenshot or DICOM file. DentDeID finds burned-in text, lists the hidden metadata, lets you hide a
face or keep only the smile, and downloads a clean copy. **Nothing is uploaded** — it runs entirely in
the tab.

### 2. In Python — for batches, DICOM-to-DICOM and research datasets

```bash
pip install dentdeid                  # Python 3.10–3.13; add [ocr] + the Tesseract program for optional OCR
```

```bash
dentdeid inspect exports/                                   # read-only: what identifying data is there?
dentdeid run exports/ -o deidentified/                      # 'share' profile (default)
dentdeid run study/ -o dataset/ --profile research --key-file ~/secure/project.key
```

```python
import dentdeid

report = dentdeid.deidentify(["exports/"], "deidentified/", profile="share")
print(report.summary())          # e.g. {'PASS': 41, 'REVIEW': 3}
```

Output folder (always a new folder, never the input folder):

```
deidentified/
├── clean/                 de-identified files (PASS / REVIEW), neutral names
├── review/                preview PNGs with every redaction outlined — look at these
├── quarantine/            anything that FAILED a check (not safe to share)
├── dentdeid-report.md     human-readable audit (no patient identifiers inside)
└── dentdeid-report.json   machine-readable audit
```

## What it removes

| Where identity hides | What DentDeID does |
|---|---|
| **DICOM header** | Full PS3.15 Basic Profile — 656 attribute rules **generated from the standard's own XML** — plus options (clean descriptors, clean structured content via Table E.3.4-1, retain age/sex with HIPAA ≥ 90 capping, per-patient date shifting …). Private tags, overlays, icons, URLs, opaque binary values, File Meta and preamble removed or replaced. A **safety net** catches anything the table doesn't list; an optional **dental allow-list** keeps only known-safe attributes. |
| **Burned-in text** | A dental-aware detector (morphological top-hat) finds header strips, corner blocks, colour labels and screenshot title bars; it is designed not to mistake crowns, restorations, implants, brackets or bone for text. Every frame and every CBCT slice is checked. Stored pixel values are blacked out (never just overlaid), and pixel data is always re-written — which also drops hidden high bits, trailing frames and metadata inside JPEG streams. Optional OCR. |
| **Embedded metadata** | EXIF (incl. **GPS**), XMP, IPTC, comments, PNG text chunks, ICC — the output contains pixels only. EXIF orientation is applied first. Names typed into the metadata are also searched for in the file name. |
| **File names** | Replaced with neutral pseudonymous names. |
| **Faces** | Extra-oral photos: blur (auto-escalates to fill if a face is still detectable), fill, or **crop to the smile**. Eye-bars are not offered: in a study with photographs of well-known people, 55.5% were still recognised with *all* facial features covered. |
| **CBCT / CT volumes** | Facial re-identification risk assessed and flagged (single-slice series and multi-frame objects); experimental soft-tissue removal. |
| **The patient's own identifiers everywhere** | Name parts, IDs and birth date (in 30+ spellings, also without separators and with umlaut transliterations) are harvested *before* cleaning and searched for in every remaining text field, OCR result and file name. |

Every output is **re-opened and re-checked** (private tags, residual identifiers, URLs and binary
values, file meta, residual text on every frame, faces, metadata). A check that can't run **fails
closed** into `quarantine/`, and one unreadable file never stops the batch. Reports, structured
reports, PDFs and DICOMDIRs are not images: they are reported as SKIPPED and never copied.

**Photographs vs screenshots.** A screenshot — a greyscale image inside software chrome, dark or
light themed — is scanned like a radiograph, and faces in it are handled too. In camera photographs
natural texture looks like text, so text-like areas are outlined in blue for your review instead of
being blacked out (`--text-in-photos` redacts them), and **every photograph is marked REVIEW**.

**Speed.** About 0.3–0.4 s per image on a 2-core machine; OCR (used automatically when Tesseract is
installed, off in the `research` profile) makes a run about 7× slower.

### Profiles

| Profile | For | Key | Keeps |
|---|---|---|---|
| `share` (default) | Case reports, forums, colleagues, teaching | one-time, discarded | only the dental allow-list |
| `research` | Building datasets | `--key-file` (consistent pseudonyms across runs) | age & sex (≥ 90 capped), dates shifted per patient, rulers, structured content known to be safe |
| `basic` | Plain PS3.15 Basic Profile + Clean Pixel Data | one-time | what the standard keeps |

`dentdeid profiles` prints them with the rule-table provenance.

## Validation

DentDeID ships **DentDeID-Synth**, a fully synthetic benchmark (fake identities, procedural radiographs,
answer keys), and a scorer. On 60 cases (each as DICOM *and* as an exported image) plus a CBCT phantom:

| Metric | Result |
|---|---|
| Burned-in identifiers removed — standard layouts | **300 / 300 (100%)** |
| Burned-in identifiers removed — *hard* (small, faint text over teeth/bone, JPEG) | **58 / 70 (82.9%)** |
| Image area redacted unnecessarily | 0.126% (mean per image) |
| Files with any fake identifier left in metadata or file name | **0** |
| False positives on dental confounders (18 unit-test radiographs with crowns, implants, brackets…) | **0** |
| CBCT phantom face-risk flag | **high** ✔ |
| Python ↔ JavaScript detector agreement | identical boxes and image classes |

**Read this honestly:** the benchmark and detector were built together, so these are **upper bounds**.
In 6 of the 8 hard cases (12 of 16 files) one faint identifier was missed and **no automated check
flagged it** — which is why a human review of the previews is part of the workflow, not an optional
extra. Reproduce with `dentdeid synth -o corpus -n 60 --seed 2026 && dentdeid bench corpus -o bench` —
the same corpus is published as the **DentDeID-Synth v1.0** dataset. Details:
[docs/BENCHMARK.md](https://github.com/Hezarani/dentdeid/blob/main/docs/BENCHMARK.md). The test suite (169 tests, including regression
tests for every issue found in AI-assisted pre-release audits) runs on every push.

## Standards & compliance

- **DICOM PS3.15 2026d** Basic Application Level Confidentiality Profile with Clean Pixel Data, Clean
  Recognizable Visual Features, Clean Structured Content, Clean Descriptors (pattern-based), Retain
  Longitudinal (full or modified dates), Retain Patient Characteristics, Retain Device/Institution
  Identity and Retain UIDs; CID 7050 method codes recorded only for what was actually applied.
  → [conformance statement](https://github.com/Hezarani/dentdeid/blob/main/docs/COMPLIANCE.md#1-dicom-ps315-annex-e-conformance-statement)
- **HIPAA Safe Harbor** identifier-by-identifier mapping, including why face-like images and keyed
  pseudonyms make most dental data an *Expert Determination* question. → [mapping](https://github.com/Hezarani/dentdeid/blob/main/docs/COMPLIANCE.md#2-hipaa-safe-harbor-45-cfr-1645142-identifier-mapping)
- **GDPR / KVKK**: pseudonymised ≠ anonymous; keyed pseudonyms as a safeguard. → [notes](https://github.com/Hezarani/dentdeid/blob/main/docs/COMPLIANCE.md#3-gdpr--uk-gdpr--turkish-kvkk)

## Limitations

- Faint text drawn over anatomy can be missed **silently** — always review.
- Free text that contains a name DentDeID has never seen (not in the header, no title such as
  "Dr.") cannot be recognised with certainty; long comments that read like names are dropped,
  short descriptions are flagged for review.
- Face detection uses classical Haar cascades; profile, partial and unusual faces can be missed, and
  greyscale photographs are treated like radiographs (no face detection).
- Tinted or pseudo-colour renderings of radiographs (sepia, colour look-up tables) are treated as
  photographs: text in them is outlined for review, not removed automatically.
- CBCT soft-tissue removal is experimental and **not validated** against face-recognition attacks.
- Validated on synthetic data only; real-world, multi-vendor validation is the next step (contributions welcome).
- Retain Safe Private, Clean Graphics and the Encrypted Attributes Sequence are not implemented; all
  private tags and graphic annotations are removed.
- Python 3.14 is not yet supported by pydicom 3.0.x.

## Cite

If DentDeID or DentDeID-Synth helps your work, please cite them — see [`CITATION.cff`](https://github.com/Hezarani/dentdeid/blob/main/CITATION.cff)
(GitHub's "Cite this repository" button gives APA and BibTeX). DOIs are minted on Zenodo at each release.

```bibtex
@software{boustani_hezarani_dentdeid_2026,
  author  = {Boustani Hezarani, Hossein},
  title   = {DentDeID: open, tested de-identification for dental images},
  year    = {2026},
  version = {0.1.0},
  url     = {https://github.com/Hezarani/dentdeid}
}
```

## References & prior work

DentDeID was inspired by, builds on and uses the following work. Full discussion:
[docs/EVIDENCE.md](https://github.com/Hezarani/dentdeid/blob/main/docs/EVIDENCE.md); BibTeX:
[paper.bib](https://github.com/Hezarani/dentdeid/blob/main/paper.bib).

**Standards and guidance it implements or follows**

- DICOM Standards Committee (2026). *DICOM PS3.15 2026d — Security and System Management Profiles*, Annex E (Tables E.1-1 and E.3.4-1). NEMA. [dicom.nema.org](https://dicom.nema.org/medical/dicom/current/output/html/part15.html) · PS3.16 CID 7050 · PS3.5 B.2 (UUID-derived UIDs)
- U.S. HHS Office for Civil Rights (2012). *Guidance Regarding Methods for De-identification of Protected Health Information in Accordance with the HIPAA Privacy Rule.* [hhs.gov](https://www.hhs.gov/hipaa/for-professionals/special-topics/de-identification/index.html) · 45 CFR §164.514
- Regulation (EU) 2016/679 (GDPR) · EDPB (2025) *Guidelines 01/2025 on Pseudonymisation* (public consultation version) · Türkiye Law No. 6698 on the Protection of Personal Data (KVKK)
- American Dental Association (2026, March 11). Comments on RIN 0955-AA11, *Diagnostic Imaging Interoperability Standards and Certification* (letter to ASTP/ONC). [ada.org](https://www.ada.org/-/media/project/ada-organization/ada/ada-org/files/advocacy/regulatory-reform/2026/imaging_rfi_2026.pdf)
- Patel, S. (2024). Dental photography: data protection and social media. *BDJ In Practice* 37(11):438–439. [doi:10.1038/s41404-024-2917-1](https://doi.org/10.1038/s41404-024-2917-1)

**De-identification tools and benchmarks it builds on**

- Aryanto, K.Y.E., Oudkerk, M., van Ooijen, P.M.A. (2015). Free DICOM de-identification tools in clinical research. *European Radiology* 25(12):3685–3695. [doi:10.1007/s00330-015-3794-0](https://doi.org/10.1007/s00330-015-3794-0)
- Moore, S.M. et al. (2015). De-identification of medical images with retention of scientific research value. *RadioGraphics* 35(3):727–735. [doi:10.1148/rg.2015140244](https://doi.org/10.1148/rg.2015140244)
- Rutherford, M. et al. (2021). A DICOM dataset for evaluation of medical image de-identification. *Scientific Data* 8:183. [doi:10.1038/s41597-021-00967-y](https://doi.org/10.1038/s41597-021-00967-y)
- Clunie, D. et al. (2025). Summary of the NCI 2023 Virtual Workshop on Medical Image De-identification, Parts 1 & 2. *J Imaging Inform Med* 38(1):1–15, 16–30. [doi:10.1007/s10278-024-01182-y](https://doi.org/10.1007/s10278-024-01182-y) · [doi:10.1007/s10278-024-01183-x](https://doi.org/10.1007/s10278-024-01183-x)
- Pei, L. et al. (2025). Medical Image De-Identification Benchmark Challenge (MIDI-B, MICCAI 2024). [arXiv:2507.23608](https://doi.org/10.48550/arXiv.2507.23608) · Rutherford, M.W. et al. (2025). Medical Image De-Identification Resources. [arXiv:2508.01889](https://doi.org/10.48550/arXiv.2508.01889) · Data: [doi:10.7937/cf2p-aw56](https://doi.org/10.7937/cf2p-aw56)
- Seyderhelm, F. et al. (2025). An open data and methods framework for comparative evaluation of DICOM de-identification tools. *Stud Health Technol Inform* 331:327–337. [doi:10.3233/SHTI251411](https://doi.org/10.3233/SHTI251411)
- Rempe, M. et al. (2025). De-identification of medical imaging data: a comprehensive tool for ensuring patient privacy (MEDE). *European Radiology* 35(12):7809–7818. [doi:10.1007/s00330-025-11695-x](https://doi.org/10.1007/s00330-025-11695-x)
- Vcelak, P. et al. (2019). Identification and classification of DICOM files with burned-in text content. *Int J Med Inform* 126:128–137. [doi:10.1016/j.ijmedinf.2019.02.011](https://doi.org/10.1016/j.ijmedinf.2019.02.011)
- Macdonald, J.A. et al. (2024). A method for efficient de-identification of DICOM metadata and burned-in pixel text. *J Imaging Inform Med* 37(5). [doi:10.1007/s10278-024-01098-7](https://doi.org/10.1007/s10278-024-01098-7)
- Khosravi, B. et al. (2023). Anonymizing radiographs using an object detection deep learning algorithm. *Radiology: Artificial Intelligence* 5(6):e230085. [doi:10.1148/ryai.230085](https://doi.org/10.1148/ryai.230085)
- Naddeo, K. et al. (2025). DICOM de-identification via hybrid AI and rule-based framework. [arXiv:2507.23736](https://doi.org/10.48550/arXiv.2507.23736)
- Kline, A. et al. (2026). From redaction to restoration: deep learning for medical image deidentification and reconstruction. *J Imaging Inform Med*. [doi:10.1007/s10278-026-02241-2](https://doi.org/10.1007/s10278-026-02241-2)
- Brinz, J. et al. (2025). Data sharing for responsible artificial intelligence in dentistry: a narrative review of legal frameworks and privacy-preserving techniques. *Journal of Dentistry* 163:106130. [doi:10.1016/j.jdent.2025.106130](https://doi.org/10.1016/j.jdent.2025.106130)
- Software: pydicom *deid* ([doi:10.5281/zenodo.1287922](https://doi.org/10.5281/zenodo.1287922)) · [dicognito](https://github.com/blairconrad/dicognito) · [Kitware dicom-anonymizer](https://github.com/KitwareMedical/dicom-anonymizer) · [RSNA CTP](https://mircwiki.rsna.org/index.php?title=CTP-The_RSNA_Clinical_Trial_Processor) · [Microsoft Presidio](https://github.com/microsoft/presidio)

**Evidence that motivated it (re-identification risk)**

- Choi, H.-R. et al. (2026). Automated forensic human identification on dental panoramic radiographs using transformer-based detection with supervised metric learning. *BMC Oral Health* 26:1681. [doi:10.1186/s12903-026-09339-7](https://doi.org/10.1186/s12903-026-09339-7)
- İzci, A., Kayhan, U., Pekince, A., Liman, Z. (2026). Deep learning-based panoramic radiograph retrieval from antemortem images for forensic identification. *Int J Legal Med*. [doi:10.1007/s00414-026-03905-y](https://doi.org/10.1007/s00414-026-03905-y)
- Schwarz, C.G. et al. (2019). Identification of anonymous MRI research participants with face-recognition software. *N Engl J Med* 381(17):1684–1686. [doi:10.1056/NEJMc1908881](https://doi.org/10.1056/NEJMc1908881) · Schwarz, C.G. et al. (2021). Changing the face of neuroimaging research. *NeuroImage* 231:117845. [doi:10.1016/j.neuroimage.2021.117845](https://doi.org/10.1016/j.neuroimage.2021.117845)
- Ding, A.S. et al. (2025). Manual and automated facial de-identification techniques for patient imaging with preservation of sinonasal anatomy. *Int J CARS* 20(10):2167–2177. [doi:10.1007/s11548-025-03421-1](https://doi.org/10.1007/s11548-025-03421-1) · Kang, S.U. et al. (2026). De-identification strategy and re-identification risks for facial computed tomography images via deep learning. *J Imaging Inform Med*. [doi:10.1007/s10278-026-01858-7](https://doi.org/10.1007/s10278-026-01858-7)
- Preston, F.G. et al. (2022). Informed consent in facial photograph publishing. *J Empir Res Hum Res Ethics* 17(3):373–381. [doi:10.1177/15562646221075459](https://doi.org/10.1177/15562646221075459) · Roguljić, M. et al. (2022). Deidentification of facial photographs: a survey of editorial policies and practices. *J Med Ethics* 48(1):56–60. [doi:10.1136/medethics-2019-105823](https://doi.org/10.1136/medethics-2019-105823)

**Algorithms and software it uses**

- Otsu, N. (1979). A threshold selection method from gray-level histograms. *IEEE Trans Syst Man Cybern* 9(1):62–66. [doi:10.1109/TSMC.1979.4310076](https://doi.org/10.1109/TSMC.1979.4310076) — CBCT tissue classes
- Meyer, F. (1979). Iterative image transformations for an automatic screening of cervical smears. *J Histochem Cytochem* 27(1):128–135. [doi:10.1177/27.1.438499](https://doi.org/10.1177/27.1.438499) — the top-hat transform behind the text detector
- Viola, P., Jones, M. (2001). Rapid object detection using a boosted cascade of simple features. *CVPR 2001* 1:I-511–I-518. [doi:10.1109/CVPR.2001.990517](https://doi.org/10.1109/CVPR.2001.990517) · Lienhart, R., Maydt, J. (2002). An extended set of Haar-like features for rapid object detection. *ICIP 2002* 1:I-900–I-903. [doi:10.1109/ICIP.2002.1038171](https://doi.org/10.1109/ICIP.2002.1038171) — face detection
- Smith, R. (2007). An overview of the Tesseract OCR engine. *ICDAR 2007* 2:629–633. [doi:10.1109/ICDAR.2007.4376991](https://doi.org/10.1109/ICDAR.2007.4376991) — optional OCR
- Mason, D. (2011). Pydicom: an open source DICOM library. *Medical Physics* 38(6):3493. [doi:10.1118/1.3611983](https://doi.org/10.1118/1.3611983) · Harris, C.R. et al. (2020). Array programming with NumPy. *Nature* 585:357–362. [doi:10.1038/s41586-020-2649-2](https://doi.org/10.1038/s41586-020-2649-2) · Pillow ([doi:10.5281/zenodo.596518](https://doi.org/10.5281/zenodo.596518)) · Bradski, G. (2000). The OpenCV Library. *Dr. Dobb's Journal of Software Tools*
- Krawczyk, H., Bellare, M., Canetti, R. (1997). HMAC: keyed-hashing for message authentication. RFC 2104. [doi:10.17487/RFC2104](https://doi.org/10.17487/RFC2104) · NIST (2015). Secure Hash Standard, FIPS 180-4. [doi:10.6028/NIST.FIPS.180-4](https://doi.org/10.6028/NIST.FIPS.180-4) · Davis, K., Peabody, B., Leach, P. (2024). Universally Unique IDentifiers (UUIDs). RFC 9562. [doi:10.17487/RFC9562](https://doi.org/10.17487/RFC9562)

## Contributing & security

Contributions are welcome — especially vendor layouts and real-world validation results (never real
data). See [CONTRIBUTING.md](https://github.com/Hezarani/dentdeid/blob/main/CONTRIBUTING.md). Found a leak? Please report it privately:
[SECURITY.md](https://github.com/Hezarani/dentdeid/blob/main/SECURITY.md).

## Part of DentGit

DentDeID is part of a portfolio of small, complete, citable open-source tools for dentistry by
Hossein Boustani Hezarani, DDS — alongside [ToothNotation](https://github.com/Hezarani/toothnotation),
[OraCalm](https://github.com/Hezarani/oracalm), [DentRx](https://github.com/Hezarani/dentrx) and
[OdontoAge](https://github.com/Hezarani/odontoage).

## License

MIT © 2026 Hossein Boustani Hezarani. The PS3.15 rule tables are derived from the DICOM Standard
(© NEMA; DICOM® is a registered trademark of NEMA). DentDeID-Synth data: CC BY 4.0.
