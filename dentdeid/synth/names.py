"""Clearly synthetic identities for test data (no real people, no real registries).

Names are assembled from neutral syllables, so they read like names (and exercise name
detection) without being drawn from any list of real persons. IDs are random digit strings
without national-ID checksums.
"""
from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass

import numpy as np

_SYL = ["ka", "ro", "mi", "ta", "ne", "su", "li", "do", "ve", "ra", "zo", "pe", "hu", "ni", "ba",
        "se", "lo", "ti", "me", "ya", "ze", "fu", "gi", "no", "ar", "el", "in", "os", "un", "em",
        "da", "ko", "ri", "sa", "vo", "le"]
_GIVEN_END = ["a", "e", "o", "an", "en", "is", "ia", "ur"]
_FAMILY_END = ["son", "er", "ova", "yan", "oglu", "sen", "ez", "ini", "berg", "zadeh", "ic", "etti"]
_CLINIC = ["Dental Clinic", "Diş Kliniği", "Zahnarztpraxis", "Oral Surgery Centre", "Tandvårdsklinik",
           "Dental Studio", "Ağız ve Diş Sağlığı Merkezi"]
_STREETS = ["Lale Sk.", "Hauptstr.", "Maple Ave", "Kungsgatan", "Atatürk Blv.", "Rue Verte"]
_CITIES = ["Ankara", "Berlin", "Uppsala", "Leeds", "İzmir", "Lyon"]


def _word(rng: np.random.Generator, n: int) -> str:
    return "".join(rng.choice(_SYL) for _ in range(n))


@dataclass
class Identity:
    given: str
    family: str
    patient_id: str
    birth_date: str       # YYYYMMDD
    exam_date: str        # YYYYMMDD
    exam_time: str        # HHMMSS
    sex: str
    clinic: str
    dentist_given: str
    dentist_family: str
    phone: str
    address: str
    accession: str

    @property
    def dicom_name(self) -> str:
        return f"{self.family}^{self.given}"

    @property
    def display_name(self) -> str:
        return f"{self.family.upper()}, {self.given}"

    @property
    def dentist(self) -> str:
        return f"Dr. {self.dentist_given} {self.dentist_family}"

    def age_at_exam(self) -> int:
        b = _dt.datetime.strptime(self.birth_date, "%Y%m%d").date()
        e = _dt.datetime.strptime(self.exam_date, "%Y%m%d").date()
        return e.year - b.year - ((e.month, e.day) < (b.month, b.day))

    def phi_strings(self) -> list[str]:
        """Every identifying string inserted anywhere (used by the benchmark's residual scan)."""
        b = self.birth_date
        return [self.given, self.family, self.patient_id, f"{b[6:8]}.{b[4:6]}.{b[0:4]}",
                f"{b[0:4]}-{b[4:6]}-{b[6:8]}", self.dentist_family, self.phone, self.accession]

    def as_dict(self) -> dict:
        return asdict(self)


def make_identity(rng: np.random.Generator) -> Identity:
    given = _word(rng, int(rng.integers(1, 3))).capitalize() + str(rng.choice(_GIVEN_END))
    family = _word(rng, int(rng.integers(2, 4))).capitalize() + str(rng.choice(_FAMILY_END))
    birth = _dt.date(1935, 1, 1) + _dt.timedelta(days=int(rng.integers(0, 365 * 83)))
    exam = _dt.date(2019, 1, 1) + _dt.timedelta(days=int(rng.integers(0, 365 * 7)))
    if birth >= exam:
        birth = exam - _dt.timedelta(days=365 * 8)
    t = f"{int(rng.integers(8, 19)):02d}{int(rng.integers(0, 60)):02d}{int(rng.integers(0, 60)):02d}"
    dg = _word(rng, 2).capitalize() + str(rng.choice(_GIVEN_END))
    df = _word(rng, 2).capitalize() + str(rng.choice(_FAMILY_END))
    clinic = f"{_word(rng, 2).capitalize()} {rng.choice(_CLINIC)}"
    phone = f"+{int(rng.integers(30, 99))} {int(rng.integers(200, 999))} {int(rng.integers(100, 999))} " \
            f"{int(rng.integers(10, 99))} {int(rng.integers(10, 99))}"
    address = f"{rng.choice(_STREETS)} {int(rng.integers(1, 200))}, {rng.choice(_CITIES)}"
    return Identity(
        given=given, family=family,
        patient_id=str(int(rng.integers(100000, 99999999))),
        birth_date=birth.strftime("%Y%m%d"), exam_date=exam.strftime("%Y%m%d"), exam_time=t,
        sex=str(rng.choice(["F", "M"])), clinic=clinic, dentist_given=dg, dentist_family=df,
        phone=phone, address=address, accession="A" + str(int(rng.integers(1000000, 9999999))),
    )
