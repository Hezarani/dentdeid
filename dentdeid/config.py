"""Named profiles and the run configuration."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .dicom.cbct import CBCT_MODES
from .dicom.meta import MetaConfig
from .imaging.faces import FACE_ACTIONS
from .imaging.textdetect import DetectParams

TEXT_POLICIES = ("redact-all", "phi-only", "off")
OCR_MODES = ("auto", "on", "off")
OUTPUT_FORMATS = ("same", "png", "jpeg")


@dataclass(frozen=True)
class Config:
    profile: str = "share"
    meta: MetaConfig = field(default_factory=MetaConfig)
    text_policy: str = "redact-all"     # burned-in text
    keep_scales: bool = False           # keep calibration rulers/scales
    text_in_photos: bool = False        # also scan colour photographs for burned-in text
    faces: str = "blur"                 # extra-oral photographs
    cbct: str = "flag"                  # CBCT/CT volumes
    shell_mm: float = 10.0
    ocr: str = "auto"
    output_format: str = "same"
    rename: bool = True                 # replace file names with pseudonymous names
    strict: bool = True                 # fail closed: a check that cannot run quarantines the file
    previews: bool = True               # review/ PNGs with the redactions outlined
    write_mapping: bool = False         # KEEP-PRIVATE/mapping.csv (original → new names)
    detect: DetectParams = field(default_factory=DetectParams)
    known: tuple[str, ...] = ()         # identifiers supplied by the user

    def __post_init__(self):
        if self.text_policy not in TEXT_POLICIES:
            raise ValueError(f"text_policy must be one of {TEXT_POLICIES}")
        if self.faces not in FACE_ACTIONS:
            raise ValueError(f"faces must be one of {FACE_ACTIONS}")
        if self.cbct not in CBCT_MODES:
            raise ValueError(f"cbct must be one of {CBCT_MODES}")
        if self.ocr not in OCR_MODES:
            raise ValueError(f"ocr must be one of {OCR_MODES}")
        if self.output_format not in OUTPUT_FORMATS:
            raise ValueError(f"output_format must be one of {OUTPUT_FORMATS}")
        if self.text_policy == "phi-only" and self.ocr == "off":
            raise ValueError("the phi-only text policy needs OCR (set ocr='auto' or 'on')")

    def with_(self, **kw) -> "Config":
        return replace(self, **kw)


PROFILES: dict[str, Config] = {
    # A clinician sharing a case (colleague, forum, publication, social media): remove as much
    # as possible; no key is kept, so the mapping cannot be reproduced.
    "share": Config(
        profile="share",
        meta=MetaConfig(options=frozenset({"clean_descriptors"}), allowlist=True),
        text_policy="redact-all", keep_scales=False, faces="blur", cbct="flag",
    ),
    # Building a research dataset: pseudonymised with a persistent key, ages/sex kept,
    # dates shifted consistently per patient, rulers kept for calibration.
    "research": Config(
        profile="research",
        meta=MetaConfig(options=frozenset({"retain_patient_characteristics",
                                           "retain_longitudinal_modified_dates",
                                           "clean_descriptors", "clean_structured_content"})),
        text_policy="redact-all", keep_scales=True, faces="blur", cbct="flag", ocr="off",
    ),
    # Plain DICOM PS3.15 Basic Profile + Clean Pixel Data, no options.
    "basic": Config(
        profile="basic",
        meta=MetaConfig(options=frozenset()),
        text_policy="redact-all", keep_scales=False, faces="fill", cbct="flag",
    ),
}

PROFILE_HELP = {
    "share": "Sharing a case with a colleague, a forum or a journal: maximum removal, dental "
             "allow-list, no re-identification key kept.",
    "research": "Building a dataset: keyed pseudonyms (use --key-file), age/sex kept (age >= 90 "
                "capped), dates shifted per patient, descriptors cleaned, rulers kept. OCR off for speed "
                "(add --ocr on for an extra check).",
    "basic": "DICOM PS3.15 Basic Application Level Confidentiality Profile + Clean Pixel Data, "
             "no options.",
}


def get_profile(name: str) -> Config:
    try:
        return PROFILES[name]
    except KeyError:
        raise ValueError(f"unknown profile {name!r}; choose from {sorted(PROFILES)}") from None
