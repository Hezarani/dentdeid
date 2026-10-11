# Changelog

All notable changes to DentDeID are documented here. The project follows
[Semantic Versioning](https://semver.org/).

## [0.1.0] — 2026-09-30

First public release.

### Added
- **DICOM attributes** — PS3.15 (2026d) Basic Application Level Confidentiality Profile, with the
  rule tables generated from the standard's own DocBook XML (Table E.1-1: 656 rules + private
  attributes; Table E.3.4-1: 214 content-item rules). Options: Clean Descriptors (pattern-based),
  Clean Structured Content, Retain Patient Characteristics (with HIPAA age ≥ 90 aggregation), Retain
  Longitudinal Temporal Information (full or per-patient shifted dates), Retain Device / Institution
  Identity, Retain UIDs. Compound action codes resolved conservatively (remove unless required).
- Safety net for attributes not in Table E.1-1 (dates by VR, person names, free text scanned for
  harvested identifiers and PHI patterns, non-standard UIDs remapped, URLs and opaque binary values
  removed, ICC profiles replaced, unknown VRs removed).
- Dental allow-list mode (keep only attributes known to be safe and useful for dental imaging).
- Keyed pseudonyms (HMAC-SHA-256): patient IDs (with issuer), UUIDv8-shaped `2.25` UIDs, per-patient
  date offsets.
- File Meta Information and preamble replacement; group 0004, overlays, curves, icons, original,
  encrypted and signature attributes removed; CID 7050 method codes recorded only for what was applied.
- **Burned-in text** detector (morphological top-hat, dental-confounder aware, two-pass,
  colour-overlay aware, ruler/scale classification, radiograph / screenshot / photograph
  classification) with an identical JavaScript port. Every frame and CT slice is checked; pixel data
  is always re-written from decoded values.
- Optional OCR (Tesseract) for extra regions, a `phi-only` policy and output verification.
- **Exported images** (JPEG/PNG/TIFF/BMP/WebP/GIF): EXIF/GPS/XMP/ICC/comments/text chunks removed,
  orientation applied, 16-bit greyscale preserved, identifying file names replaced.
- **Faces** in extra-oral photographs: blur (auto-escalates to fill), fill, or perioral crop.
- **CBCT/CT volumes** (series and multi-frame objects): facial re-identification risk assessment;
  experimental soft-tissue strip and shell mitigations.
- Independent verification of every written output; fail-closed quarantine; per-file error isolation;
  PHI-free JSON/Markdown reports; review images.
- **DentDeID-Synth** corpus generator with answer keys, and a benchmark (`dentdeid bench`).
- Zero-install web app (GitHub Pages) for clinicians sharing a single case.

### Pre-release audit
Before this release, two rounds of separate AI-assisted (Claude) reviews of privacy (adversarial
synthetic files), DICOM PS3.15 conformance, academic accuracy, the web app and packaging were carried
out; every confirmed issue was fixed and is covered by `tests/test_audit_regressions.py` or
`tests/test_web_parity.py`.
