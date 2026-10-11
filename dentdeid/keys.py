"""Keyed pseudonymisation: consistent pseudonyms, UIDs and date offsets.

DICOM PS3.15 E.1.1 warns that *unencrypted* hashes of identifiers are vulnerable to
trivial dictionary attacks. DentDeID therefore never uses a plain hash: every pseudonym,
remapped UID and date offset is derived with HMAC-SHA-256 under a secret project key.

* With a **persistent key** (``--key-file``) the same patient always receives the same
  pseudonym, the same study keeps the same (new) Study Instance UID, and dates are shifted
  by the same per-patient offset across runs. The output is **pseudonymised** data: whoever
  holds the key (and the originals) can re-link it. Store the key separately from the data.
* With an **ephemeral key** (the default of the ``share`` profile) a random key lives only in
  memory for one run and is then discarded, so the mapping cannot be reproduced.
"""
from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import hmac
import os
import re
import secrets
import warnings
from pathlib import Path

KEY_BYTES = 32


def load_or_create_key(path: str | os.PathLike) -> bytes:
    """Read a key file, or create one (mode 0600) holding 32 random bytes as hex."""
    p = Path(path)
    if p.exists():
        if os.name == "posix" and p.stat().st_mode & 0o077:
            warnings.warn(f"key file {p.name} is readable by other users; restrict it (chmod 600)",
                          stacklevel=2)
        raw = p.read_text(encoding="ascii").strip()
        key = bytes.fromhex(raw)
        if len(key) < 16:
            raise ValueError(f"key file {p} is too short (need >= 16 bytes of hex)")
        return key
    p.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(KEY_BYTES)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as fh:
        fh.write(key.hex() + "\n")
    return key


class Pseudonymizer:
    """Derives stable, keyed replacement values."""

    def __init__(self, key: bytes | None = None, *, prefix: str = "DDID"):
        self.ephemeral = key is None
        self._key = key if key is not None else secrets.token_bytes(KEY_BYTES)
        self.prefix = prefix

    # -- core -------------------------------------------------------------------------
    def _mac(self, domain: str, value: str) -> bytes:
        msg = f"{domain}\x1f{value}".encode("utf-8")
        return hmac.new(self._key, msg, hashlib.sha256).digest()

    def fingerprint(self) -> str:
        """Short non-secret key fingerprint, useful to tell two keys apart in reports."""
        return self._mac("fingerprint", "dentdeid").hex()[:12]

    # -- pseudonyms -------------------------------------------------------------------
    def pseudo_id(self, value: str, domain: str = "patient", length: int = 10) -> str:
        """``DDID-7K2QF9M3XA``-style identifier (Crockford-ish base32, no padding)."""
        digest = self._mac(domain, (value or "").strip().upper())
        code = base64.b32encode(digest).decode("ascii").rstrip("=")[:length]
        return f"{self.prefix}-{code}"

    def token(self, value: str, domain: str = "token", length: int = 12) -> str:
        """Lower-case hex token (for file names and report cross-references)."""
        return self._mac(domain, value).hex()[:length]

    # -- UIDs -------------------------------------------------------------------------
    def uid(self, old_uid: str) -> str:
        """Map a UID to a ``2.25.<n>`` UID built from a keyed, UUIDv8-shaped 128-bit value.

        PS3.5 B.2 allows UIDs of the form ``2.25.<integer value of a UUID>``; the result is
        at most 44 characters, well under the 64-character UI limit.
        """
        b = bytearray(self._mac("uid", old_uid.strip())[:16])
        b[6] = (b[6] & 0x0F) | 0x80  # UUID version 8 (custom, RFC 9562)
        b[8] = (b[8] & 0x3F) | 0x80  # RFC 4122/9562 variant
        return "2.25." + str(int.from_bytes(bytes(b), "big"))

    # -- dates ------------------------------------------------------------------------
    def date_offset_days(self, patient_key: str, min_days: int = 30, max_days: int = 3650) -> int:
        """A per-patient backward shift in days, in ``[min_days, max_days]``."""
        n = int.from_bytes(self._mac("date-offset", patient_key or "")[:8], "big")
        return min_days + n % (max_days - min_days + 1)


def shift_da(value: str, days: int) -> str:
    """Shift a DICOM DA value (YYYYMMDD, possibly a range 'A-B') back by ``days``."""
    def one(v: str) -> str:
        v = v.strip()
        if not v:
            return v
        d = _dt.datetime.strptime(v[:8], "%Y%m%d").date() - _dt.timedelta(days=days)
        return d.strftime("%Y%m%d")
    if "-" in value:
        a, b = value.split("-", 1)
        return f"{one(a)}-{one(b)}"
    return one(value)


def shift_dt(value: str, days: int) -> str:
    """Shift a DICOM DT value (YYYY[MM[DD[HHMMSS[.F]]]][&ZZXX]) back by ``days``.

    The time of day is kept, the UTC-offset suffix (which reveals a location) is dropped, and
    values with only year or year-month precision are shifted from the start of that period
    and returned at their original precision.
    """
    v = value.strip()
    m = re.fullmatch(r"(\d{4,14}(?:\.\d{1,6})?)([+-]\d{4})?", v)
    if not m:
        raise ValueError(f"not a DICOM DT value: {v!r}")
    body = m.group(1)
    digits = body.split(".")[0]
    if len(digits) >= 8:
        return shift_da(digits[:8], days) + body[8:]
    if len(digits) not in (4, 6):
        raise ValueError(f"not a DICOM DT value: {v!r}")
    padded = digits + "0101"[len(digits) - 4:]
    return shift_da(padded, days)[: len(digits)]
