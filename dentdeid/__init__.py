"""DentDeID — open, tested de-identification for dental images.

Dental radiographs, CBCT and clinical photographs leak identity through DICOM headers,
burned-in text, EXIF/GPS metadata, file names and faces. DentDeID removes these following
DICOM PS3.15 (2026d) and records exactly what it did — without ever writing patient
identifiers into its own reports.

Quick start::

    import dentdeid
    report = dentdeid.deidentify(["exports/"], "deidentified/", profile="share")
    print(report.summary())            # {'PASS': 12, 'REVIEW': 1}

A de-identified dental image is **not** anonymous: the dentition itself is a biometric
identifier. Always review outputs and share them under consent or a data-use agreement.
"""
from ._version import __version__
from .config import PROFILE_HELP, PROFILES, Config, get_profile
from .dicom.meta import MetaConfig, deidentify_dataset, harvest_identifiers
from .imaging.textdetect import DetectParams, Region, detect_text
from .keys import Pseudonymizer, load_or_create_key
from .phi import KnownIdentifiers, find_phi, scrub_text
from .pipeline import DeIdentifier, deidentify
from .report import RESIDUAL_RISK, BatchReport, FileReport

__all__ = [
    "__version__", "deidentify", "DeIdentifier", "Config", "MetaConfig", "PROFILES", "PROFILE_HELP",
    "get_profile", "detect_text", "DetectParams", "Region", "deidentify_dataset", "harvest_identifiers",
    "KnownIdentifiers", "find_phi", "scrub_text", "Pseudonymizer", "load_or_create_key", "BatchReport",
    "FileReport", "RESIDUAL_RISK",
]
