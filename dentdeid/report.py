"""Audit reports. **Reports never contain PHI**: no original values, names or file names —
only counts, kinds, boxes, check results and pseudonymous references."""
from __future__ import annotations

import datetime as _dt
import json
from dataclasses import dataclass, field
from pathlib import Path

from ._version import __version__

STATUS_ORDER = {"PASS": 0, "REVIEW": 1, "FAIL": 2, "ERROR": 3, "SKIPPED": -1}

RESIDUAL_RISK = [
    "Dental radiographs and CBCT show the dentition, which is used for forensic identification "
    "(2026 deep-learning studies ranked the right person first in 65-75% of searches of research "
    "galleries of about 200 people). Removing names does not make an image anonymous.",
    "Outputs are de-identified (direct identifiers removed) and, with a kept key, pseudonymised — "
    "under the GDPR/KVKK they may still be personal data. Share under consent or a data-use agreement.",
    "Automatic detection is not perfect. A person must look at every image (see the review/ "
    "previews) before it leaves your organisation.",
]


@dataclass
class Check:
    name: str
    result: str            # pass | review | fail | skipped
    detail: str = ""

    def as_dict(self) -> dict:
        return {"name": self.name, "result": self.result, "detail": self.detail}


@dataclass
class FileReport:
    index: int
    source_ref: str                 # keyed token of the original path — not the path itself
    source_type: str                # dicom | raster | unknown
    kind: str = ""                  # modality / image kind
    output: str | None = None       # path relative to the output folder
    status: str = "PASS"
    actions: dict = field(default_factory=dict)
    checks: list[Check] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, name: str, result: str, detail: str = "") -> None:
        self.checks.append(Check(name, result, detail))

    def finalize(self) -> str:
        results = {c.result for c in self.checks}
        if self.status in ("ERROR", "SKIPPED"):
            return self.status
        if "fail" in results:
            self.status = "FAIL"
        elif "review" in results:
            self.status = "REVIEW"
        else:
            self.status = "PASS"
        return self.status

    def as_dict(self) -> dict:
        return {
            "index": self.index, "source_ref": self.source_ref, "source_type": self.source_type,
            "kind": self.kind, "output": self.output, "status": self.status, "actions": self.actions,
            "checks": [c.as_dict() for c in self.checks], "notes": self.notes,
        }


@dataclass
class BatchReport:
    profile: str
    settings: dict
    key_fingerprint: str
    key_persistent: bool
    files: list[FileReport] = field(default_factory=list)
    # in-memory only (never serialised — it holds original paths): source path -> FileReport
    sources: dict = field(default_factory=dict, repr=False)
    created: str = field(default_factory=lambda: _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"))

    def summary(self) -> dict:
        c: dict[str, int] = {}
        for f in self.files:
            c[f.status] = c.get(f.status, 0) + 1
        return c

    def as_dict(self) -> dict:
        return {
            "tool": f"DentDeID {__version__}",
            "created_utc": self.created,
            "profile": self.profile,
            "settings": self.settings,
            "key": {"fingerprint": self.key_fingerprint, "persistent": self.key_persistent},
            "summary": self.summary(),
            "residual_risk": RESIDUAL_RISK,
            "files": [f.as_dict() for f in self.files],
        }

    def write(self, out_dir: str | Path) -> tuple[Path, Path]:
        out = Path(out_dir)
        j = out / "dentdeid-report.json"
        m = out / "dentdeid-report.md"
        j.write_text(json.dumps(self.as_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        m.write_text(self.markdown(), encoding="utf-8")
        return j, m

    def markdown(self) -> str:
        s = self.summary()
        lines = [
            "# DentDeID report", "",
            f"- Tool: DentDeID {__version__}",
            f"- Created (UTC): {self.created}",
            f"- Profile: **{self.profile}**",
            f"- Key: {'persistent key file' if self.key_persistent else 'one-time key (discarded after this run)'}"
            f" — fingerprint `{self.key_fingerprint}`",
            "- Result: " + ", ".join(f"**{k}** {v}" for k, v in sorted(s.items(), key=lambda kv: STATUS_ORDER.get(kv[0], 9))),
            "",
            "| # | Type | Kind | Status | Output | Checks needing attention |",
            "|---|---|---|---|---|---|",
        ]
        for f in self.files:
            attn = "; ".join(f"{c.name}: {c.detail}" if c.detail else c.name
                             for c in f.checks if c.result in ("review", "fail"))
            lines.append(f"| {f.index} | {f.source_type} | {f.kind} | {f.status} | {f.output or '—'} | {attn or '—'} |")
        lines += ["", "## Meaning of the status", "",
                  "- **PASS** — every automated check passed. A person should still glance at the preview.",
                  "- **REVIEW** — de-identified, but something needs a human look (listed above).",
                  "- **FAIL** — not safe to share. The file was written to `quarantine/`, not `clean/`.",
                  "", "## Residual risk (always applies)", ""]
        lines += [f"- {r}" for r in RESIDUAL_RISK]
        lines += ["", "_This report contains no patient identifiers._", ""]
        return "\n".join(lines)
