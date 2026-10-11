#!/usr/bin/env python3
"""Regenerate ``dentdeid/data/ps315_<edition>.json`` from the official DICOM standard.

The DICOM Standards Committee publishes the standard as machine-readable DocBook XML.
This script parses PS3.15 Annex E, Table E.1-1 (the Basic Application Level
Confidentiality Profile and its Options) and Table E.3.4-1 (Clean Structured Content Option:
content-item concept names) into a compact JSON rule table, so that the de-identification
actions DentDeID applies are *derived from the standard*, not typed by hand.

Usage::

    python scripts/build_ps315_table.py                   # downloads the current edition
    python scripts/build_ps315_table.py --xml part15.xml  # uses a local copy

The attribute tags, names and action codes are reproduced as factual interoperability
data with attribution. DICOM® is a registered trademark of NEMA; the DICOM Standard is
© NEMA. No descriptive prose from the standard is reproduced.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

URL = "https://dicom.nema.org/medical/dicom/current/source/docbook/part15/part15.xml"
DB = "{http://docbook.org/ns/docbook}"
XID = "{http://www.w3.org/XML/1998/namespace}id"

# Column order of Table E.1-1 (verified against the header row at build time).
EXPECTED_HEADER = [
    "Attribute Name", "Tag", "Retd.", "In Std. Comp. IOD", "Basic Prof.",
    "Rtn. Safe Priv. Opt.", "Rtn. UIDs Opt.", "Rtn. Dev. Id. Opt.", "Rtn. Inst. Id. Opt.",
    "Rtn. Pat. Chars. Opt.", "Rtn. Long. Full Dates Opt.", "Rtn. Long. Modif. Dates Opt.",
    "Clean Desc. Opt.", "Clean Struct. Cont. Opt.", "Clean Graph. Opt.",
]
OPTION_KEYS = [
    "retain_safe_private", "retain_uids", "retain_device_identity", "retain_institution_identity",
    "retain_patient_characteristics", "retain_longitudinal_full_dates",
    "retain_longitudinal_modified_dates", "clean_descriptors", "clean_structured_content",
    "clean_graphics",
]
VALID_ACTIONS = {"", "X", "Z", "D", "K", "C", "U", "X/Z", "X/D", "Z/D", "X/Z/D", "X/Z/U*"}

# Column order of Table E.3.4-1 (Clean Structured Content Option content items).
EXPECTED_CONTENT_HEADER = [
    "Code Meaning", "Code Value", "Coding Scheme Designator", "Value Type", "Retd.", "In Std. Tpl.",
    "Basic Prof.", "Rtn. UIDs Opt.", "Rtn. Dev. Id. Opt.", "Rtn. Inst. Id. Opt.", "Rtn. Pat. Chars. Opt.",
    "Rtn. Long. Full Dates Opt.", "Rtn. Long. Modif. Dates Opt.", "Clean Desc. Opt.",
]
CONTENT_OPTION_KEYS = [
    "retain_uids", "retain_device_identity", "retain_institution_identity",
    "retain_patient_characteristics", "retain_longitudinal_full_dates",
    "retain_longitudinal_modified_dates", "clean_descriptors",
]


def _text(el) -> str:
    s = "".join(el.itertext()).replace("​", "")
    return re.sub(r"\s+", " ", s).strip()


def _norm_header(h: str) -> str:
    return re.sub(r"\s*\(from\s*\)", "", h).strip()


def parse(xml_bytes: bytes) -> dict:
    root = ET.fromstring(xml_bytes)
    subtitle = root.find(f"{DB}subtitle")
    edition_m = re.search(r"PS3\.15 (\d{4}[a-z])", _text(subtitle) if subtitle is not None else "")
    edition = edition_m.group(1) if edition_m else "unknown"

    table = next(e for e in root.iter() if e.get(XID) == "table_E.1-1")
    header = [_norm_header(_text(c)) for c in next(table.find(f"{DB}thead").iter(f"{DB}tr"))]
    if header != EXPECTED_HEADER:
        raise SystemExit(f"Unexpected Table E.1-1 header — review the parser:\n{header}")

    rules = []
    for tr in table.find(f"{DB}tbody").iter(f"{DB}tr"):
        cells = [_text(c) for c in tr]
        if len(cells) != len(EXPECTED_HEADER):
            raise SystemExit(f"Bad row: {cells}")
        name, tag, retired, in_iod, basic = cells[:5]
        opts = {k: v for k, v in zip(OPTION_KEYS, cells[5:]) if v}
        for v in [basic, *opts.values()]:
            if v not in VALID_ACTIONS:
                raise SystemExit(f"Unknown action code {v!r} in row {cells}")
        m = re.fullmatch(r"\(([0-9A-Fa-fx]{4}),([0-9A-Fa-fx]{4})\)", tag)
        if m:
            key = (m.group(1) + m.group(2)).upper().replace("X", "x")
        elif tag.startswith("(gggg,eeee)"):
            key = "PRIVATE"
        else:
            raise SystemExit(f"Unparsed tag {tag!r}")
        rules.append({
            "tag": key, "name": name, "retired": retired == "Y",
            "in_std_iod": in_iod == "Y", "basic": basic, "options": opts,
        })
    content = []
    ctable = next(e for e in root.iter() if e.get(XID) == "table_E.3.4-1")
    cheader = [_norm_header(_text(c)) for c in next(ctable.find(f"{DB}thead").iter(f"{DB}tr"))]
    if cheader != EXPECTED_CONTENT_HEADER:
        raise SystemExit(f"Unexpected Table E.3.4-1 header — review the parser:\n{cheader}")
    for tr in ctable.find(f"{DB}tbody").iter(f"{DB}tr"):
        cells = [_text(c) for c in tr]
        if len(cells) != len(EXPECTED_CONTENT_HEADER):
            raise SystemExit(f"Bad content row: {cells}")
        meaning, code, scheme, vtype, retired, in_tpl, basic = cells[:7]
        opts = {k: v for k, v in zip(CONTENT_OPTION_KEYS, cells[7:]) if v}
        for v in [basic, *opts.values()]:
            if v not in VALID_ACTIONS:
                raise SystemExit(f"Unknown action code {v!r} in content row {cells}")
        content.append({"code": code, "scheme": scheme, "meaning": meaning, "value_type": vtype,
                        "retired": retired == "Y", "basic": basic, "options": opts})
    return {"edition": edition, "rules": rules, "content_items": content}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xml", help="local path to part15.xml (default: download)")
    ap.add_argument("--out-dir", default=str(Path(__file__).resolve().parents[1] / "dentdeid" / "data"))
    a = ap.parse_args(argv)
    if a.xml:
        data = Path(a.xml).read_bytes()
        src = a.xml
    else:
        with urllib.request.urlopen(URL, timeout=120) as r:  # noqa: S310 (fixed https URL)
            data = r.read()
        src = URL
    parsed = parse(data)
    doc = {
        "source": {
            "standard": "DICOM PS3.15 — Security and System Management Profiles",
            "table": "Annex E, Table E.1-1 (Application Level Confidentiality Profile Attributes) and "
                     "Table E.3.4-1 (Clean Structured Content Option Content Item Concept Name Codes)",
            "edition": parsed["edition"],
            "url": URL,
            "sha256_of_source_xml": hashlib.sha256(data).hexdigest(),
            "generated": _dt.date.today().isoformat(),
            "generator": "scripts/build_ps315_table.py",
            "attribution": "Derived from the DICOM Standard (c) NEMA. DICOM is a registered trademark of "
                           "NEMA. Tags, attribute names and action codes are reproduced as factual "
                           "interoperability data; no descriptive text is reproduced.",
        },
        "action_codes": {
            "D": "replace with a non-zero-length dummy value consistent with the VR",
            "Z": "replace with a zero-length value (or a non-zero dummy)",
            "X": "remove",
            "K": "keep (sequences are cleaned recursively)",
            "C": "clean: replace with values of similar meaning known not to identify",
            "U": "replace with an internally consistent UID",
        },
        "option_keys": OPTION_KEYS,
        "rules": parsed["rules"],
        "content_option_keys": CONTENT_OPTION_KEYS,
        "content_items": parsed["content_items"],
    }
    out = Path(a.out_dir) / f"ps315_{parsed['edition']}.json"
    out.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(parsed['rules'])} rules, {len(parsed['content_items'])} content items, "
          f"edition {parsed['edition']}) from {src}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
