import os
import stat
import uuid

from dentdeid.keys import Pseudonymizer, load_or_create_key, shift_da, shift_dt


def test_pseudonyms_are_keyed_and_stable():
    a, b = Pseudonymizer(b"a" * 32), Pseudonymizer(b"b" * 32)
    assert a.pseudo_id("483920") == a.pseudo_id("483920")
    assert a.pseudo_id("483920") != b.pseudo_id("483920")
    assert a.pseudo_id("483920").startswith("DDID-") and len(a.pseudo_id("x")) == 15
    assert Pseudonymizer().ephemeral and not a.ephemeral


def test_uids_valid_and_consistent():
    p = Pseudonymizer(b"k" * 32)
    u = p.uid("1.2.3.4.5")
    assert u == p.uid("1.2.3.4.5") and u != p.uid("1.2.3.4.6")
    assert u.startswith("2.25.") and len(u) <= 64
    parsed = uuid.UUID(int=int(u[5:]))
    assert parsed.version == 8 and parsed.variant == uuid.RFC_4122


def test_date_offsets_per_patient():
    p = Pseudonymizer(b"k" * 32)
    d = p.date_offset_days("id:1")
    assert 30 <= d <= 3650 and d == p.date_offset_days("id:1")
    assert shift_da("20260311", 10) == "20260301"
    assert shift_da("20260101-20260131", 1) == "20251231-20260130"
    assert shift_dt("20260311101500.123", 1) == "20260310101500.123"


def test_key_file_created_private(tmp_path):
    f = tmp_path / "project.key"
    k1 = load_or_create_key(f)
    assert len(k1) == 32 and load_or_create_key(f) == k1
    if os.name == "posix":
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
