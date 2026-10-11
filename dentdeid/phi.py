"""Finding protected health information (PHI) in free text.

Two complementary strategies are used, both deterministic and auditable:

1. **Known-identifier matching.** Before any cleaning, DentDeID harvests the patient's own
   identifiers from the DICOM header (name parts, IDs, birth date in many spellings, the
   clinic, the dentist) or from values the user supplies (``--known``). Those exact values
   are then searched for everywhere else: other header fields, file names, OCR'd burned-in
   text. This catches the most common real-world leak — a name typed into a comment field —
   with near-zero false positives.
2. **Pattern matching.** Dates, times, e-mail addresses, phone numbers, long ID-like numbers
   (with checksum validation for Turkish T.C. Kimlik and UK NHS numbers), US SSNs, URLs,
   IP addresses, ages over 89 (HIPAA Safe Harbor), and "label: value" pairs such as
   ``Patient: ...``, ``Hasta Adı: ...`` or ``Geb.: ...``.

Matching is case-, accent- and Turkish-dotted-i-insensitive, on word boundaries; names and
IDs are also matched with separators removed ("HansMueller", "ZX 998 877"), German umlaut
transliterations are added (Müller / Mueller), and non-Latin digits are folded to 0-9.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

__all__ = [
    "Finding", "KnownIdentifiers", "fold", "find_patterns", "find_phi", "scrub_text",
    "is_safe_burned_in_text", "date_renderings", "find_descriptor_phi", "name_like_words",
]

# Honorifics / particles that are not identifying on their own.
_STOP_TOKENS = {
    "dr", "dr.", "dt", "dt.", "mr", "mrs", "ms", "miss", "prof", "prof.", "doc", "dds", "dmd",
    "phd", "md", "bay", "bayan", "sn", "sayin", "herr", "frau", "de", "da", "van", "von", "der",
    "den", "la", "le", "del", "di", "bin", "binti", "al", "el", "und", "and", "ve", "y",
    "ddid",   # DentDeID's own pseudonym prefix (re-processing an earlier output)
}
_PSEUDONYM = re.compile(r"^DDID-[A-Z2-7]{10}$")


def fold(s: str) -> tuple[str, list[int]]:
    """Case/accent-fold ``s`` and return ``(folded, index_map)``.

    ``index_map[i]`` is the index in ``s`` of the character that produced ``folded[i]``, so
    matches found in the folded text can be mapped back onto the original. The input is
    NFC-normalised first, so decomposed (macOS) and composed spellings fold identically;
    digits of any script (e.g. Arabic-Indic) fold to ASCII 0-9.
    """
    out: list[str] = []
    idx: list[int] = []
    for i, ch in enumerate(s):
        if unicodedata.combining(ch):
            continue                                   # a stray combining mark (NFD input)
        if ch in "ıİ":
            piece = "i"
        elif ch.isdigit() and not ("0" <= ch <= "9"):
            try:
                piece = str(unicodedata.digit(ch))
            except (TypeError, ValueError):
                piece = ch
        else:
            piece = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
            piece = piece.casefold()
        for c in piece:
            out.append(c)
            idx.append(i)
    return "".join(out), idx


def _compact(folded: str, idx: list[int]) -> tuple[str, list[int]]:
    """Drop every non-alphanumeric character (keeping the index map)."""
    keep = [(c, i) for c, i in zip(folded, idx) if c.isalnum()]
    return "".join(c for c, _ in keep), [i for _, i in keep]


_TRANSLIT = (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"))


def _umlaut_variants(value: str) -> set[str]:
    """Müller → {Mueller}; Mueller → {Muller} (so both spellings are searched)."""
    out = set()
    low = value.lower()
    t = low
    for a, b in _TRANSLIT:
        t = t.replace(a, b)
    if t != low:
        out.add(t)
    if re.search(r"(?:ae|oe|ue)", low):
        out.add(re.sub(r"(?<=[a-z])(ae|oe|ue)", lambda m: m.group(1)[0], low))
    return out


def _fold_str(s: str) -> str:
    return fold(s)[0]


@dataclass(frozen=True)
class Finding:
    kind: str       # e.g. "known:patient-name", "date", "email", "tr-national-id"
    start: int      # span in the ORIGINAL text
    end: int

    def as_dict(self) -> dict:  # values are deliberately NOT included (reports must not leak PHI)
        return {"kind": self.kind, "start": self.start, "end": self.end}


# ---------------------------------------------------------------------------------------
# Date renderings — so a DOB harvested as 19870312 is also found as 12.03.1987, 3/12/87 …
# ---------------------------------------------------------------------------------------
_MONTHS = {
    1: ["january", "jan", "ocak", "januar", "janvier"],
    2: ["february", "feb", "subat", "februar", "fevrier"],
    3: ["march", "mar", "mart", "marz", "maerz", "mars"],
    4: ["april", "apr", "nisan", "avril"],
    5: ["may", "mayis", "mai"],
    6: ["june", "jun", "haziran", "juni", "juin"],
    7: ["july", "jul", "temmuz", "juli", "juillet"],
    8: ["august", "aug", "agustos", "aout"],
    9: ["september", "sep", "sept", "eylul", "septembre"],
    10: ["october", "oct", "ekim", "oktober", "okt", "octobre"],
    11: ["november", "nov", "kasim", "novembre"],
    12: ["december", "dec", "aralik", "dezember", "dez", "decembre"],
}


def date_renderings(yyyymmdd: str) -> set[str]:
    """Common human spellings of a date (all folded to lower case)."""
    s = re.sub(r"\D", "", yyyymmdd or "")
    if len(s) != 8:
        return set()
    y, m, d = s[:4], s[4:6], s[6:8]
    yy = y[2:]
    mi, di = int(m), int(d)
    if not (1 <= mi <= 12 and 1 <= di <= 31):
        return set()
    out = {s, f"{y}-{m}-{d}", f"{y}/{m}/{d}", f"{y}.{m}.{d}",
           f"{d}{m}{y}", f"{m}{d}{y}", f"{d}{m}{yy}", f"{yy}{m}{d}"}
    roman = _ROMAN[mi]
    for dd in {d, str(di)}:
        for mm in {m, str(mi)}:
            for sep in "./-":
                out.add(f"{dd}{sep}{mm}{sep}{y}")
                out.add(f"{dd}{sep}{mm}{sep}{yy}")
                out.add(f"{mm}{sep}{dd}{sep}{y}")
                out.add(f"{mm}{sep}{dd}{sep}{yy}")
        for sep in ". -/":
            out.add(f"{dd}{sep}{roman}{sep}{y}")
            out.add(f"{dd}{sep}{roman}{sep}{yy}")
    # month + year (HIPAA Safe Harbor: every date element except the year is an identifier)
    for name in _MONTHS[mi]:
        out.add(f"{name} {y}")
        out.add(f"{name} {yy}")
    for mm in {m, str(mi)}:
        out.add(f"{mm}/{y}")
        out.add(f"{mm}.{y}")
    out.add(f"{y}-{m}")
    out.add(f"{y}{m}")
    ordinal = f"{di}{_ordinal_suffix(di)}"
    for name in _MONTHS[mi]:
        out.add(f"{di} {name} {y}")
        out.add(f"{d} {name} {y}")
        out.add(f"{name} {di}, {y}")
        out.add(f"{name} {di} {y}")
        out.add(f"{ordinal} {name} {y}")
        out.add(f"{ordinal} of {name} {y}")
        out.add(f"{name} {ordinal}, {y}")
        out.add(f"{name} {ordinal} {y}")
    return out


_ROMAN = {1: "i", 2: "ii", 3: "iii", 4: "iv", 5: "v", 6: "vi", 7: "vii", 8: "viii", 9: "ix", 10: "x",
          11: "xi", 12: "xii"}


def _ordinal_suffix(n: int) -> str:
    if 10 <= n % 100 <= 20:
        return "th"
    return {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


class KnownIdentifiers:
    """A set of identifying strings harvested for one patient/study."""

    def __init__(self) -> None:
        self._terms: dict[str, str] = {}
        self._compact_terms: dict[str, str] = {}   # searched with all separators removed
        self._regex: re.Pattern | None = None
        self._cregex: re.Pattern | None = None

    def __len__(self) -> int:
        return len(self._terms)

    def __bool__(self) -> bool:
        return bool(self._terms)

    def kinds(self) -> dict[str, int]:
        c: dict[str, int] = {}
        for k in self._terms.values():
            c[k] = c.get(k, 0) + 1
        return c

    def _put(self, term: str, kind: str) -> None:
        t = re.sub(r"[\s\^,._-]+", " ", _fold_str(term)).strip()
        if len(t) < 2 or t in _STOP_TOKENS:
            return
        if t.isdigit() and len(t) < 4:  # tiny numbers are not identifying on their own
            return
        self._terms.setdefault(t, kind)
        self._regex = None

    def _put_compact(self, term: str, kind: str, min_len: int) -> None:
        c = "".join(ch for ch in _fold_str(term) if ch.isalnum())
        if len(c) >= min_len and c not in _STOP_TOKENS:
            self._compact_terms.setdefault(c, kind)
            self._cregex = None

    def add_name(self, value: str, kind: str = "name") -> None:
        """Add a person name (DICOM PN ``Family^Given^Middle^Prefix^Suffix`` or free text)."""
        if not value or _PSEUDONYM.match(str(value).strip()):
            return
        v = unicodedata.normalize("NFC", str(value))
        variants = {v} | _umlaut_variants(v)
        for vv in variants:
            for group in vv.split("="):  # PN alphabetic=ideographic=phonetic
                parts = [p for p in re.split(r"[\^,]", group) if p.strip()]
                if parts:
                    self._put(" ".join(p.strip() for p in parts), kind)
                    self._put_compact("".join(parts), kind, 6)
                    if len(parts) >= 2:
                        self._put(" ".join(p.strip() for p in reversed(parts[:2])), kind)
                        self._put_compact("".join(reversed(parts[:2])), kind, 6)
                for tok in re.split(r"[\s\^,.\-]+", group):
                    tok = tok.strip()
                    if len(tok) >= 2 and _fold_str(tok) not in _STOP_TOKENS and not tok.isdigit():
                        self._put(tok, kind)
                        self._put_compact(tok, kind, 6)   # "Y i l m a z", "Yilmazova", "HansMueller"

    def add_id(self, value: str, kind: str = "id") -> None:
        if not value or _PSEUDONYM.match(str(value).strip()):
            return
        v = str(value).strip()
        self._put(v, kind)
        digits = re.sub(r"\D", "", v)
        if len(digits) >= 4 and digits != v:
            self._put(digits, kind)
        if any(ch.isdigit() for ch in v):
            self._put_compact(v, kind, 5)                  # "ZX 998 877", "ZX-998877"

    def add_date(self, value: str, kind: str = "date") -> None:
        for r in date_renderings(str(value)):
            self._put(r, kind)

    def add_text(self, value: str, kind: str = "text") -> None:
        if value and len(str(value).strip()) >= 3:
            self._put(str(value), kind)

    def update(self, other: "KnownIdentifiers") -> None:
        for t, k in other._terms.items():
            self._terms.setdefault(t, k)
        for t, k in other._compact_terms.items():
            self._compact_terms.setdefault(t, k)
        self._regex = None
        self._cregex = None

    def _compiled(self) -> re.Pattern | None:
        if not self._terms:
            return None
        if self._regex is None:
            alts = sorted(self._terms, key=len, reverse=True)
            body = "|".join(re.escape(a).replace(r"\ ", r"[\s\^,._-]+") for a in alts)
            self._regex = re.compile(rf"(?<![0-9a-z])(?:{body})(?![0-9a-z])")
        return self._regex

    def find(self, text: str) -> list[Finding]:
        rx = self._compiled()
        if rx is None or not text:
            return []
        folded, idx = fold(text)
        out = []
        for m in rx.finditer(folded):
            kind = self._terms.get(re.sub(r"[\s\^,._-]+", " ", m.group(0)).strip(), "known")
            start = idx[m.start()]
            end = idx[m.end() - 1] + 1
            out.append(Finding(f"known:{kind}", start, end))
        if self._compact_terms:
            if self._cregex is None:
                alts = sorted(self._compact_terms, key=len, reverse=True)
                self._cregex = re.compile("|".join(re.escape(a) for a in alts))
            ctext, cidx = _compact(folded, idx)
            for m in self._cregex.finditer(ctext):
                start = cidx[m.start()]
                end = cidx[m.end() - 1] + 1
                if any(f.start <= start and end <= f.end for f in out):
                    continue
                # extend over the rest of the word (e.g. a surname with a suffix)
                while end < len(text) and text[end].isalnum():
                    end += 1
                out.append(Finding(f"known:{self._compact_terms[m.group(0)]}", start, end))
        return out


# ---------------------------------------------------------------------------------------
# Pattern PHI
# ---------------------------------------------------------------------------------------
_MONTH_ALT = "|".join(sorted({n for v in _MONTHS.values() for n in v}, key=len, reverse=True))
_P = [
    ("date", re.compile(r"(?<!\d)(?:19|20)\d{2}[-/.](?:0?[1-9]|1[0-2])[-/.](?:0?[1-9]|[12]\d|3[01])(?!\d)")),
    ("date", re.compile(r"(?<![\d.])(?<!\d[-/])(?:0?[1-9]|[12]\d|3[01])[./-](?:0?[1-9]|1[0-2])[./-](?:(?:19|20)\d{2}|\d{2})(?![\d.])(?![-/]\d)")),
    ("date", re.compile(r"(?<![\d.])(?<!\d[-/])(?:0?[1-9]|1[0-2])[./-](?:0?[1-9]|[12]\d|3[01])[./-](?:(?:19|20)\d{2}|\d{2})(?![\d.])(?![-/]\d)")),
    ("date", re.compile(r"(?<!\d)(?:19|20)\d{2}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])(?!\d)")),
    ("date", re.compile(rf"(?<![a-z])(?:\d{{1,2}}\.?\s+(?:{_MONTH_ALT})\.?,?\s+\d{{2,4}}|(?:{_MONTH_ALT})\.?\s+\d{{1,2}},?\s+\d{{4}})(?![a-z])")),
    ("time", re.compile(r"(?<![\d:])(?:[01]?\d|2[0-3]):[0-5]\d(?::[0-5]\d)?(?:\s?[ap]\.?m\.?)?(?![\d:])")),
    ("email", re.compile(r"[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}")),
    ("url", re.compile(r"(?:https?://|www\.)[^\s<>\"]+")),
    ("ip-address", re.compile(r"(?<![\d.])(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)(?![\d.])")),
    ("us-ssn", re.compile(r"(?<!\d)\d{3}-\d{2}-\d{4}(?!\d)")),
    ("phone", re.compile(r"(?<![\w+.])\+?\(?\d{1,4}\)?(?:[\s./-]?\(?\d{2,4}\)?){2,5}(?![\w.])")),
    ("age-over-89", re.compile(r"(?<!\d)(?:9\d|1[01]\d)\s?(?:y|yr|yrs|years?|ya[sş]|jahre|ans)\b")),
    ("labelled", re.compile(
        r"\b(?:patient(?:en)?(?:\s*name)?|pat|pt|name|nom|nome|hasta(?:\s*ad[ıi])?|ad[ıi]?\s*soyad[ıi]?|"
        r"soyad[ıi]?|isim|vorname|nachname|dob|d\.o\.b|birth(?:\s*date)?|do[gğ]um(?:\s*tarihi)?|"
        r"geb(?:urtsdatum|\.)?|mrn|chart(?:\s*no)?|file\s*no|dosya\s*no|protokol\s*no|kimlik(?:\s*no)?|"
        r"tc(?:\s*no)?|ssn|nhs(?:\s*no)?|tel|phone|telefon|adres|address|anschrift)\s*[:#]\s*"
        r"([^\n\r;|]{1,40})")),
]
_LONG_NUMBER = re.compile(r"(?<![\d.,])\d{6,}(?![\d.,])")


def _tckn_valid(n: str) -> bool:
    if len(n) != 11 or n[0] == "0" or not n.isdigit():
        return False
    d = [int(c) for c in n]
    if ((sum(d[0:9:2]) * 7 - sum(d[1:8:2])) % 10) != d[9]:
        return False
    return sum(d[:10]) % 10 == d[10]


def _nhs_valid(n: str) -> bool:
    n = re.sub(r"\D", "", n)
    if len(n) != 10:
        return False
    total = sum(int(n[i]) * (10 - i) for i in range(9))
    check = 11 - total % 11
    check = 0 if check == 11 else check
    return check != 10 and check == int(n[9])


_FDI = {f"{q}{t}" for q in (1, 2, 3, 4) for t in range(1, 9)} | {f"{q}{t}" for q in (5, 6, 7, 8) for t in range(1, 6)}


def _tooth_list(s: str) -> bool:
    """'11 12 13 21 22' or '11-12-13' is a list of FDI tooth numbers, not a phone number."""
    groups = re.findall(r"\d+", s)
    return len(groups) >= 3 and all(g in _FDI for g in groups)


# Descriptor-only patterns (Clean Descriptors): a title followed by a name, and age/sex tokens
# such as "55F" or "55 yo" (PS3.15 E.3.5 uses "55F Dr. Smith" as its example).
_TITLED_NAME = re.compile(
    r"(?<![\w])(?:(?:dr|dt|drs|doc|doç|prof|uzm|op|mr|mrs|ms|miss|herr|frau|sn|sayın|sayin)\.?\s*)+"
    r"(?:(?:med|dent|med\.dent)\.?\s+)?"
    r"[A-ZÇĞİÖŞÜÄÉ][\w'’-]+(?:\s+[A-ZÇĞİÖŞÜÄÉ][\w'’-]+)?", re.IGNORECASE)
_AGE_SEX = re.compile(
    r"(?<![\w.])\d{1,3}\s?(?:y|yo|y/o|yr|yrs|years?(?:[- ]old)?|ya[sş](?:[ıi]nda)?|j|jahre)?\s?"
    r"(?:m|f|w|k|e|male|female|erkek|kad[ıi]n)(?![\w])|(?<![\w.])\d{1,3}\s?(?:yo|y/o|years?[- ]old|ya[sş][ıi]nda)(?![\w])",
    re.IGNORECASE)


def find_descriptor_phi(text: str) -> list[Finding]:
    """Extra patterns applied to free-text *descriptors* (Clean Descriptors Option)."""
    if not text:
        return []
    out = []
    for m in _TITLED_NAME.finditer(text):
        # "Dr. " alone or a title followed by a lower-case word ("dr. visit") is not a name
        tail = text[m.start():m.end()].split()[-1]
        if tail[:1].isupper():
            out.append(Finding("titled-name", m.start(), m.end()))
    for m in _AGE_SEX.finditer(text):
        out.append(Finding("age-sex", m.start(), m.end()))
    return out


def find_patterns(text: str) -> list[Finding]:
    if not text:
        return []
    folded, idx = fold(text)
    found: list[Finding] = []

    def add(kind: str, a: int, b: int) -> None:
        if b > a:
            found.append(Finding(kind, idx[a], idx[b - 1] + 1))

    for kind, rx in _P:
        for m in rx.finditer(folded):
            if kind == "labelled":
                add("labelled-value", m.start(1), m.end(1))
            elif kind == "phone":
                g = m.group(0)
                digits = re.sub(r"\D", "", g)
                has_sep = bool(re.search(r"[\s()/-]|^\+", g))
                if len(digits) >= 9 and has_sep and g.count(".") <= 2 and not _tooth_list(g):
                    add("phone", m.start(), m.end())
            else:
                add(kind, m.start(), m.end())
    for m in _LONG_NUMBER.finditer(folded):
        num = m.group(0)
        if _tckn_valid(num):
            add("tr-national-id", m.start(), m.end())
        elif _nhs_valid(num):
            add("uk-nhs-number", m.start(), m.end())
        else:
            add("id-number", m.start(), m.end())
    return found


def find_phi(text: str, known: KnownIdentifiers | None = None, patterns: bool = True) -> list[Finding]:
    out: list[Finding] = []
    if known:
        out.extend(known.find(text))
    if patterns:
        out.extend(find_patterns(text))
    out.sort(key=lambda f: (f.start, -(f.end - f.start)))
    return out


def _merge_spans(findings: Iterable[Finding]) -> list[tuple[int, int]]:
    spans = sorted((f.start, f.end) for f in findings)
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


_SEPARATORS = " ,;:/|^-_\t"


def scrub_text(text: str, known: KnownIdentifiers | None = None, patterns: bool = True,
               exclude: set[str] | frozenset[str] = frozenset(),
               descriptors: bool = False) -> tuple[str, list[Finding]]:
    """Remove every PHI span from ``text`` (and the separators left dangling around it).

    Returns ``(clean_text, findings)``; ``exclude`` lists finding kinds to leave in place;
    ``descriptors=True`` adds the Clean Descriptors patterns (titled names, age/sex tokens).
    """
    if not text:
        return text, []
    findings = [f for f in find_phi(text, known, patterns) if f.kind not in exclude]
    if descriptors:
        findings += find_descriptor_phi(text)
    if not findings:
        return text, []
    pieces, pos = [], 0
    for a, b in _merge_spans(findings):
        pieces.append(text[pos:a])
        pos = b
    pieces.append(text[pos:])
    kept = [p.strip(_SEPARATORS) for p in pieces]
    clean = " ".join(p for p in kept if p)
    return re.sub(r"\s{2,}", " ", clean).strip(), findings


# ---------------------------------------------------------------------------------------
# Burned-in text that is *known to be safe* in dental radiographs (used only by the optional
# OCR "phi-only" policy; the default policy redacts all detected text).
# ---------------------------------------------------------------------------------------
_SAFE_WORDS = {
    "r", "l", "right", "left", "sag", "sol", "rechts", "links", "d", "g", "dx", "sx",
    "kv", "kvp", "ma", "mas", "ms", "s", "sec", "mm", "cm", "mgy", "ugy", "dap", "gy", "cm2",
    "pan", "ceph", "bw", "pa", "io", "cbct", "fov", "lat", "ap", "scale", "olcek", "massstab",
    "x", "mag", "zoom", "hu", "roi", "tmj", "max", "mand", "mx", "md",
}
_NUMBER_UNIT = re.compile(r"^[<>~=+-]?\d{1,4}(?:[.,]\d{1,3})?(?:x\d{1,4}(?:[.,]\d{1,3})?)?[a-zµ%²/]*$")


def is_safe_burned_in_text(text: str) -> bool:
    """True only if *every* token is a known-safe dental technical token or a short number."""
    folded = _fold_str(text).replace("µ", "u")
    tokens = [t for t in re.split(r"[\s:;,/|()\[\]=]+", folded) if t]
    if not tokens:
        return False
    for t in tokens:
        if t in _SAFE_WORDS or _NUMBER_UNIT.match(t):
            continue
        return False
    return True


# Two or more capitalised words in a row ("Ayse Yilmaz") — used to flag OCR'd text and file
# names for human review (not for automatic removal: it also matches ordinary headings).
_NAME_LIKE = re.compile(r"(?<![\w])[A-ZÇĞİÖŞÜÄÉ][a-zçğıöşüäéß]{2,}(?:[ _-]+[A-ZÇĞİÖŞÜÄÉ][a-zçğıöşüäéß]{2,})+")
_NOT_NAMES = {"panoramic", "periapical", "bitewing", "cephalometric", "right", "left", "upper", "lower",
              "image", "study", "series", "report", "dental", "clinic", "scale", "view", "axial", "coronal",
              "sagittal", "maxilla", "mandible", "tooth", "teeth", "exposure", "control", "patient"}


def name_like_words(text: str) -> bool:
    """True if ``text`` contains a run of capitalised words that could be a person's name."""
    t = unicodedata.normalize("NFC", text or "")
    t = re.sub(r"(?<=[a-zçğıöşüäéß])(?=[A-ZÇĞİÖŞÜÄÉ])", " ", t)          # "YilmazAyse" → "Yilmaz Ayse"
    for m in _NAME_LIKE.finditer(t):
        words = [w.casefold() for w in re.split(r"[ _-]+", m.group(0))]
        if not all(w in _NOT_NAMES for w in words):
            return True
    return False
