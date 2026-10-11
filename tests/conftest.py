import copy
import json
from pathlib import Path

import numpy as np
import pytest

from dentdeid.synth import names, overlay, render
from dentdeid.synth.corpus import _dicom, build


@pytest.fixture(scope="session")
def corpus(tmp_path_factory) -> Path:
    """A small DentDeID-Synth corpus (every kind at least once + one CBCT phantom)."""
    out = tmp_path_factory.mktemp("corpus")
    build(out, n=8, seed=7, include_cbct=True)
    return out


@pytest.fixture(scope="session")
def keys(corpus) -> dict:
    return {p.stem: json.loads(p.read_text("utf-8")) for p in sorted((corpus / "answer_keys").glob("*.json"))}


@pytest.fixture()
def ident():
    return names.make_identity(np.random.default_rng(11))


@pytest.fixture()
def dicom_ds(ident):
    rng = np.random.default_rng(3)
    r = overlay.apply_styles(render.periapical(rng), ident, rng, ["corner_block"])
    return _dicom(r.image, ident, "IO", rng)


@pytest.fixture()
def fresh(dicom_ds):
    return lambda: copy.deepcopy(dicom_ds)
