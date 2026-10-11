---
title: 'DentDeID: open, tested de-identification for dental images'
tags:
  - Python
  - JavaScript
  - de-identification
  - dentistry
  - DICOM
  - medical imaging
  - privacy
authors:
  - name:
      given-names: Hossein
      surname: Boustani Hezarani
    orcid: 0009-0007-8481-0498
    affiliation: 1
affiliations:
  - name: Independent researcher, Ankara, Türkiye
    index: 1
date: 11 October 2026
bibliography: paper.bib
---

# Summary

Dentists share images constantly — with colleagues, in case reports, at study clubs and on social
media — and dental datasets are the raw material of dental artificial-intelligence research. Before
such images leave a practice, the information that identifies the patient has to be removed.
DentDeID does this for intra-oral and panoramic radiographs, cephalograms, cone-beam CT (CBCT)
volumes and screenshots, and clinical photographs. It treats the five places where dental images
leak identity: DICOM attributes, text burned into the pixels, embedded metadata of exported files
(EXIF, GPS, XMP, PNG text chunks), file names, and faces (in photographs, and in CBCT volumes that can
be rendered into a face). DentDeID is a dependency-light Python library and command-line tool, a
zero-install web application that runs entirely in the browser for clinicians sharing a single
case, and DentDeID-Synth, a fully synthetic benchmark with answer keys that is also published as a
separate dataset.

# Statement of need

Dental images rarely travel as clean DICOM. The American Dental Association told the US health
information technology regulator in 2026 that dental "images are often transmitted as screenshots,
PDFs, or secondary captures, stripping essential metadata" [@ada2026letter], so identifiers sit in
burned-in header strips, EXIF blocks and file names that DICOM-only tools never see. Dental
radiographs are also full of bright, compact structures — restorations, crowns, implants,
orthodontic brackets — that defeat threshold-based text detectors. And the dentition itself is
identifying: deep-learning systems ranked the correct person first for 65–75% of panoramic
radiographs when searching closed research galleries of about 200 people, drawn from cohorts of
842–1,029 individuals [@choi2026forensic; @izci2026retrieval], while face-recognition software matched 3D reconstructions
of head MRI to photographs [@schwarz2019mri] and many scans remained matchable after defacing
[@schwarz2021deface; @ding2025deface; @kang2026ct]. An informative note in the DICOM standard itself
gives dental images as an example of a case where cleaning recognisable features may make pixel data
unusable [@dicom2026ps315]. A recent review of data sharing for dental AI found so little dentistry-specific
work that it had to draw on medicine [@brinz2025datasharing]. DentDeID's users are clinicians sharing
cases — who need explicit patient consent for non-clinical use [@patel2024photography] — and
researchers building dental datasets.

# State of the field

Open DICOM de-identifiers such as pydicom *deid* [@sochat_deid], dicognito, RSNA CTP and Microsoft
Presidio, and research systems such as MEDE [@rempe2025mede], apply header rules and, in some cases,
OCR-based pixel redaction. An early comparison of free tools found incomplete removal
[@aryanto2015free], and later work described curation practice and frameworks for comparing tools
[@moore2015deid; @seyderhelm2025framework]. Methods for burned-in text range from
classification [@vcelak2019burnedin] and OCR pipelines [@macdonald2024deid] to deep-learning
detection and redaction [@khosravi2023anonymizing; @naddeo2025hybrid; @kline2026redaction].
Evaluation has matured through synthetic-PHI datasets with answer keys [@rutherford2021dataset;
@rutherford2025midi; @midib2025data], the NCI workshop on image de-identification
[@clunie2025midi1; @clunie2025midi2] and the MICCAI 2024 MIDI-B challenge [@pei2025midib]. All of
this work targets general radiology, oncology imaging (PT, CT, MR, DX, MG, CR, US) or pathology. To our knowledge
(PubMed, Europe PMC, PyPI and GitHub searches, September 2026) no open tool is designed for or
validated on dental imaging. General tools do process dental DICOM headers, but not dental exports
and screenshots, dental confounders in text detection, dental photographs or CBCT face risk.
DentDeID contributes these, together with a dental benchmark modelled on MIDI-style answer keys.

# Software design

**Standards-derived rules.** The PS3.15 (2026d) Basic Application Level Confidentiality Profile, its
options and the Clean Structured Content content-item table are generated directly from the
standard's machine-readable DocBook source, with a provenance hash, rather than transcribed
[@dicom2026ps315]. Compound action codes are resolved conservatively (remove unless the attribute is
known to be required), and actions on sequences apply to all of their contents. Attributes the table
does not list pass through a value-representation safety net, and an optional dental allow-list keeps
only attributes known to be safe. The patient's own identifiers are harvested before cleaning and
searched for everywhere else, also with separators removed and with transliterations. UIDs,
pseudonyms and per-patient date offsets use keyed HMAC-SHA-256 [@rfc2104], with UUID-derived UIDs
[@rfc9562], following the standard's warning against unencrypted hashes.

**Dental-aware burned-in text detection.** A deterministic detector uses a morphological top-hat
[@meyer1979tophat] to keep thin glyph strokes while suppressing broad bright structures, detects
chromatic overlays on greyscale radiographs, groups glyph-like components into text lines, and
separates evenly spaced calibration ticks from text. Images are classified as radiographs,
screenshots (greyscale content inside coloured software chrome) or photographs; in photographs,
where natural texture resembles text, detections are proposed for review rather than redacted. The
detector uses integer arithmetic only, so the JavaScript port in the web application returns
identical boxes and classes (verified in the test suite). Every frame and every CT slice is checked,
and pixel data is always re-written from decoded values, which also removes unused high bits,
trailing data and metadata inside JPEG streams. Tesseract OCR [@smith2007tesseract] is optional.

**Faces and volumes.** Photographs are processed with Viola–Jones cascades
[@viola2001rapid; @lienhart2002extended]; because covering parts of the face often leaves it
recognisable [@preston2022faces; @roguljic2022photographs], DentDeID offers whole-face obscuring or a
perioral crop. CBCT volumes receive an Otsu-based [@otsu1979threshold] assessment of facial
re-identification risk and an explicitly experimental soft-tissue removal.

**Fail-closed verification and PHI-free audit.** Every written file is re-opened and re-checked; a
check that cannot run quarantines the file, and one unreadable file never stops a batch. Reports
contain counts, kinds, boxes and check results, never an identifier or original file name. Review
images support the human approval that PS3.15 anticipates for Clean Pixel Data. The library builds
on pydicom [@mason2011pydicom], NumPy [@harris2020numpy], Pillow [@pillow_zenodo] and OpenCV
[@bradski2000opencv].

# Validation

On DentDeID-Synth (60 cases written as DICOM and as exports, plus a CBCT phantom), DentDeID removed
300/300 burned-in identifier boxes in standard layouts and 58/70 (82.9%) in deliberately hard cases
(small, low-contrast text over anatomy, JPEG-compressed), redacted 0.126% of image area
unnecessarily, left no fake identifier in any metadata field or file name, and flagged the phantom's
facial risk; a separate unit test found no false positives on 18 radiographs containing only dental
confounders. Because corpus and detector were developed together, these results are upper bounds.
In 6 of the 8 hard cases one identifier was missed and no automated check flagged it, which is why
human review is built into the workflow. Before release, separate AI-assisted adversarial audits of
privacy, standards conformance, documentation and the web application were run (see the AI usage
disclosure), and every confirmed issue became a regression test.

# Research impact statement

DentDeID lowers the barrier to sharing dental images responsibly for teaching, publication and
research. It provides, to our knowledge, the first openly licensed benchmark for de-identifying
dental images (DentDeID-Synth v1.0, CC BY 4.0, byte-for-byte reproducible from a seed), so other
groups can measure their own tools on identical data. The software is installable from PyPI, archived
with DOIs on Zenodo together with the dataset, tested on every change (169 tests, including
Python–JavaScript parity), and its benchmark is reproducible with two commands. No external use is
claimed at the time of this release.

# AI usage disclosure

Generative AI was used extensively. Anthropic's Claude (Opus model family, in the Cowork mode of
the Claude desktop application) wrote and refactored most of the code, tests and documentation under
the author's direction, searched and summarised the literature, and ran automated audits of the
software. The author conceived the project, defined its scope and requirements, made the design
decisions, reviewed the outputs and is responsible for the content. Correctness was checked by an
automated test suite (including Python–JavaScript parity and regression tests), by separate AI-run
adversarial audits whose findings were fixed and turned into tests, and by verifying every DOI
against its registry or publisher record and every other source at its origin.

# Acknowledgements

DentDeID builds on pydicom, NumPy, Pillow and OpenCV, and on the DICOM Standards Committee's
publication of the standard in machine-readable form. This work received no specific funding. The
author declares no competing interests.

# References
