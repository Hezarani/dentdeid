# DentDeID — conformance statement & regulatory mapping

> **Not legal advice.** This document explains what DentDeID does, mapped to the DICOM
> standard and common privacy rules, so that you — with your data-protection officer, ethics
> committee or IRB — can decide whether it fits your purpose. No software can make that decision
> for you.

Applies to DentDeID **0.1.0**, rule tables **DICOM PS3.15 2026d, Table E.1-1** (attributes) and
**Table E.3.4-1** (content items of the Clean Structured Content Option).

---

## 1. DICOM PS3.15 Annex E conformance statement

DentDeID acts as a **de-identifier** for the *Basic Application Level Confidentiality Profile*
(PS3.15 E.2). The attribute and content-item actions are **generated from the standard's own
machine-readable DocBook XML** by `scripts/build_ps315_table.py` (656 attribute rules + the
private-attribute rule; 214 content-item rules); the SHA-256 of the source file is stored in
`dentdeid/data/ps315_2026d.json` and printed by `dentdeid profiles`.

### 1.1 Profile and options

| PS3.15 profile / option | CID 7050 code | Support | How |
|---|---|---|---|
| Basic Application Level Confidentiality Profile | 113100 | ✅ | Table E.1-1 + private-attribute rule; §1.2–1.6 |
| Clean Pixel Data | 113101 | ✅ automated + **human review** | §1.7. Recorded (and `Burned In Annotation = NO`) only when the pixels were actually cleaned and no detected text was left in place |
| Clean Recognizable Visual Features | 113102 | ✅ photos / ⚠️ CBCT experimental | Photos: face blur / fill / perioral crop. CBCT: `strip-soft-tissue` (experimental). Recorded, with `Recognizable Visual Features = NO`, only when something was obscured |
| Clean Graphics | 113103 | ❌ not claimed | Graphic annotations are **removed** (Basic Profile `D` on a sequence, §1.2); overlays (60xx) and curves (50xx) are removed |
| Clean Structured Content | 113104 | ✅ | Items of Acquisition Context, Content and Specimen Preparation Sequences follow **Table E.3.4-1** by concept name; items whose concept is not listed get the by-VR safety net (§1.3); person-name items not listed are removed |
| Clean Descriptors | 113105 | ⚠️ pattern-based | Free text is kept after removing the harvested identifiers, PHI patterns, titled names ("Dr. Smith") and age/sex tokens ("55F"); long free text (comments) that still reads like a person's name is dropped, short descriptions that do are flagged for review. An unlabelled name the tool has never seen cannot be recognised with certainty |
| Retain Longitudinal Temporal Information — Full Dates | 113106 | ✅ | Dates/times kept (`Longitudinal Temporal Information Modified = UNMODIFIED`) |
| Retain Longitudinal Temporal Information — Modified Dates | 113107 | ✅ | §1.5 |
| Retain Patient Characteristics | 113108 | ✅ | Age, sex, size, weight — and, per the standard, Ethnic Group — kept; age derived if missing; **age ≥ 90 → `090Y`** |
| Retain Device Identity | 113109 | ✅ | AE titles are *cleaned* (replaced), not kept (E.3.8) |
| Retain UIDs | 113110 | ✅ | |
| Retain Safe Private | 113111 | ❌ | **All** private attributes are removed |
| Retain Institution Identity | 113112 | ✅ | |

Profiles shipped with DentDeID:

| DentDeID profile | PS3.15 options | Extras |
|---|---|---|
| `share` | Clean Descriptors | Dental allow-list; one-time key; faces blurred; all burned-in text redacted |
| `research` | Clean Descriptors, Clean Structured Content, Retain Patient Characteristics, Retain Longitudinal (Modified Dates) | Persistent key file; calibration rulers kept |
| `basic` | none | Faces filled |

Clean Pixel Data and Clean Recognizable Visual Features (for photographs) apply in all three profiles;
the column lists the options that change the header attributes.

### 1.2 Action codes, compound codes and sequences

`X` remove · `Z` zero-length · `D` dummy · `K` keep (sequences processed recursively) · `C` clean ·
`U` consistent new UID. An enabled option overrides the Basic Profile (PS3.15 E.1.1); when two
enabled options disagree (`K` vs `C`) the more protective `C` wins (the standard does not specify
this case).

**Compound codes** (Table E.1-1a: "X unless Z/D is required to maintain IOD conformance"). DentDeID
does not carry the complete IOD module tables, so it **removes** (`X`) by default and uses the
"required" alternative only where the attribute is known to be Type 1/2 in an IOD dental images use:
`X/Z → Z` for Acquisition Context Sequence (Type 2 in DX/IO); `Z/D → Z`; `D` for Device Serial
Number, Acquisition DateTime and Content Date/Time in enhanced multi-frame objects (e.g. X-Ray 3D
Craniofacial) and for Content Date/Time in VL images; `X/Z/U* → U` (references kept with every
contained UID replaced, for referential integrity). Everything else resolves to `X`.

**Sequences.** An action on a sequence applies to the sequence *and all of its contents*
(Table E.1-1a). `X`/`Z` remove or empty it; `D` on a sequence **removes it** (a generic dummy item
cannot be constructed without the IOD); `K`/`U` keep it and process every item recursively.

**Dummy values:** text `DEIDENTIFIED`, DA `19000101`, DT `19000101000000`, TM `000000`, AS `000D`,
numbers `0`. Patient's Name and Patient ID receive the keyed pseudonym (a `Z` action permits a
non-zero dummy); Patient's Birth Date is emptied. `C` on binary (OB/OW/UN) or URL values removes them.

### 1.3 Attributes not listed in Table E.1-1 (safety net)

PS3.15 notes that the table may not be sufficient and suggests handling dates by VR. For every
attribute **not** in the table DentDeID:

- **DA/DT/TM** → emptied (or kept/shifted under the Retain Longitudinal options);
- **PN** → emptied;
- **LO/SH/ST/LT/UT/UC, CS, AE** → searched for the harvested identifiers and PHI patterns; matches
  removed (coded values — Code Value, Coding Scheme Designator … — are exempt from the *patterns*
  but still searched for the patient's identifiers);
- **UI** → remapped unless it is a standard `1.2.840.10008.*` UID or a class/transfer-syntax UID;
- **UR** (URLs) and non-pixel **binary** values (OB/OW/OF/OD/OL/OV) → removed; an **ICC profile**
  is replaced by a generic sRGB profile;
- **UN** (unknown VR in a standard group) → removed, because its content cannot be inspected;
- an **Encrypted Attributes Sequence** is always removed (no reversible copy is ever kept).

Optionally (`--allowlist`, on in `share`) every top-level attribute not on the **dental allow-list**
(`dentdeid/data/dental_allowlist.json`, v2, 181 attributes) is removed afterwards — PS3.15's "retain only
what is known to be safe" design; allow-listed free-text fields that carry no image meaning
(Position Reference Indicator, Window Center & Width Explanation) are emptied.

### 1.4 UIDs and pseudonyms

New UIDs are `2.25.<integer>` where the integer is a UUIDv8-shaped value from
`HMAC-SHA-256(key, old UID)` (PS3.5 B.2; RFC 9562). The same key always maps the same UID the same
way, so studies and series stay internally consistent (PS3.15 E.1.1); without the key the mapping
cannot be reproduced or reversed. PS3.15 warns that *unencrypted* hashes are open to dictionary
attacks, so no plain hash is ever used. Well-known UIDs defined by the standard (e.g. the UTC
synchronisation frame of reference) are not replaced (E.3.9). Patient pseudonyms are `DDID-` + 10
base-32 characters of an HMAC of the Patient ID **and its issuer** (or name + birth date when the ID
is missing).

### 1.5 Manner of date modification (Retain Longitudinal — Modified Dates)

Every DA and the date part of every DT (including year-only and year-month DT values) is shifted
**backwards by a per-patient offset** of 30–3650 days derived from `HMAC-SHA-256(key, patient key)`.
Times of day are unchanged (a deliberate choice: whole-day shifts keep acquisition timing); UTC
offsets (DT suffixes, Timezone Offset From UTC) are removed because they reveal a location.
Intervals between a patient's studies are preserved exactly; with a persistent key the shift is
reproducible across runs. `Longitudinal Temporal Information Modified = MODIFIED`.

### 1.6 File Meta Information, preamble and other structures

The File Meta Information and 128-byte preamble are **replaced** (PS3.15 E.1.1): Implementation
Class UID `2.25.169520286596579396759548905391758843674` (derived from the project URL),
Implementation Version Name `DENTDEID_0.1.0`; source/sending/receiving AE titles and private
information are dropped. Group 0004, overlay (60xx) and curve (50xx) groups, Icon Image Sequence,
Original Attributes Sequence and Digital Signatures Sequence are removed. `Patient Identity Removed =
YES`; `De-identification Method` and `De-identification Method Code Sequence` (CID 7050) record
exactly what was applied.

**Objects that are not images are not processed.** A DICOMDIR is not copied (it indexes the
original, identified files; E.1.1); structured reports, encapsulated documents (PDF, STL …),
presentation states and other non-image objects are reported as SKIPPED and not copied.

### 1.7 Pixel data

Burned-in text is found by a deterministic detector (morphological top-hat stroke detection,
colour-overlay detection on greyscale images and screenshots, glyph grouping into lines; see
`docs/EVIDENCE.md`), optionally supplemented by Tesseract OCR. **Every frame** of a multi-frame
object and **every slice** of a CT/CBCT series is examined, on the windowed view and, for
greater-than-8-bit data, also on a plain linear view. Detected regions (padded) are set to the
stored value that displays black — the stored values are changed, as PS3.15 E.3.1 requires; an
overlay or shutter is never used.

The pixel data of every decodable image is **always re-written** from the decoded values,
uncompressed (Explicit VR Little Endian), at exactly the declared length. This also removes
content a viewer never shows: values in unused high bits (retired overlays), bytes after the last
frame, and EXIF/comment segments inside JPEG-compressed pixel data. If pixel data cannot be decoded,
the file **fails closed** (quarantined) in the default strict mode.

Colour **photographs** (told apart from screenshots — greyscale content in software chrome — by
their pixel statistics) are not text-redacted automatically, because natural texture resembles text;
text-like areas (including coloured labels) are outlined in the review image and **every photograph
is marked REVIEW** (`--text-in-photos` redacts them). Tinted or pseudo-colour renderings of
radiographs are treated as photographs.

PS3.15 E.3.1 notes that Clean Pixel Data "may require intervention of or approval by a human
operator". DentDeID therefore writes a **review image** for every image, and when the source declares
`Burned In Annotation = YES` the file is always marked REVIEW.

---

## 2. HIPAA Safe Harbor (45 CFR §164.514(b)(2)) — identifier mapping

| # | Safe Harbor identifier | DentDeID handling | Residual note |
|---|---|---|---|
| A | Names | PN attributes removed/pseudonymised; harvested names searched in all text, OCR and file names (also without separators and with umlaut transliterations); burned-in text redacted; EXIF Artist/Owner dropped | Faint burned-in text over anatomy can be missed — review the previews |
| B | Geographic subdivisions < state | Patient/Institution Address, residence removed; **GPS EXIF dropped**; "address:" labelled values removed | Unlabelled free-text addresses are only caught if harvested from the header |
| C | Dates (except year); ages > 89 | Removed/dummied (Basic), including month-year renderings of known dates; ages ≥ 90 → `090Y` | **Shifted dates (`research` profile) are *not* Safe Harbor** — they need Expert Determination or a limited data set agreement |
| D, E | Telephone / fax | Removed; phone pattern in text | |
| F | E-mail | Pattern | |
| G | SSN | Pattern | |
| H | Medical record numbers | Patient ID replaced; other IDs removed; long-number pattern | |
| I–K | Health plan, account, certificate/licence numbers | Insurance sequences removed; long-number pattern | Short codes may pass |
| L, M | Vehicle / **device identifiers & serial numbers** | Device Serial Number removed/dummied, Device UID remapped; EXIF camera/lens serials dropped | Kept if you enable Retain Device Identity |
| N, O | URLs, IP addresses | URL attributes removed; patterns in text | |
| P | Biometric identifiers | — | HHS guidance does not list radiographs as biometric identifiers. DentDeID's **conservative interpretation**: because the dentition supports forensic identification (2026 studies ranked the right person first in 65–75% of searches of closed research galleries of about 200 people), dental radiographs should be treated as potentially identifying images |
| Q | Full-face photographs and comparable images | Faces blurred/filled/cropped; CBCT face risk flagged; experimental soft-tissue removal | Lips/smile can remain distinctive; CBCT mitigation unvalidated |
| R | Any other unique code | UIDs remapped and pseudonyms created with a secret key; accession emptied | These codes are **derived from** identifiers (keyed HMAC), so they are **not** Safe Harbor re-identification codes under §164.514(c). HHS guidance allows such keyed codes under **Expert Determination** when the key is kept secret. The `share` profile discards its key after the run |

Because of rows **P**, **Q** and **R**, the realistic routes for dental radiographs and CBCT are
**Expert Determination**, a **limited data set** with a data-use agreement, or patient
**authorisation/consent**.

## 3. GDPR / UK GDPR / Turkish KVKK

- **Pseudonymised ≠ anonymous.** Data processed with a kept key (`research`) is *pseudonymised*
  (GDPR Art. 4(5)) and remains personal data; see EDPB *Guidelines 01/2025 on pseudonymisation*
  (version adopted for public consultation, 16 January 2025).
- **Anonymisation** requires that identification is not reasonably likely by any means reasonably
  likely to be used (Recital 26; see also EDPB *Guidelines 02/2026 on anonymisation*, public
  consultation 8 July – 30 October 2026). Given that the dentition is identifying, de-identified dental
  images should normally still be treated as **personal data** and, being health data, as a
  **special category** (GDPR Art. 9; KVKK Law No. 6698 Art. 6).
- DentDeID supports **data minimisation** (Art. 5(1)(c)) and **pseudonymisation as a safeguard**
  (Art. 32, Art. 89) — it does not by itself create a legal basis for sharing.
- For publication and social media, UK professional guidance on dental photographs (Patel 2024,
  *BDJ In Practice*) requires explicit patient consent for non-clinical use.

## 4. What DentDeID does not do

- It does not guarantee anonymity, and a PASS status is not a legal determination.
- It does not re-identify, link or store identifiers; reports contain no PHI.
- It does not process structured reports, encapsulated documents, presentation states, video,
  STL/intra-oral scans or vendor-proprietary formats; these are skipped (reported as SKIPPED), not copied.
- It does not implement Retain Safe Private, Clean Graphics or the Encrypted Attributes Sequence.
- It does not detect faces in greyscale photographs (they are treated like radiographs).
