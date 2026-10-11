"""Python ↔ JavaScript detector parity, the CLI, and the benchmark."""
import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from dentdeid import cli
from dentdeid.imaging import raster
from dentdeid.imaging.textdetect import detect_text

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node.js not installed")
def test_js_detector_matches_python(keys, corpus, tmp_path):
    jobs, expected = [], []
    for i, k in enumerate(v for v in keys.values() if "raster" in v):
        arr, _, _ = raster.load(corpus / k["raster"])
        f = tmp_path / f"{i}.bin"
        f.write_bytes(np.ascontiguousarray(arr).tobytes())
        jobs.append({"file": str(f), "width": arr.shape[1], "height": arr.shape[0],
                     "channels": 1 if arr.ndim == 2 else arr.shape[2]})
        expected.append([r.as_dict() for r in detect_text(arr)])
    jf = tmp_path / "jobs.json"
    jf.write_text(json.dumps(jobs))
    got = json.loads(subprocess.check_output(["node", str(ROOT / "tests" / "_parity_runner.js"), str(jf)]))
    assert got == expected
    assert sum(len(e) for e in expected) > 5


def test_cli_profiles(capsys):
    assert cli.main(["profiles"]) == 0
    assert "2026d" in capsys.readouterr().out


def test_cli_run_and_inspect(corpus, tmp_path, capsys, keys):
    src = str(corpus / "images")
    assert cli.main(["inspect", src, "--json"]) == 0
    rows = json.loads(capsys.readouterr().out.split("\n(inspect")[0])
    assert rows and all("error" not in r for r in rows)
    blob = json.dumps(rows)
    assert not any(k["identity"]["family"] in blob for k in keys.values())
    code = cli.main(["run", src, "-o", str(tmp_path / "o"), "--ocr", "off", "--no-previews"])
    assert code == 0
    assert "PASS" in capsys.readouterr().out


def test_cli_synth(tmp_path):
    assert cli.main(["synth", "-o", str(tmp_path / "c"), "-n", "2", "--no-cbct"]) == 0
    assert len(list((tmp_path / "c" / "answer_keys").glob("*.json"))) == 2


def test_benchmark_on_corpus(corpus, tmp_path):
    from dentdeid.bench import run
    from dentdeid.config import PROFILES
    r = run(corpus, tmp_path, PROFILES["share"].with_(ocr="off", previews=False))
    assert r["metadata"]["files_with_residual_phi"] == 0
    assert r["burned_in_text"]["standard"]["recall"] == 1.0
    assert r["cbct"][0]["face_risk"] == "high"
    assert (tmp_path / "BENCHMARK.md").exists()
