"""The PS3.15 rule table is generated from the standard; check it is complete and sane."""
import json
from importlib import resources

import pytest
from pydicom.datadict import tag_for_keyword

from dentdeid.dicom import ps315

VALID = {"", "X", "Z", "D", "K", "C", "U", "X/Z", "X/D", "Z/D", "X/Z/D", "X/Z/U*"}


def _raw():
    return json.loads(resources.files("dentdeid").joinpath(f"data/ps315_{ps315.EDITION}.json").read_text("utf-8"))


def test_edition_and_provenance():
    src = ps315.source_info()
    assert src["edition"] == "2026d"
    assert len(src["sha256_of_source_xml"]) == 64
    assert "NEMA" in src["attribution"]


def test_rule_count_and_codes():
    raw = _raw()
    assert len(raw["rules"]) >= 650            # 657 rows in PS3.15 2026d Table E.1-1
    for r in raw["rules"]:
        assert r["basic"] in VALID
        for v in r["options"].values():
            assert v in VALID
    assert ps315.rule_count() == len(raw["rules"]) - 1   # minus the private-attribute pseudo-row


@pytest.mark.parametrize("kw,basic,resolved", [
    ("PatientName", "Z", "Z"),
    ("PatientID", "Z/D", "Z"),
    ("PatientBirthDate", "Z", "Z"),
    ("PatientAge", "X", "X"),
    ("SOPInstanceUID", "U", "U"),
    ("StudyInstanceUID", "U", "U"),
    ("InstitutionName", "X/Z/D", "X"),
    ("StudyDate", "Z", "Z"),
    ("IconImageSequence", "X", "X"),
    ("OriginalAttributesSequence", "X", "X"),
    ("DigitalSignaturesSequence", "X", "X"),
])
def test_known_attributes(kw, basic, resolved):
    r = ps315.lookup(tag_for_keyword(kw))
    assert r is not None and r.basic == basic
    assert ps315.resolve(r, frozenset()) == resolved


def test_options_override_basic():
    age = ps315.lookup(tag_for_keyword("PatientAge"))
    assert ps315.resolve(age, frozenset({"retain_patient_characteristics"})) == "K"
    sd = ps315.lookup(tag_for_keyword("StudyDate"))
    assert ps315.resolve(sd, frozenset({"retain_longitudinal_modified_dates"})) == "C"
    assert ps315.resolve(sd, frozenset({"retain_longitudinal_full_dates"})) == "K"
    desc = ps315.lookup(tag_for_keyword("StudyDescription"))
    assert ps315.resolve(desc, frozenset()) == "X"
    assert ps315.resolve(desc, frozenset({"clean_descriptors"})) == "C"


def test_conflicting_options_prefer_clean():
    cal = ps315.lookup(tag_for_keyword("DateOfLastCalibration"))
    assert ps315.resolve(cal, frozenset({"retain_device_identity", "retain_longitudinal_modified_dates"})) == "C"


def test_repeating_groups():
    assert ps315.lookup(0x60003000).name == "Overlay Data"
    assert ps315.lookup(0x601E3000).name == "Overlay Data"
    assert ps315.lookup(0x50000010) is not None


def test_cid7050_codes():
    codes = [c for c, _ in ps315.CID7050.values()]
    assert codes == [str(n) for n in range(113100, 113113)]


def test_compound_codes_follow_iod_requirements():
    dsn = ps315.lookup(tag_for_keyword("DeviceSerialNumber"))
    assert ps315.resolve(dsn, frozenset()) == "X"                                   # Type 3 in DX/IO
    assert ps315.resolve(dsn, frozenset(), "1.2.840.10008.5.1.4.1.1.13.1.2") == "D"   # Type 1 (enhanced)
    acq = ps315.lookup(tag_for_keyword("AcquisitionContextSequence"))
    assert ps315.resolve(acq, frozenset()) == "Z"                                   # Type 2 in DX/IO
    ref = ps315.lookup(tag_for_keyword("ReferencedImageSequence"))
    assert ps315.resolve(ref, frozenset()) == "U"


def test_content_item_table_is_parsed():
    assert ps315.content_rule_count() == 214
    r = ps315.lookup_content("121031", "DCM", "DATE")                 # Subject Birth Date
    assert r is not None and ps315.resolve_content(r, frozenset({"retain_longitudinal_modified_dates"})) == "X"
    org = ps315.lookup_content("113873", "DCM", "TEXT")               # Organization Name
    assert ps315.resolve_content(org, frozenset()) == "X"
    assert ps315.resolve_content(org, frozenset({"retain_institution_identity"})) == "K"


def test_clean_graphics_is_not_claimed():
    assert "clean_graphics" not in ps315.SUPPORTED_TABLE_OPTIONS
