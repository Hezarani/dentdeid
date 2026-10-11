"""Command-line interface: ``dentdeid run | inspect | synth | bench | profiles``."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from ._version import __version__
from .config import PROFILE_HELP, PROFILES, get_profile
from .dicom import ps315
from .dicom.cbct import CBCT_MODES
from .imaging.faces import FACE_ACTIONS

_OPTION_NAMES = sorted(ps315.SUPPORTED_TABLE_OPTIONS)


def _build_config(a):
    cfg = get_profile(a.profile)
    kw = {}
    for name in ("text_policy", "faces", "cbct", "ocr", "output_format"):
        v = getattr(a, name, None)
        if v is not None:
            kw[name] = v
    if a.keep_scales is not None:
        kw["keep_scales"] = a.keep_scales
    if a.text_in_photos:
        kw["text_in_photos"] = True
    if a.no_rename:
        kw["rename"] = False
    if a.no_previews:
        kw["previews"] = False
    if a.lenient:
        kw["strict"] = False
    if a.write_mapping:
        kw["write_mapping"] = True
    known = list(a.known or [])
    if a.known_file:
        known += [ln.strip() for ln in Path(a.known_file).read_text("utf-8").splitlines() if ln.strip()]
    if known:
        kw["known"] = tuple(known)
    meta = cfg.meta
    if a.option or a.allowlist is not None:
        from dataclasses import replace
        opts = set(meta.options) | set(a.option or [])
        meta = replace(meta, options=frozenset(opts),
                       allowlist=meta.allowlist if a.allowlist is None else a.allowlist)
        kw["meta"] = meta
    return cfg.with_(**kw) if kw else cfg


def cmd_run(a) -> int:
    from .pipeline import DeIdentifier
    cfg = _build_config(a)
    if cfg.profile == "research" and not a.key_file:
        print("note: the research profile is meant to be used with --key-file so pseudonyms stay "
              "consistent across runs; using a one-time key for now.", file=sys.stderr)
    try:
        deid = DeIdentifier(cfg, key_file=a.key_file)
        rep = deid.run(a.inputs, a.out)
    except (ValueError, RuntimeError, OSError) as e:
        print(f"dentdeid: error: {e}", file=sys.stderr)
        return 2
    s = rep.summary()
    print(f"DentDeID {__version__} — profile '{cfg.profile}' — {len(rep.files)} input(s)")
    for k in ("PASS", "REVIEW", "FAIL", "ERROR", "SKIPPED"):
        if k in s:
            print(f"  {k:<7} {s[k]}")
    print(f"Clean files:  {Path(a.out) / 'clean'}")
    print(f"Report:       {Path(a.out) / 'dentdeid-report.md'}")
    if s.get("FAIL"):
        print(f"Quarantined:  {Path(a.out) / 'quarantine'}  (NOT safe to share)")
    print("Reminder: look at every image in review/ before sharing. De-identified is not anonymous.")
    return 2 if s.get("FAIL") or s.get("ERROR") else 0


def cmd_inspect(a) -> int:
    """Read-only audit: what identifying content does each file carry? Nothing is written."""
    import pydicom

    from .dicom.meta import harvest_identifiers
    from .imaging import faces, raster
    from .imaging.textdetect import detect_text, is_grayscale_like, to_gray
    from .phi import KnownIdentifiers, find_phi
    from .pipeline import discover, is_dicom
    rows = []
    for p in discover(a.inputs):
        row = {"file_index": len(rows) + 1}
        try:
            if is_dicom(p):
                ds = pydicom.dcmread(p, force=True)
                known = harvest_identifiers(ds, KnownIdentifiers())
                priv = sum(1 for e in ds.iterall() if e.tag.is_private)
                row.update({"type": "dicom", "modality": str(ds.get("Modality", "")),
                            "identifier_forms_in_header": len(known), "identifier_kinds": known.kinds(),
                            "private_attributes": priv,
                            "overlay_planes": len({e.tag.group for e in ds if 0x6000 <= e.tag.group <= 0x601E}),
                            "burned_in_annotation_flag": str(ds.get("BurnedInAnnotation", "not set"))})
                try:
                    from .dicom.pixels import read_pixels, to_display8
                    pd = read_pixels(ds)
                    if pd is not None:
                        fr = pd.array if pd.frames <= 1 else pd.array[0]
                        row["burned_in_text_regions"] = len(detect_text(to_display8(fr, pd, ds)))
                except Exception as e:  # pragma: no cover
                    row["pixels"] = f"not decodable ({type(e).__name__})"
            elif p.suffix.lower() in raster.RASTER_SUFFIXES:
                arr, inv, texts = raster.load(p)
                disp = raster.display8(arr)
                photo = not is_grayscale_like(to_gray(disp)[1])
                row.update({"type": "image", "kind": "photo" if photo else "radiograph",
                            "metadata_items": inv["total_items"], "gps": inv["gps"],
                            "identifier_like_metadata": sorted({f.kind for t in texts for f in find_phi(t)}),
                            "filename_identifier_like": sorted({f.kind for f in find_phi(p.stem.replace('_', ' '))})})
                if not photo:
                    row["burned_in_text_regions"] = len(detect_text(disp))
                elif faces.available():
                    rgb = disp if disp.ndim == 3 else disp
                    row["faces"] = len(faces.detect_faces(rgb))
            else:
                row["type"] = "skipped"
        except Exception as e:
            row["error"] = type(e).__name__
        rows.append(row)
    if a.json:
        print(json.dumps(rows, indent=1))
    else:
        for r in rows:
            print(" · ".join(f"{k}={v}" for k, v in r.items()))
        print("(inspect never prints identifier values; run `dentdeid run` to clean the files)")
    return 0


def cmd_synth(a) -> int:
    from .synth.corpus import build
    m = build(a.out, n=a.n, seed=a.seed, include_cbct=not a.no_cbct)
    print(f"DentDeID-Synth: {m['n_items']} items (+{m['n_volumes']} CBCT volume) written to {a.out}")
    return 0


def cmd_bench(a) -> int:
    from .bench import run
    cfg = get_profile(a.profile)
    if a.ocr:
        cfg = cfg.with_(ocr=a.ocr)
    r = run(a.corpus, a.out, cfg)
    print((Path(a.out) / "BENCHMARK.md").read_text("utf-8"))
    return 0 if r["metadata"]["files_with_residual_phi"] == 0 else 1


def cmd_profiles(_a) -> int:
    for name, cfg in PROFILES.items():
        print(f"{name}\n  {PROFILE_HELP[name]}\n  PS3.15 options: {', '.join(sorted(cfg.meta.options)) or 'none'}"
              f"\n  allow-list: {cfg.meta.allowlist} · text: {cfg.text_policy} · faces: {cfg.faces} · cbct: {cfg.cbct}\n")
    src = ps315.source_info()
    print(f"Rule table: DICOM PS3.15 {src['edition']} Table E.1-1 ({ps315.rule_count()} rules) and "
          f"Table E.3.4-1 ({ps315.content_rule_count()} content items), source sha256 "
          f"{src['sha256_of_source_xml'][:16]}…")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dentdeid", description="De-identify dental images (DICOM, exports, photos, CBCT).")
    ap.add_argument("--version", action="version", version=f"dentdeid {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="de-identify files/folders into an output folder")
    r.add_argument("inputs", nargs="+")
    r.add_argument("-o", "--out", required=True, help="output folder (clean/, review/, quarantine/, report)")
    r.add_argument("-p", "--profile", default="share", choices=sorted(PROFILES))
    r.add_argument("--key-file", help="secret key file for consistent pseudonyms (created if missing)")
    r.add_argument("--known", action="append", help="an identifier to search for everywhere (repeatable)")
    r.add_argument("--known-file", help="text file with one identifier per line")
    r.add_argument("--text", dest="text_policy", choices=["redact-all", "phi-only", "off"])
    r.add_argument("--keep-scales", dest="keep_scales", action="store_true", default=None)
    r.add_argument("--no-keep-scales", dest="keep_scales", action="store_false")
    r.add_argument("--text-in-photos", action="store_true",
                   help="also black out text-like areas found in colour photographs (off by default: natural "
                        "texture looks like text, so they are only outlined for review)")
    r.add_argument("--faces", choices=list(FACE_ACTIONS))
    r.add_argument("--cbct", choices=list(CBCT_MODES), help="CBCT face handling (strip/shell are EXPERIMENTAL)")
    r.add_argument("--ocr", choices=["auto", "on", "off"])
    r.add_argument("--format", dest="output_format", choices=["same", "png", "jpeg"])
    r.add_argument("--option", action="append", choices=_OPTION_NAMES, help="extra PS3.15 option (repeatable)")
    r.add_argument("--allowlist", dest="allowlist", action="store_true", default=None)
    r.add_argument("--no-allowlist", dest="allowlist", action="store_false")
    r.add_argument("--no-rename", action="store_true")
    r.add_argument("--no-previews", action="store_true")
    r.add_argument("--write-mapping", action="store_true", help="write KEEP-PRIVATE/mapping.csv (re-identifying!)")
    r.add_argument("--lenient", action="store_true", help="REVIEW instead of FAIL when a check cannot run")
    r.set_defaults(func=cmd_run)

    i = sub.add_parser("inspect", help="read-only audit of identifying content (writes nothing)")
    i.add_argument("inputs", nargs="+")
    i.add_argument("--json", action="store_true")
    i.set_defaults(func=cmd_inspect)

    s = sub.add_parser("synth", help="generate the DentDeID-Synth test corpus")
    s.add_argument("-o", "--out", required=True)
    s.add_argument("-n", type=int, default=24)
    s.add_argument("--seed", type=int, default=2026)
    s.add_argument("--no-cbct", action="store_true")
    s.set_defaults(func=cmd_synth)

    b = sub.add_parser("bench", help="score DentDeID on a DentDeID-Synth corpus")
    b.add_argument("corpus")
    b.add_argument("-o", "--out", required=True)
    b.add_argument("-p", "--profile", default="share", choices=sorted(PROFILES))
    b.add_argument("--ocr", choices=["auto", "on", "off"])
    b.set_defaults(func=cmd_bench)

    pr = sub.add_parser("profiles", help="list profiles and the PS3.15 rule table in use")
    pr.set_defaults(func=cmd_profiles)

    a = ap.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
