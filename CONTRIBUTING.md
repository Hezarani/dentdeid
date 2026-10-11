# Contributing to DentDeID

Thank you for helping make dental data safer to share.

## Golden rule

**Never put real patient data in an issue, pull request, test or example** — not even "anonymised"
images. Use the synthetic generator (`dentdeid synth`) or describe the layout in words. If you found a
leak in real data, see [SECURITY.md](SECURITY.md).

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[test,all]"
python -m pytest -q            # ~170 tests, about a minute
```

The JavaScript detector (`docs/detector.js`, and the 16-bit mapping in `docs/png16.js`) must return
**identical** boxes, image classes and pixels to `dentdeid/imaging/textdetect.py` and
`dentdeid/imaging/raster.py`; `tests/test_parity_cli_bench.py` and `tests/test_web_parity.py` check
this when Node.js is installed. Change both sides together.

Found a way to make DentDeID leak? Add a failing synthetic case to `tests/test_audit_regressions.py`
first, then fix it.

## Useful contributions

- **Vendor layouts.** Describe where your imaging software burns in text (vendor, modality, position,
  font size) — or contribute a synthetic layout to `dentdeid/synth/overlay.py`.
- **Safe private attributes.** Vendor private tags that are demonstrably non-identifying (with
  documentation) for a future *Retain Safe Private* option.
- **Real-world validation.** Studies measuring recall on real, ethically approved dental datasets are
  the most valuable contribution of all — please share results (not data).
- **Translations** of the web app and PHI patterns (labels such as *Hasta Adı*, *Geb.*).

## Standards updates

The PS3.15 rule table is generated, never hand-edited:

```bash
python scripts/build_ps315_table.py         # downloads the current edition
```

Then update `EDITION` in `dentdeid/dicom/ps315.py` and re-run the tests.
