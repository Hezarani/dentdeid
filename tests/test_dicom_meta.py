import io

import pydicom
import pytest

from dentdeid.dicom.meta import (IMPLEMENTATION_CLASS_UID, MetaConfig, allowlist_tags, deidentify_dataset,
                                 harvest_identifiers, residual_identifiers)
from dentdeid.keys import Pseudonymizer
from dentdeid.phi import KnownIdentifiers

RESEARCH = MetaConfig(options=frozenset({"retain_patient_characteristics", "retain_longitudinal_modified_dates",
                                         "clean_descriptors"}))


def _roundtrip(ds):
    buf = io.BytesIO()
    ds.save_as(buf, enforce_file_format=True)
    buf.seek(0)
    return pydicom.dcmread(buf)


def test_harvest_collects_identity(dicom_ds, ident):
    k = harvest_identifiers(dicom_ds)
    kinds = k.kinds()
    assert {"patient-name", "patient-id", "birth-date", "staff-name"} <= set(kinds)
    assert k.find(f"{ident.given} {ident.family}")


def test_basic_profile_removes_identity(fresh, ident):
    ds = fresh()
    res, known = deidentify_dataset(ds, MetaConfig(), Pseudonymizer(b"k" * 32))
    out = _roundtrip(ds)
    assert out.PatientIdentityRemoved == "YES"
    assert out.PatientName == out.PatientID == res.patient_pseudonym
    assert out.PatientBirthDate == ""
    assert "PatientAge" not in out
    assert not [e for e in out.iterall() if e.tag.is_private]
    assert not [e for e in out if 0x6000 <= e.tag.group <= 0x601E]
    assert "IconImageSequence" not in out and "RequestAttributesSequence" not in out
    assert "StudyDescription" not in out and "ImageComments" not in out
    assert out.LongitudinalTemporalInformationModified == "REMOVED"
    assert [i.CodeValue for i in out.DeidentificationMethodCodeSequence] == ["113100"]
    assert residual_identifiers(out, known) == []


def test_file_meta_and_preamble_replaced(fresh):
    ds = fresh()
    deidentify_dataset(ds, MetaConfig(), Pseudonymizer(b"k" * 32))
    out = _roundtrip(ds)
    assert out.file_meta.ImplementationClassUID == IMPLEMENTATION_CLASS_UID
    assert "SourceApplicationEntityTitle" not in out.file_meta
    assert out.preamble == b"\x00" * 128
    assert out.file_meta.MediaStorageSOPInstanceUID == out.SOPInstanceUID


def test_safety_net_catches_vendor_misuse(fresh, ident):
    ds = fresh()
    assert ident.patient_id in ds.FilterType            # FilterType is not in Table E.1-1
    res, _ = deidentify_dataset(ds, MetaConfig(), Pseudonymizer(b"k" * 32))
    assert ident.patient_id not in str(ds.get("FilterType", ""))
    assert res.counts["safety_net:text_scrubbed"] >= 1


def test_research_profile_keeps_age_sex_and_shifts_dates(fresh, ident):
    ds = fresh()
    orig_study = ds.StudyDate
    res, known = deidentify_dataset(ds, RESEARCH, Pseudonymizer(b"k" * 32))
    assert ds.PatientSex == ident.sex
    assert ds.PatientAge == f"{min(ident.age_at_exam(), 90):03d}Y"
    assert ds.StudyDate and ds.StudyDate != orig_study
    assert ds.LongitudinalTemporalInformationModified == "MODIFIED"
    assert ident.family not in ds.StudyDescription and "control" in ds.StudyDescription
    assert residual_identifiers(ds, known) == []
    assert {i.CodeValue for i in ds.DeidentificationMethodCodeSequence} == {"113100", "113105", "113107", "113108"}


def test_age_cap_at_90(fresh):
    ds = fresh()
    ds.PatientAge = "093Y"
    deidentify_dataset(ds, RESEARCH, Pseudonymizer(b"k" * 32))
    assert ds.PatientAge == "090Y"


def test_age_derived_when_missing(fresh, ident):
    ds = fresh()
    del ds.PatientAge
    res, _ = deidentify_dataset(ds, RESEARCH, Pseudonymizer(b"k" * 32))
    assert ds.PatientAge == f"{min(ident.age_at_exam(), 90):03d}Y"
    assert any("derived" in n for n in res.notes)


def test_consistency_across_instances_of_one_study(fresh):
    a, b = fresh(), fresh()
    b.SOPInstanceUID = "1.2.3.999"
    b.StudyDate = "20200101"
    a.StudyDate = "20200111"
    p = Pseudonymizer(b"k" * 32)
    deidentify_dataset(a, RESEARCH, p)
    deidentify_dataset(b, RESEARCH, p)
    assert a.StudyInstanceUID == b.StudyInstanceUID and a.SOPInstanceUID != b.SOPInstanceUID
    assert a.PatientID == b.PatientID
    from datetime import datetime
    d = (datetime.strptime(a.StudyDate, "%Y%m%d") - datetime.strptime(b.StudyDate, "%Y%m%d")).days
    assert d == 10                                          # interval preserved by a per-patient shift


def test_different_keys_give_unlinkable_output(fresh):
    a, b = fresh(), fresh()
    deidentify_dataset(a, MetaConfig(), Pseudonymizer(b"a" * 32))
    deidentify_dataset(b, MetaConfig(), Pseudonymizer(b"b" * 32))
    assert a.PatientID != b.PatientID and a.StudyInstanceUID != b.StudyInstanceUID


def test_allowlist_mode(fresh):
    ds = fresh()
    ds.CineRate = "10"                                   # harmless, but not on the allow-list
    res, _ = deidentify_dataset(ds, MetaConfig(allowlist=True), Pseudonymizer(b"k" * 32))
    keep = allowlist_tags()
    assert all(int(e.tag) in keep for e in ds)
    assert "Manufacturer" in ds and "KVP" in ds and "PixelData" in ds
    assert res.counts["allowlist_removed"] > 0


def test_mutually_exclusive_date_options():
    with pytest.raises(ValueError):
        MetaConfig(options=frozenset({"retain_longitudinal_full_dates", "retain_longitudinal_modified_dates"}))
    with pytest.raises(ValueError):
        MetaConfig(options=frozenset({"not_an_option"}))


def test_residual_detector_flags_leaks(fresh, ident):
    ds = fresh()
    known = harvest_identifiers(ds, KnownIdentifiers())
    hits = residual_identifiers(ds, known)
    assert any(h["keyword"] == "PatientName" for h in hits)
    assert all("value" not in h for h in hits)               # never returns values
