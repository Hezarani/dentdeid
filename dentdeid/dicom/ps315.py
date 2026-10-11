"""DICOM PS3.15 Annex E rule table (generated from the standard) and action resolution."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib import resources

EDITION = "2026d"

#: PS3.16 CID 7050 "De-identification Method" codes (Coding Scheme Designator DCM).
CID7050 = {
    "basic": ("113100", "Basic Application Confidentiality Profile"),
    "clean_pixel_data": ("113101", "Clean Pixel Data Option"),
    "clean_recognizable_visual_features": ("113102", "Clean Recognizable Visual Features Option"),
    "clean_graphics": ("113103", "Clean Graphics Option"),
    "clean_structured_content": ("113104", "Clean Structured Content Option"),
    "clean_descriptors": ("113105", "Clean Descriptors Option"),
    "retain_longitudinal_full_dates": ("113106", "Retain Longitudinal Temporal Information Full Dates Option"),
    "retain_longitudinal_modified_dates": ("113107", "Retain Longitudinal Temporal Information Modified Dates Option"),
    "retain_patient_characteristics": ("113108", "Retain Patient Characteristics Option"),
    "retain_device_identity": ("113109", "Retain Device Identity Option"),
    "retain_uids": ("113110", "Retain UIDs Option"),
    "retain_safe_private": ("113111", "Retain Safe Private Option"),
    "retain_institution_identity": ("113112", "Retain Institution Identity Option"),
}

#: Options DentDeID implements for the non-pixel attributes (Table E.1-1 columns).
#: (Clean Graphics and Retain Safe Private are not implemented: graphic annotations and all
#: private attributes are removed.)
SUPPORTED_TABLE_OPTIONS = (
    "retain_uids", "retain_device_identity", "retain_institution_identity",
    "retain_patient_characteristics", "retain_longitudinal_full_dates",
    "retain_longitudinal_modified_dates", "clean_descriptors", "clean_structured_content",
)

# --- compound action codes (PS3.15 Table E.1-1a) ------------------------------------------
# "X/Z" = X unless Z is required to keep the IOD valid (Type 2); "X/D" = X unless D is
# required (Type 1); "Z/D" = Z unless D is required; "X/Z/D" = X unless Z or D is required;
# "X/Z/U*" = X unless Z or replacement of contained UIDs is required.
# DentDeID does not carry the full IOD module tables, so it removes (X) or empties (Z) by
# default and uses the "required" alternative only where the attribute is known to be
# Type 1/2 in an IOD that dental images use:
_T2_ALWAYS = {0x00400555}                    # Acquisition Context Sequence (DX, IO, MG: Type 2)
_ENHANCED_SOP_CLASSES = {                    # Enhanced General Equipment / Enhanced image modules
    "1.2.840.10008.5.1.4.1.1.2.1",           # Enhanced CT
    "1.2.840.10008.5.1.4.1.1.2.2",           # Legacy Converted Enhanced CT
    "1.2.840.10008.5.1.4.1.1.4.1",           # Enhanced MR
    "1.2.840.10008.5.1.4.1.1.12.1.1",        # Enhanced XA
    "1.2.840.10008.5.1.4.1.1.13.1.1",        # X-Ray 3D Angiographic
    "1.2.840.10008.5.1.4.1.1.13.1.2",        # X-Ray 3D Craniofacial (dental CBCT)
    "1.2.840.10008.5.1.4.1.1.13.1.3",        # Breast Tomosynthesis
}
_T1_ENHANCED = {0x00181000,                  # Device Serial Number
                0x0008002A,                  # Acquisition DateTime
                0x00080023, 0x00080033}      # Content Date / Content Time
_VL_SOP_PREFIX = "1.2.840.10008.5.1.4.1.1.77.1."   # VL (photographic) images: VL Image Module
_T1_VL = {0x00080023, 0x00080033}            # Content Date / Content Time (Type 1C there)


def _compound(action: str, tag: int, sop_class: str | None) -> str:
    if "/" not in action:
        return action
    enhanced = (sop_class or "") in _ENHANCED_SOP_CLASSES
    if "D" in action.split("/") and enhanced and tag in _T1_ENHANCED:
        return "D"
    if "D" in action.split("/") and (sop_class or "").startswith(_VL_SOP_PREFIX) and tag in _T1_VL:
        return "D"
    if action == "X/Z/U*":
        # References to other instances are kept with every contained UID replaced
        # (referential integrity; the items carry no identity once their UIDs are remapped).
        return "U"
    if action.startswith("Z"):
        return "Z"                            # Z/D: the attribute is at least Type 2
    if "Z" in action and tag in _T2_ALWAYS:
        return "Z"
    return "X"


@dataclass(frozen=True)
class Rule:
    tag: str           # 8 hex digits, may contain lower-case 'x' wildcards; or "PRIVATE"
    name: str
    basic: str
    options: dict
    retired: bool
    in_std_iod: bool


@lru_cache(maxsize=None)
def _load() -> tuple[dict, list[tuple[re.Pattern, Rule]], dict]:
    raw = json.loads(resources.files("dentdeid").joinpath(f"data/ps315_{EDITION}.json").read_text("utf-8"))
    exact: dict[int, Rule] = {}
    wild: list[tuple[re.Pattern, Rule]] = []
    for r in raw["rules"]:
        rule = Rule(r["tag"], r["name"], r["basic"], dict(r["options"]), r["retired"], r["in_std_iod"])
        if r["tag"] == "PRIVATE":
            continue
        if "x" in r["tag"]:
            wild.append((re.compile("^" + r["tag"].replace("x", "[0-9A-F]") + "$"), rule))
        else:
            exact[int(r["tag"], 16)] = rule
    return exact, wild, raw["source"]


def source_info() -> dict:
    return dict(_load()[2])


def rule_count() -> int:
    exact, wild, _ = _load()
    return len(exact) + len(wild)


def lookup(tag: int) -> Rule | None:
    exact, wild, _ = _load()
    r = exact.get(int(tag))
    if r is not None:
        return r
    h = f"{int(tag):08X}"
    for rx, rule in wild:
        if rx.match(h):
            # Overlay/curve repeating groups apply to even groups only.
            return rule
    return None


def _choose(basic: str, opts: dict, options) -> str:
    opt_actions = [opts[o] for o in options if o in opts]
    if opt_actions:
        if "C" in opt_actions:
            return "C"
        if "K" in opt_actions:
            return "K"
        return opt_actions[0]
    return basic


def resolve(rule: Rule, options: frozenset[str], sop_class: str | None = None) -> str:
    """Final single-letter action for ``rule`` given the enabled Table E.1-1 options.

    An enabled Option's requirement overrides the Basic Profile (PS3.15 E.1.1). When two
    enabled options disagree (e.g. Retain Device Identity says K while Retain Modified Dates
    says C for a calibration date), the more protective C wins. Compound codes are resolved
    by :func:`_compound`.
    """
    action = _choose(rule.basic, rule.options, options)
    tag = int(rule.tag, 16) if rule.tag != "PRIVATE" and "x" not in rule.tag else 0
    return _compound(action, tag, sop_class)


# --- Table E.3.4-1: content items (Clean Structured Content Option) ------------------------
@dataclass(frozen=True)
class ContentRule:
    code: str
    scheme: str
    meaning: str
    value_type: str
    basic: str
    options: dict


@lru_cache(maxsize=None)
def _content() -> dict[tuple[str, str, str], ContentRule]:
    raw = json.loads(resources.files("dentdeid").joinpath(f"data/ps315_{EDITION}.json").read_text("utf-8"))
    out = {}
    for r in raw.get("content_items", []):
        out[(r["code"], r["scheme"], r["value_type"])] = ContentRule(
            r["code"], r["scheme"], r["meaning"], r["value_type"], r["basic"], dict(r["options"]))
    return out


def content_rule_count() -> int:
    return len(_content())


def lookup_content(code_value: str, scheme: str, value_type: str = "") -> ContentRule | None:
    """Rule for a content item's concept name (a few codes have one row per value type)."""
    code, scheme, vt = str(code_value).strip(), str(scheme).strip(), str(value_type).strip().upper()
    table = _content()
    hit = table.get((code, scheme, vt))
    if hit is None:
        same = [r for (c, s, _), r in table.items() if c == code and s == scheme]
        if same:  # value type not listed for this concept: use its most protective row
            rank = {"X": 0, "X/D": 1, "D": 2, "Z": 3, "C": 4, "U": 5, "K": 6}
            hit = min(same, key=lambda r: rank.get(r.basic, 9))
    return hit


def resolve_content(rule: ContentRule, options: frozenset[str]) -> str:
    """Action for a content item (X/D resolves to X: the item is removed unless required)."""
    action = _choose(rule.basic, rule.options, options)
    return "X" if action == "X/D" else action
