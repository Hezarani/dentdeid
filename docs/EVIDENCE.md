# Evidence base & design rationale

Why DentDeID exists, what it builds on, and why each design decision was made. Every DOI was checked
against its registry (Crossref or DataCite) or publisher record, and standards, legal texts and web
pages at their source (September 2026).

## 1. The problem: dental images leak identity in dental-specific ways

**Dental images often do not travel as clean DICOM.** In a letter of 11 March 2026 to the Assistant
Secretary for Technology Policy/ONC (US Department of Health and Human Services; RIN 0955-AA11), the
American Dental Association wrote that dental "images are often transmitted as screenshots, PDFs, or
secondary captures, stripping essential metadata", and that "patients frequently serve as
intermediaries, transporting CDs, emails, or paperwork between care locations"
([ADA letter](https://www.ada.org/-/media/project/ada-organization/ada/ada-org/files/advocacy/regulatory-reform/2026/imaging_rfi_2026.pdf);
reported by [ADA News, 17 March 2026](https://adanews.ada.org/ada-news/2026/march/ada-calls-for-improved-interoperability-standards-for-dental-imaging/)).
Such exports and screenshots carry identity in four places at once:

1. **Burned-in text** — patient name, date of birth and ID in panoramic header strips, CBCT
   screenshot corners and title bars, coloured measurement labels;
2. **Embedded metadata** — EXIF (including **GPS position** from phone cameras), XMP, JPEG comments,
   PNG text chunks; and in DICOM, the header, private vendor tags and overlays;
3. **File names** — exports named `Surname_Name_1987-03-12_PAN.jpg`;
4. **Faces** — extra-oral orthodontic and aesthetic photographs.

**Clinicians share cases.** Case reports, study clubs, social media and referrals all involve images.
UK professional guidance on dental photographs requires explicit consent for non-clinical use such as
social media (Patel, *BDJ In Practice* 2024, [10.1038/s41404-024-2917-1](https://doi.org/10.1038/s41404-024-2917-1)).
A 2025 review of data sharing for AI in dentistry had to extend its search to medicine "given the
limited number of results relevant to dentistry" (Brinz et al., *J Dent* 2025,
[10.1016/j.jdent.2025.106130](https://doi.org/10.1016/j.jdent.2025.106130)).

**Researchers build datasets.** Every dental AI dataset has to be de-identified first, yet methods
sections often say only that images were "anonymized" (e.g. Choi et al. 2026,
[10.1186/s12903-026-09339-7](https://doi.org/10.1186/s12903-026-09339-7)).

## 2. The dentition is itself an identifier

Removing the name is necessary but not sufficient:

- Deep-learning retrieval ranked the correct ante-mortem **panoramic radiograph** first in
  **75.0 ± 6.5%** of searches (top-5 83.3%) in subject-level 5-fold cross-validation within a cohort
  of 842 people
  (İzci et al., *Int J Legal Med* 2026, [10.1007/s00414-026-03905-y](https://doi.org/10.1007/s00414-026-03905-y)).
- A transformer-based identification system reached **rank-1 65.4%** (rank-10 87.4%) on paired
  ante-/post-mortem-style panoramic radiographs — a 206-person test gallery drawn from 1,029 individuals
  (Choi et al., *BMC Oral Health* 2026, [10.1186/s12903-026-09339-7](https://doi.org/10.1186/s12903-026-09339-7)).

These are closed research galleries of about 200 people, not population-scale search — but
they show that a radiograph without a name is still linkable. Consequence for DentDeID: it never calls its output
*anonymous*. Reports state the residual risk every time, and the documentation routes users toward
consent, data-use agreements or expert determination (see [`COMPLIANCE.md`](COMPLIANCE.md)).

## 3. Faces: why no eye-bars, and why CBCT is flagged

- In a study with photographs of well-known people, **55.5%** were still recognised when a black box
  covered *all* facial features (only hair and neck visible), whereas letterbox-style crops that
  reveal less of the face delayed recognition far longer (Preston et al., *J Empir Res Hum Res Ethics* 2022,
  [10.1177/15562646221075459](https://doi.org/10.1177/15562646221075459); see also Roguljić et al.,
  *J Med Ethics* 2022, [10.1136/medethics-2019-105823](https://doi.org/10.1136/medethics-2019-105823)).
  → DentDeID offers whole-face blur/fill or a **perioral crop** (keep only the smile), not eye-bars,
  although PS3.15 E.3.2 still mentions "black rectangles over the eyes" as an established technique.
- Face-recognition software matched **70 of 84** research volunteers (83%) to their photographs from
  3D reconstructions of head MRI (Schwarz et al., *NEJM* 2019, [10.1056/NEJMc1908881](https://doi.org/10.1056/NEJMc1908881)),
  and 28–38% remained matchable after popular defacing tools (Schwarz et al., *NeuroImage* 2021,
  [10.1016/j.neuroimage.2021.117845](https://doi.org/10.1016/j.neuroimage.2021.117845)).
- For head CT, re-identification remained **30–45%** after manual, atlas-based or nnU-Net defacing
  (Ding et al., *IJCARS* 2025, [10.1007/s11548-025-03421-1](https://doi.org/10.1007/s11548-025-03421-1));
  see also Kang et al., *J Imaging Inform Med* 2026, [10.1007/s10278-026-01858-7](https://doi.org/10.1007/s10278-026-01858-7).
  → A large-field-of-view dental CBCT includes the lower face. DentDeID always **flags** the risk,
  and offers soft-tissue removal only as an explicitly **experimental** option.
- The DICOM standard itself points to dentistry: an informative note to PS3.15 E.3.2 (*Clean
  Recognizable Visual Features*) says the option "may render the pixel data unusable for the purpose
  for which it has been collected … Consider for example, the case of dental images."

## 4. Prior work DentDeID builds on — and the gap

| Tool / study | What it does well | Dental gap |
|---|---|---|
| pydicom *deid* ([10.5281/zenodo.1287922](https://doi.org/10.5281/zenodo.1287922)), dicognito, Kitware dicom-anonymizer, RSNA CTP | DICOM header rules; coordinate-based pixel "recipes" | Designed for DICOM; no dental export/screenshot handling, EXIF, file names or faces |
| Microsoft Presidio image redactor | OCR + NER on pixels | Needs Tesseract + spaCy; not dental-aware |
| MEDE (Rempe et al., *Eur Radiol* 2025, [10.1007/s00330-025-11695-x](https://doi.org/10.1007/s00330-025-11695-x)) | Metadata, defacing, OCR text removal for MRI/CT/US/WSI | Not designed or validated for dental modalities or exports |
| Burned-in text: Vcelak et al. 2019 ([10.1016/j.ijmedinf.2019.02.011](https://doi.org/10.1016/j.ijmedinf.2019.02.011)); Macdonald et al. 2024 ([10.1007/s10278-024-01098-7](https://doi.org/10.1007/s10278-024-01098-7)); Khosravi et al. 2023 ([10.1148/ryai.230085](https://doi.org/10.1148/ryai.230085)); Naddeo et al. 2025 ([arXiv:2507.23736](https://doi.org/10.48550/arXiv.2507.23736)); Kline et al. 2026 ([10.1007/s10278-026-02241-2](https://doi.org/10.1007/s10278-026-02241-2)) | Classification, OCR/NER and deep-learning redaction of burned-in text and markers | General radiology (e.g. 415,182 images from one institution; hip and chest radiographs) |
| Comparisons and benchmarks: Aryanto et al. 2015 ([10.1007/s00330-015-3794-0](https://doi.org/10.1007/s00330-015-3794-0)); Moore et al. 2015 ([10.1148/rg.2015140244](https://doi.org/10.1148/rg.2015140244)); Rutherford et al. 2021 ([10.1038/s41597-021-00967-y](https://doi.org/10.1038/s41597-021-00967-y)); NCI 2023 workshop (Clunie et al. 2025, [10.1007/s10278-024-01182-y](https://doi.org/10.1007/s10278-024-01182-y), [10.1007/s10278-024-01183-x](https://doi.org/10.1007/s10278-024-01183-x)); Seyderhelm et al. 2025 ([10.3233/SHTI251411](https://doi.org/10.3233/SHTI251411)); MICCAI 2024 MIDI-B challenge (Pei et al. 2025, [arXiv:2507.23608](https://doi.org/10.48550/arXiv.2507.23608); data [10.7937/cf2p-aw56](https://doi.org/10.7937/cf2p-aw56); Rutherford et al. 2025, [arXiv:2508.01889](https://doi.org/10.48550/arXiv.2508.01889)) | Evaluation methodology, synthetic-PHI validation data, answer-key scoring (MIDI-B: 97.9–99.9% action accuracy) — the model for DentDeID-Synth | PT, CT, MR, DX, MG, CR, US from oncology collections — **no dental images** |

To our knowledge (PubMed, Europe PMC, PyPI and GitHub searches, September 2026), no open tool is
designed for or validated on dental imaging. General DICOM de-identifiers do process dental DICOM
headers; what is missing is handling of dental exports and screenshots, dental confounders in
burned-in-text detection, dental photographs and CBCT face risk. Whether vendors' own "anonymised
export" features close this gap was not assessed.

## 5. Design decisions

| Decision | Rationale |
|---|---|
| Generate the PS3.15 rule tables from the standard's DocBook XML | No transcription errors; one command to update to a new edition; provenance hash recorded |
| Safety net by VR for unlisted attributes | PS3.15 says the table may be insufficient and recommends handling dates by VR |
| Dental allow-list mode | PS3.15 describes "retain only what is known to be safe" as a valid design; dental IODs are small enough to curate |
| Harvest identifiers before cleaning | The most common leak is the patient's own name typed into another field or burned into pixels; exact matching is precise |
| Keyed HMAC (RFC 2104, SHA-256 per FIPS 180-4), never plain hashes | PS3.15 warns unencrypted hashes are open to dictionary attacks |
| Morphological top-hat stroke detector (Meyer 1979, [10.1177/27.1.438499](https://doi.org/10.1177/27.1.438499)) | Keeps thin glyph strokes; suppresses broad bright structures (crowns, restorations, implants, brackets) that would fool a plain threshold |
| Colour-on-greyscale detection; screenshot vs photograph classification | Radiographs are greyscale, so chromatic pixels are overlays; screenshots are greyscale images in coloured chrome; in camera photographs natural texture resembles text, so detections are proposed for review rather than redacted |
| Integer-only detector with a JavaScript twin | The web app and the Python library give **identical** results (tested), so the demo shows exactly what the library does |
| Always re-write decoded pixel data | Removes what viewers never show: unused high bits, trailing data, metadata inside JPEG streams |
| Fail closed | A check that cannot run (undecodable pixels, no face detector for a photo) quarantines the file rather than passing it |
| Verification re-reads the written file, every frame and slice | A writer bug cannot hide a leak |
| Reports without PHI | Audit trails must not become a second copy of the identifiers |
| Human review previews | PS3.15 E.3.1: Clean Pixel Data "may require intervention of or approval by a human operator" |

Classical methods and libraries used: Otsu thresholding ([10.1109/TSMC.1979.4310076](https://doi.org/10.1109/TSMC.1979.4310076))
for CBCT tissue classes; Viola–Jones cascades ([10.1109/CVPR.2001.990517](https://doi.org/10.1109/CVPR.2001.990517)) with
Lienhart–Maydt extended Haar features ([10.1109/ICIP.2002.1038171](https://doi.org/10.1109/ICIP.2002.1038171)) as shipped in
OpenCV for faces; Tesseract ([10.1109/ICDAR.2007.4376991](https://doi.org/10.1109/ICDAR.2007.4376991)) for optional OCR;
pydicom ([10.1118/1.3611983](https://doi.org/10.1118/1.3611983)), NumPy ([10.1038/s41586-020-2649-2](https://doi.org/10.1038/s41586-020-2649-2)),
Pillow ([10.5281/zenodo.596518](https://doi.org/10.5281/zenodo.596518)) and OpenCV (Bradski 2000).

## 6. Validation and its limits

Full results: [`BENCHMARK.md`](BENCHMARK.md). On DentDeID-Synth (seed 2026; 60 cases written as both
DICOM and exported images, plus a CBCT phantom):

- **Standard layouts:** 300/300 burned-in PHI boxes removed (100%).
- **Hard cases** (small, low-contrast text drawn over teeth and bone, JPEG-compressed): 58/70 (82.9%).
  **In 6 of the 8 hard cases (12 of 16 files) one identifier was missed, and no automated check
  flagged any of them.** OCR did not change this. This is why DentDeID requires a person to review
  every image.
- **Image area redacted unnecessarily:** 0.126% (mean per image).
- **Metadata / file names:** 0 files with any residual fake identifier.
- **Detector false positives on dental confounders** (restorations, crowns, root fillings,
  implants, brackets + arch-wire, trabecular bone): none in the test suite's 18 confounder-only
  radiographs (a unit test, separate from the corpus).

**Limitation:** the synthetic corpus and the detector were developed together, so these numbers are
an **upper bound**. Real-world recall must be measured on real, ethically approved clinical data
across vendors — the most valuable next study.
