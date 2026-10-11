"""De-identification of DICOM *attributes* (everything except the pixel values).

Order of operations for one dataset:

1. **Harvest** the patient's own identifiers (name parts, IDs, birth date, clinic, staff
   names, exam dates) *before* anything is removed, for use by every later text check.
2. **Walk** every element, including those nested in sequences, and apply:

   * private attributes → removed (PS3.15: private data is not known to be safe);
   * group 0004, overlay (60xx) and curve (50xx) groups → removed;
   * attributes listed in PS3.15 Table E.1-1 → the action resolved for the enabled options
     (with the Clean Structured Content Option, content items in Acquisition Context,
     Content and Specimen Preparation Sequences follow Table E.3.4-1 by concept name);
   * anything else → the **safety net**: dates/times by VR (PS3.15 recommends handling
     dates by DA/DT/TM rather than only the listed ones), person names emptied, free-text
     values scanned for the harvested identifiers and PHI patterns, non-standard UIDs
     remapped, URLs and non-pixel binary values removed, ICC profiles replaced by a
     generic sRGB profile, and attributes of unknown VR (``UN``) removed.
3. **Replace identity** with keyed pseudonyms, optionally derive Patient's Age from the
   birth and study dates (capped at 90 per HIPAA Safe Harbor), and record the method in
   Patient Identity Removed, De-identification Method (Code Sequence, CID 7050) and
   Longitudinal Temporal Information Modified.
4. Optionally keep **only** attributes on the dental allow-list.
5. Replace the **File Meta Information and preamble** (PS3.15 E.1.1).
"""
from __future__ import annotations

import datetime as _dt
import json
import uuid
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import resources

from pydicom.dataelem import DataElement
from pydicom.datadict import keyword_for_tag, tag_for_keyword
from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.multival import MultiValue
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian

from .._version import __version__
from ..keys import Pseudonymizer, shift_da, shift_dt
from ..phi import KnownIdentifiers, name_like_words, scrub_text
from . import ps315

IMPLEMENTATION_CLASS_UID = "2.25." + str(uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/Hezarani/dentdeid").int)
IMPLEMENTATION_VERSION_NAME = ("DENTDEID_" + __version__)[:16]
DUMMY_TEXT = "DEIDENTIFIED"
DUMMY_DA = "19000101"
DUMMY_DT = "19000101000000"
DUMMY_TM = "000000"

_TEXT_VRS = {"LO", "SH", "ST", "LT", "UT", "UC"}
_DATE_VRS = {"DA", "DT", "TM"}
_BINARY_VRS = {"OB", "OW", "OD", "OF", "OL", "OV", "UN"}
# Binary attributes that carry image structure, not identity (kept by the safety net).
_STRUCTURAL_BINARY = {
    "PixelData", "FloatPixelData", "DoubleFloatPixelData", "ExtendedOffsetTable",
    "ExtendedOffsetTableLengths", "LUTData", "RedPaletteColorLookupTableData",
    "GreenPaletteColorLookupTableData", "BluePaletteColorLookupTableData",
    "AlphaPaletteColorLookupTableData", "SegmentedRedPaletteColorLookupTableData",
    "SegmentedGreenPaletteColorLookupTableData", "SegmentedBluePaletteColorLookupTableData",
    "SegmentedAlphaPaletteColorLookupTableData",
}
# Coded-entry parts: exempt from PHI *patterns* (a SNOMED code is not an ID number) but still
# searched for the patient's own identifiers.
_CODE_KWS = {"CodeValue", "LongCodeValue", "CodingSchemeDesignator", "CodingSchemeVersion",
             "MappingResource", "ContextIdentifier", "ContextGroupVersion"}
_KEEP_URL_KWS = {"URNCodeValue"}                       # "urn:oid:…" codes, not locations
# Sequences whose items are SR-style content items (PS3.15 E.3.4).
_CONTENT_SEQ_TAGS = {0x00400555, 0x0040A730, 0x00400610}
_CONTENT_VALUE_KWS = {"TEXT": ("TextValue",), "PNAME": ("PersonName",), "DATE": ("Date",),
                      "TIME": ("Time",), "DATETIME": ("DateTime",), "UIDREF": ("UID",),
                      "NUM": (), "CODE": (), "CONTAINER": (), "IMAGE": (), "COMPOSITE": (),
                      "WAVEFORM": (), "SCOORD": (), "SCOORD3D": (), "TCOORD": ()}
_ENCRYPTED_ATTRIBUTES_SEQUENCE = 0x04000500
_ALLOWLIST_EMPTIED = ("PositionReferenceIndicator", "WindowCenterWidthExplanation")


@lru_cache(maxsize=1)
def srgb_icc_profile() -> bytes:
    """A generic sRGB ICC profile (replaces a device profile, whose text could identify)."""
    from PIL import ImageCms
    b = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    return b + (b"\x00" if len(b) % 2 else b"")
_MAXLEN = {"SH": 16, "LO": 64, "CS": 16, "AE": 16, "PN": 64, "LT": 10240, "ST": 1024, "UC": 1 << 20,
           "UT": 1 << 20}
_STANDARD_UID_ROOT = "1.2.840.10008."
_NEVER_REMAP = {"TransferSyntaxUID", "CodingSchemeUID", "ContextUID", "MappingResourceUID", "TemplateUID",
                "ContextGroupExtensionCreatorUID"}
# Safety-net patterns: 'time' is excluded (acquisition times are not identifying on their own).
_SAFETY_PATTERN_EXCLUDE = {"time"}

_PATIENT_NAME_KWS = {"PatientName", "OtherPatientNames", "PatientBirthName"}
_RELATIVE_NAME_KWS = {"PatientMotherBirthName", "ResponsiblePerson", "PersonName"}
_ID_KWS = {"PatientID", "OtherPatientIDs", "AccessionNumber", "MedicalRecordLocator", "StudyID",
           "AdmissionID", "IssuerOfPatientID", "PatientInsurancePlanCodeSequence", "FillerOrderNumberImagingServiceRequest",
           "PlacerOrderNumberImagingServiceRequest", "RequestedProcedureID", "ScheduledProcedureStepID",
           "PerformedProcedureStepID"}
_TEXT_KWS = {"PatientAddress", "PatientTelephoneNumbers", "PatientTelecomInformation", "InstitutionName",
             "InstitutionAddress", "InstitutionalDepartmentName", "StationName", "ReferringPhysicianAddress",
             "ReferringPhysicianTelephoneNumbers", "PatientComments", "Occupation", "MilitaryRank",
             "BranchOfService", "RegionOfResidence"}
_EXAM_DATE_KWS = {"StudyDate", "SeriesDate", "AcquisitionDate", "ContentDate"}


@dataclass
class MetaConfig:
    """Which PS3.15 options and DentDeID extensions to apply to the attributes."""

    options: frozenset = frozenset()          # Table E.1-1 option keys (see ps315.SUPPORTED_TABLE_OPTIONS)
    pseudonymize_patient: bool = True         # Patient's Name/ID ← keyed pseudonym (else empty/dummy)
    allowlist: bool = False                   # keep only the dental allow-list (top level)
    safety_net: bool = True                   # by-VR checks for attributes not in Table E.1-1
    remap_unlisted_uids: bool = True
    derive_age: bool = True                   # add Patient's Age from DOB + study date (if retaining characteristics)
    hipaa_age_cap: bool = True                # ages >= 90 → "090Y"

    def __post_init__(self):
        unknown = set(self.options) - set(ps315.SUPPORTED_TABLE_OPTIONS)
        if unknown:
            raise ValueError(f"unsupported PS3.15 options: {sorted(unknown)}")
        if {"retain_longitudinal_full_dates", "retain_longitudinal_modified_dates"} <= set(self.options):
            raise ValueError("the two Retain Longitudinal Temporal Information options are mutually exclusive")


@dataclass
class MetaResult:
    counts: Counter = field(default_factory=Counter)
    patient_pseudonym: str | None = None
    date_offset_days: int | None = None
    notes: list[str] = field(default_factory=list)
    methods: list[str] = field(default_factory=list)       # CID 7050 keys applied

    def as_dict(self) -> dict:
        return {
            "counts": dict(sorted(self.counts.items())),
            "patient_pseudonym": self.patient_pseudonym,
            "date_shift_applied": self.date_offset_days is not None,
            "notes": list(self.notes),
            "ps315_methods": list(self.methods),
        }


@lru_cache(maxsize=None)
def allowlist_tags() -> frozenset[int]:
    raw = json.loads(resources.files("dentdeid").joinpath("data/dental_allowlist.json").read_text("utf-8"))
    tags = set()
    for kws in raw["groups"].values():
        for kw in kws:
            t = tag_for_keyword(kw)
            if t is None:
                raise ValueError(f"unknown keyword in allow-list: {kw}")
            tags.add(int(t))
    return frozenset(tags)


# ---------------------------------------------------------------------------------------
# 1. Harvest
# ---------------------------------------------------------------------------------------
def _values(elem) -> list:
    v = elem.value
    if v is None:
        return []
    if isinstance(v, (list, tuple, MultiValue)):
        return [x for x in v if x not in (None, "")]
    return [v] if v != "" else []


def harvest_identifiers(ds: Dataset, known: KnownIdentifiers | None = None) -> KnownIdentifiers:
    """Collect the patient's identifiers from ``ds`` (recursively) *before* cleaning."""
    known = known if known is not None else KnownIdentifiers()

    def visit(d: Dataset) -> None:
        for elem in d:
            if elem.VR == "SQ":
                for item in elem.value or []:
                    visit(item)
                continue
            kw = elem.keyword or ""
            if elem.tag.is_private:
                continue
            for v in _values(elem):
                s = str(v)
                if elem.VR == "PN":
                    if kw in _PATIENT_NAME_KWS:
                        known.add_name(s, "patient-name")
                    elif kw in _RELATIVE_NAME_KWS:
                        known.add_name(s, "relative-name")
                    else:
                        known.add_name(s, "staff-name")
                elif kw in _ID_KWS or (kw.endswith("ID") and elem.VR in ("LO", "SH") and "Patient" in kw):
                    known.add_id(s, "patient-id" if "Patient" in kw else "record-id")
                elif kw == "PatientBirthDate":
                    known.add_date(s, "birth-date")
                elif kw in _EXAM_DATE_KWS:
                    known.add_date(s, "exam-date")
                elif kw in _TEXT_KWS:
                    known.add_text(s, "institution" if kw.startswith("Institution") or kw == "StationName"
                                   else "contact")
                elif kw == "DeviceSerialNumber":
                    known.add_text(s, "device-serial")

    visit(ds)
    return known


# ---------------------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------------------
def _is_overlay_or_curve_group(group: int) -> bool:
    return (0x6000 <= group <= 0x601E or 0x5000 <= group <= 0x501E) and group % 2 == 0


def _empty(elem: DataElement) -> None:
    elem.value = Sequence([]) if elem.VR == "SQ" else None


def _dummy_for(elem: DataElement, pseudo: Pseudonymizer, offset: int | None):
    vr = elem.VR
    if vr == "UI":
        return [pseudo.uid(str(v)) for v in _values(elem)] or pseudo.uid("dummy")
    if vr == "DA":
        vals = _values(elem)
        if offset is not None and vals:
            return [shift_da(str(v), offset) for v in vals]
        return DUMMY_DA
    if vr == "DT":
        vals = _values(elem)
        if offset is not None and vals:
            return [shift_dt(str(v), offset) for v in vals]
        return DUMMY_DT
    if vr == "TM":
        return DUMMY_TM
    if vr in ("IS", "DS"):
        return "0"
    if vr in ("US", "SS", "UL", "SL", "FL", "FD", "UV", "SV", "AT"):
        return 0
    if vr in ("OB", "OW", "OD", "OF", "OL", "OV", "UN"):
        return b"\x00\x00"
    if vr == "AS":
        return "000D"
    if vr == "AE":
        return "DEID"
    if vr == "CS":
        return DUMMY_TEXT
    return DUMMY_TEXT[: _MAXLEN.get(vr, 64)]


def _clean_text_value(value: str, known: KnownIdentifiers, exclude: set[str] | None = None) -> tuple[str, int]:
    clean, findings = scrub_text(value, known, True, frozenset(exclude or ()))
    return clean, len(findings)


# ---------------------------------------------------------------------------------------
# 2–5. De-identify
# ---------------------------------------------------------------------------------------
class _Cleaner:
    def __init__(self, cfg: MetaConfig, pseudo: Pseudonymizer, known: KnownIdentifiers,
                 offset: int | None, res: MetaResult, sop_class: str | None = None):
        self.cfg, self.pseudo, self.known, self.offset, self.res = cfg, pseudo, known, offset, res
        self.full_dates = "retain_longitudinal_full_dates" in cfg.options
        self.sop_class = sop_class

    # dates handled by VR (for 'C' actions and the safety net)
    def _date(self, elem: DataElement, *, via: str) -> None:
        vals = _values(elem)
        if not vals:
            return
        if self.full_dates:
            self.res.counts[f"{via}:dates_kept"] += 1
            return
        if self.offset is not None and elem.VR in ("DA", "DT"):
            f = shift_da if elem.VR == "DA" else shift_dt
            try:
                elem.value = [f(str(v), self.offset) for v in vals] if len(vals) > 1 else f(str(vals[0]), self.offset)
                self.res.counts["dates_shifted"] += 1
                return
            except ValueError:
                pass
        if self.offset is not None and elem.VR == "TM":
            self.res.counts[f"{via}:times_kept"] += 1  # day shift keeps time-of-day relationships
            return
        _empty(elem)
        self.res.counts[f"{via}:dates_emptied"] += 1

    def _text(self, elem: DataElement, *, via: str) -> None:
        changed = 0
        new_vals = []
        kw = elem.keyword or ""
        patterns = kw not in _CODE_KWS
        for v in _values(elem):
            c, fs = scrub_text(str(v), self.known, patterns,
                               frozenset(_SAFETY_PATTERN_EXCLUDE if via == "safety_net" else ()),
                               descriptors=(via == "clean"))
            changed += len(fs)
            new_vals.append(c)
        if via == "clean" and any(name_like_words(v) for v in new_vals):
            if elem.VR in ("UT", "LT", "ST"):
                # free-text comments/notes that still read like a person's name are dropped
                elem.value = None
                self.res.counts["clean:free_text_with_names_removed"] += 1
                return
            self.res.counts["clean:name_like_descriptor_kept"] += 1
        if changed:
            new_vals = [v[: _MAXLEN.get(elem.VR, 1 << 20)] for v in new_vals if v]
            elem.value = new_vals if len(new_vals) > 1 else (new_vals[0] if new_vals else None)
            self.res.counts[f"{via}:text_scrubbed"] += 1

    def _remove(self, ds: Dataset, elem: DataElement, counter: str) -> None:
        del ds[elem.tag]
        self.res.counts[counter] += 1

    def _replace_icc(self, elem: DataElement) -> None:
        if elem.value:
            elem.value = srgb_icc_profile()
            self.res.counts["icc_profile_replaced"] += 1

    def _uid(self, elem: DataElement) -> None:
        vals = _values(elem)
        if not vals:
            return
        # well-known UIDs defined by the standard (e.g. the UTC synchronisation frame of
        # reference) identify nothing and must not be replaced (PS3.15 E.3.9)
        new = [str(v) if str(v).startswith(_STANDARD_UID_ROOT) else self.pseudo.uid(str(v)) for v in vals]
        elem.value = new if len(new) > 1 else new[0]
        self.res.counts["uids_remapped"] += 1

    def walk(self, ds: Dataset, skip: frozenset[int] = frozenset()) -> None:
        for elem in list(ds):
            tag = elem.tag
            group = tag.group
            if int(tag) in skip:
                continue
            if tag.is_private:
                del ds[tag]
                self.res.counts["private_removed"] += 1
                continue
            if group == 0x0004 or _is_overlay_or_curve_group(group):
                del ds[tag]
                self.res.counts["overlay_curve_or_dir_removed"] += 1
                continue
            if int(tag) == _ENCRYPTED_ATTRIBUTES_SEQUENCE:
                # a reversible (encrypted) copy of the original attributes is never kept
                self._remove(ds, elem, "encrypted_attributes_removed")
                continue
            if elem.VR == "UN" and tag != 0x7FE00010:
                # unknown VR in a standard group: contents cannot be inspected → remove
                del ds[tag]
                self.res.counts["unknown_vr_removed"] += 1
                continue
            rule = ps315.lookup(tag)
            if rule is not None:
                self.apply(ds, elem, ps315.resolve(rule, self.cfg.options, self.sop_class))
            elif self.cfg.safety_net:
                self.safety(ds, elem)
            elif elem.VR == "SQ":
                self.walk_seq(elem)

    def walk_seq(self, elem: DataElement) -> None:
        for item in elem.value or []:
            self.walk(item)

    def apply(self, ds: Dataset, elem: DataElement, action: str) -> None:
        c = self.res.counts
        vr = elem.VR
        if action == "X":
            del ds[elem.tag]
            c["removed"] += 1
        elif action == "Z":
            _empty(elem)
            c["emptied"] += 1
        elif action == "D":
            if vr == "SQ":
                # PS3.15: for a sequence the action applies to the sequence *and all of its
                # contents*. A generic dummy item cannot be built without the IOD, so the
                # sequence is removed (never kept with its items).
                del ds[elem.tag]
                c["removed"] += 1
            else:
                elem.value = _dummy_for(elem, self.pseudo, self.offset)
                c["dummied"] += 1
        elif action == "U":
            if vr == "UI":
                self._uid(elem)
            elif vr == "SQ":
                self.walk_seq(elem)
        elif action == "K":
            if vr == "SQ":
                self.walk_seq(elem)
            c["kept"] += 1
        elif action == "C":
            self.clean(ds, elem)
        else:  # pragma: no cover - table validated at build time
            raise ValueError(f"unknown action {action!r}")

    def clean(self, ds: Dataset, elem: DataElement) -> None:
        """The 'C' action: keep only what is known not to identify."""
        c = self.res.counts
        vr, kw = elem.VR, elem.keyword or ""
        if vr == "SQ":
            if int(elem.tag) in _CONTENT_SEQ_TAGS:
                self.clean_content(elem)
            else:
                self.walk_seq(elem)
        elif kw == "TimezoneOffsetFromUTC":
            self._remove(ds, elem, "removed")        # reveals location; not needed once dates move
            return
        elif vr in _DATE_VRS:
            self._date(elem, via="clean")
        elif vr in _TEXT_VRS:
            self._text(elem, via="clean")
        elif vr == "PN":
            elem.value = DUMMY_TEXT
        elif vr == "AE":
            elem.value = DUMMY_TEXT[:16]            # AE titles are cleaned, not kept (E.3.8)
        elif vr == "CS":
            if any(self.known.find(str(v)) for v in _values(elem)):
                elem.value = DUMMY_TEXT
        elif vr == "UI":
            self._uid(elem)
        elif vr == "UR" or vr in _BINARY_VRS:
            # binary or URL values cannot be inspected for identity → removed
            self._remove(ds, elem, "removed")
            return
        c["cleaned"] += 1

    # -- Clean Structured Content (Table E.3.4-1) -----------------------------------------
    def clean_content(self, elem: DataElement) -> None:
        kept = Sequence()
        for item in list(elem.value or []):
            if self._clean_content_item(item):
                kept.append(item)
        elem.value = kept

    def _clean_content_item(self, item: Dataset) -> bool:
        c = self.res.counts
        vt = str(item.get("ValueType", "") or "").upper()
        value_kws = _CONTENT_VALUE_KWS.get(vt, ())
        cn = item.get("ConceptNameCodeSequence")
        rule = None
        if cn:
            code = cn[0]
            cv = code.get("CodeValue") or code.get("LongCodeValue") or code.get("URNCodeValue") or ""
            rule = ps315.lookup_content(str(cv), str(code.get("CodingSchemeDesignator", "") or ""), vt)
        values = [item[kw] for kw in value_kws if kw in item]
        if rule is not None:
            action = ps315.resolve_content(rule, self.cfg.options)
            c[f"content_items_{action}"] += 1
            if action == "X":
                return False
            for v in values:
                if action == "Z":
                    _empty(v)
                elif action == "D":
                    v.value = _dummy_for(v, self.pseudo, self.offset)
                elif action == "U":
                    self._uid(v)
                elif action == "C":
                    self.clean(item, v)
                # K: keep the value as it is
        else:
            # concept not listed: the same by-VR safety net as for unlisted attributes
            c["content_items_unlisted"] += 1
            if vt == "PNAME":
                return False
            for v in values:
                self.safety(item, v)
        skip = frozenset(int(v.tag) for v in values)
        self.walk(item, skip)                      # concept codes, references, nested content
        return True

    def safety(self, ds: Dataset, elem: DataElement) -> None:
        vr = elem.VR
        kw = elem.keyword or ""
        if vr == "SQ":
            self.walk_seq(elem)
        elif vr in _DATE_VRS:
            self._date(elem, via="safety_net")
        elif vr == "PN":
            if _values(elem):
                _empty(elem)
                self.res.counts["safety_net:names_emptied"] += 1
        elif vr in _TEXT_VRS:
            self._text(elem, via="safety_net")
        elif vr in ("CS", "AE"):
            if any(self.known.find(str(v)) for v in _values(elem)):
                elem.value = DUMMY_TEXT[:16]
                self.res.counts["safety_net:text_scrubbed"] += 1
        elif vr == "UR":
            if kw not in _KEEP_URL_KWS and _values(elem):
                self._remove(ds, elem, "safety_net:urls_removed")
        elif vr in _BINARY_VRS:
            if kw == "ICCProfile":
                self._replace_icc(elem)
            elif kw not in _STRUCTURAL_BINARY:
                self._remove(ds, elem, "safety_net:binary_removed")
        elif vr == "UI" and self.cfg.remap_unlisted_uids:
            if kw in _NEVER_REMAP or kw.endswith("ClassUID"):
                return
            vals = [str(v) for v in _values(elem)]
            if vals and not all(v.startswith(_STANDARD_UID_ROOT) for v in vals):
                self._uid(elem)


def _age_from(dob: str, ref: str) -> int | None:
    try:
        b = _dt.datetime.strptime(dob[:8], "%Y%m%d").date()
        r = _dt.datetime.strptime(ref[:8], "%Y%m%d").date()
    except (ValueError, TypeError):
        return None
    years = r.year - b.year - ((r.month, r.day) < (b.month, b.day))
    return years if 0 <= years < 150 else None


def _age_string(years: int, cap: bool) -> tuple[str, bool]:
    if cap and years >= 90:
        return "090Y", True
    return f"{years:03d}Y", False


def _parse_as(value: str) -> int | None:
    v = (value or "").strip().upper()
    if len(v) == 4 and v[:3].isdigit() and v[3] == "Y":
        return int(v[:3])
    return None


def patient_key(ds: Dataset) -> str:
    """Stable key for one patient: the ID *with its issuer* (two clinics may both use '1001')."""
    pid = str(ds.get("PatientID", "") or "").strip()
    if pid:
        issuer = str(ds.get("IssuerOfPatientID", "") or "").strip()
        return "id:" + pid + (f"|{issuer}" if issuer else "")
    name = str(ds.get("PatientName", "") or "").strip()
    dob = str(ds.get("PatientBirthDate", "") or "").strip()
    if name or dob:
        return f"name:{name}|{dob}"
    return "study:" + str(ds.get("StudyInstanceUID", "") or "")


def deidentify_dataset(ds: Dataset, cfg: MetaConfig, pseudo: Pseudonymizer,
                       known: KnownIdentifiers | None = None) -> tuple[MetaResult, KnownIdentifiers]:
    """De-identify the attributes of ``ds`` **in place**. Returns ``(result, harvested_identifiers)``."""
    res = MetaResult()
    known = harvest_identifiers(ds, known)
    key = patient_key(ds)
    pid = pseudo.pseudo_id(key)
    res.patient_pseudonym = pid
    modified = "retain_longitudinal_modified_dates" in cfg.options
    offset = pseudo.date_offset_days(key) if modified else None
    res.date_offset_days = offset

    # values needed after cleaning
    orig_dob = str(ds.get("PatientBirthDate", "") or "")
    orig_ref = str(ds.get("StudyDate", "") or ds.get("AcquisitionDate", "") or ds.get("ContentDate", "") or "")
    orig_age = str(ds.get("PatientAge", "") or "")

    _Cleaner(cfg, pseudo, known, offset, res, str(ds.get("SOPClassUID", "") or "") or None).walk(ds)

    # -- identity replacement -----------------------------------------------------------
    ds.PatientID = pid
    ds.PatientName = pid if cfg.pseudonymize_patient else ""
    ds.PatientBirthDate = ""
    if "PatientSex" not in ds:
        ds.PatientSex = ""

    if "retain_patient_characteristics" in cfg.options:
        years = _parse_as(orig_age)
        if years is None and cfg.derive_age:
            years = _age_from(orig_dob, orig_ref)
            if years is not None:
                res.notes.append("Patient's Age derived from birth date and study date (PS3.15 E.1.1 note).")
        if years is not None:
            ds.PatientAge, capped = _age_string(years, cfg.hipaa_age_cap)
            if capped:
                res.notes.append("Age >= 90 aggregated to 090Y (HIPAA Safe Harbor).")
    elif "PatientAge" in ds:
        del ds.PatientAge

    # -- record the method -------------------------------------------------------------
    res.methods = ["basic", *sorted(cfg.options)]
    ds.PatientIdentityRemoved = "YES"
    if modified:
        ds.LongitudinalTemporalInformationModified = "MODIFIED"
    elif "retain_longitudinal_full_dates" in cfg.options:
        ds.LongitudinalTemporalInformationModified = "UNMODIFIED"
    else:
        ds.LongitudinalTemporalInformationModified = "REMOVED"
    write_method_record(ds, res.methods, cfg)

    if cfg.allowlist:
        keep = allowlist_tags()
        for elem in list(ds):
            if int(elem.tag) not in keep:
                del ds[elem.tag]
                res.counts["allowlist_removed"] += 1
        # allow-listed for IOD validity, but free text a vendor or user can type anything into
        for kw in _ALLOWLIST_EMPTIED:
            if kw in ds and ds[kw].value not in (None, ""):
                ds[kw].value = None
                res.counts["allowlist_free_text_emptied"] += 1

    replace_file_meta(ds)
    return res, known


def write_method_record(ds: Dataset, methods: list[str], cfg: MetaConfig | None = None) -> None:
    """(Re)write De-identification Method (0012,0063) and its Code Sequence (0012,0064)."""
    texts = [f"DentDeID {__version__}", f"DICOM PS3.15 {ps315.EDITION} Basic Profile"]
    if cfg is not None and cfg.allowlist:
        texts.append("DentDeID dental allow-list v2")
    texts.append("Keyed pseudonyms (HMAC-SHA-256)")
    ds.DeidentificationMethod = [t[:64] for t in texts]
    seq = Sequence()
    seen = set()
    for m in methods:
        if m in seen or m not in ps315.CID7050:
            continue
        seen.add(m)
        code, meaning = ps315.CID7050[m]
        item = Dataset()
        item.CodeValue = code
        item.CodingSchemeDesignator = "DCM"
        item.CodeMeaning = meaning
        seq.append(item)
    ds.DeidentificationMethodCodeSequence = seq


def add_method(ds: Dataset, res: MetaResult, method: str, cfg: MetaConfig | None = None) -> None:
    if method not in res.methods:
        res.methods.append(method)
    write_method_record(ds, res.methods, cfg)


def replace_file_meta(ds: Dataset, transfer_syntax: str | None = None) -> None:
    """Replace File Meta Information and preamble with a description of DentDeID."""
    old = getattr(ds, "file_meta", None)
    ts = transfer_syntax or (old.get("TransferSyntaxUID") if old is not None else None) or ExplicitVRLittleEndian
    fm = FileMetaDataset()
    fm.FileMetaInformationVersion = b"\x00\x01"
    fm.MediaStorageSOPClassUID = ds.get("SOPClassUID", "1.2.840.10008.5.1.4.1.1.7")
    fm.MediaStorageSOPInstanceUID = ds.get("SOPInstanceUID", "")
    fm.TransferSyntaxUID = ts
    fm.ImplementationClassUID = IMPLEMENTATION_CLASS_UID
    fm.ImplementationVersionName = IMPLEMENTATION_VERSION_NAME
    ds.file_meta = fm
    ds.preamble = b"\x00" * 128


def residual_identifiers(ds: Dataset, known: KnownIdentifiers) -> list[dict]:
    """Every string attribute (recursively) that still contains a harvested identifier.

    Returns ``[{"tag": "(gggg,eeee)", "keyword": ..., "kinds": [...]}]`` — never the values.
    """
    hits: list[dict] = []

    def visit(d: Dataset) -> None:
        for elem in d:
            if elem.VR == "SQ":
                for item in elem.value or []:
                    visit(item)
                continue
            if elem.VR not in _TEXT_VRS | {"PN", "CS", "AE", "DA", "DT", "UR"}:
                continue
            for v in _values(elem):
                fs = known.find(str(v))
                if fs:
                    hits.append({"tag": f"({elem.tag.group:04X},{elem.tag.element:04X})",
                                 "keyword": elem.keyword, "kinds": sorted({f.kind for f in fs})})
    visit(ds)
    return hits

