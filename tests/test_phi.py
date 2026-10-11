import pytest

from dentdeid.phi import KnownIdentifiers, date_renderings, find_phi, fold, is_safe_burned_in_text, scrub_text


def kinds(text, known=None):
    return sorted({f.kind for f in find_phi(text, known)})


def test_fold_turkish_and_accents():
    assert fold("İstanbul Işık Öztürk Müller")[0] == "istanbul isik ozturk muller"


def test_known_names_match_case_accent_and_order():
    k = KnownIdentifiers()
    k.add_name("Yılmaz^Ayşe^Nur", "patient-name")
    for t in ("AYSE YILMAZ", "Yilmaz, Ayşe", "ayşe", "YILMAZ^AYSE"):
        assert "known:patient-name" in kinds(t, k), t
    assert not k.find("Ayşegül")          # word boundaries: no partial-word hits
    assert not k.find("Dr. Kemal")


def test_honorifics_are_not_identifiers():
    k = KnownIdentifiers()
    k.add_name("Dr. Kemal Öztürk", "staff-name")
    assert not k.find("Dr. Smith")
    assert k.find("kemal ozturk")


def test_birth_date_found_in_many_spellings():
    k = KnownIdentifiers()
    k.add_date("19870312", "birth-date")
    for t in ("12.03.1987", "1987-03-12", "3/12/1987", "12/03/87", "12 March 1987", "12 Mart 1987", "March 12, 1987"):
        assert "known:birth-date" in kinds(t, k), t
    assert len(date_renderings("19870312")) > 20
    assert date_renderings("bad") == set()


@pytest.mark.parametrize("text,kind", [
    ("seen 12.03.2024", "date"), ("2024-03-12", "date"), ("20240312", "date"),
    ("call +90 312 555 12 34", "phone"), ("(0312) 555 1234", "phone"),
    ("mail a.b@clinic.com", "email"), ("see www.example.org/x", "url"),
    ("host 192.168.1.20", "ip-address"), ("ssn 123-45-6789", "us-ssn"),
    ("age 93 years", "age-over-89"), ("record 4839201", "id-number"),
    ("Hasta Adı: Zeynep Kara", "labelled-value"), ("Geb.: 01.02.1990", "labelled-value"),
    ("Patient: John Doe", "labelled-value"),
])
def test_patterns(text, kind):
    assert kind in kinds(text), (text, kinds(text))


def test_tckn_and_nhs_checksums():
    assert "tr-national-id" in kinds("TC 10000000146")
    assert "tr-national-id" not in kinds("TC 10000000147")
    assert "uk-nhs-number" in kinds("NHS 943 476 5919".replace(" ", ""))


@pytest.mark.parametrize("text", ["1.2.840.10008.5.1.4.1.1.1.3", "kV 70 mA 10 s 0.32", "PAN 2D 15x8 STANDARD",
                                  "0.1\\0.1", "B30f"])
def test_no_false_positive_on_technical_strings(text):
    assert not [f for f in find_phi(text) if f.kind not in ("time",)], kinds(text)


def test_scrub_keeps_clinical_words():
    k = KnownIdentifiers()
    k.add_name("Yılmaz^Ayşe")
    clean, found = scrub_text("PA 36 - Ayşe Yılmaz kontrol", k)
    assert clean == "PA 36 kontrol" and found


def test_safe_burned_in_tokens():
    assert is_safe_burned_in_text("70 kV 10 mA 14.1 s")
    assert is_safe_burned_in_text("R")
    assert is_safe_burned_in_text("12.4 mm")
    assert not is_safe_burned_in_text("Ayse 70kV")
    assert not is_safe_burned_in_text("")
