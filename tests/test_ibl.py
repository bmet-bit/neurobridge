"""Replay the verified public-source demo with an optional local byte cache."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.real
def test_real_ibl_clock_alignment_and_dandi_draft(tmp_path: Path) -> None:
    cache = os.environ.get("NEUROBRIDGE_IBL_CACHE")
    if cache is None:
        pytest.skip("Set NEUROBRIDGE_IBL_CACHE to the pinned IBL source cache")
    source_manifest = Path(__file__).parents[1] / "examples" / "ibl_sources.json"
    for source in json.loads(source_manifest.read_text()):
        assert (Path(cache) / source).is_file(), f"Missing cached asset {source}"
    output = tmp_path / "ibl"
    script = source_manifest.with_name("ibl_demo.py")
    completed = subprocess.run(
        [sys.executable, str(script), str(output), "--cache", cache],
        check=False,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    checks = json.loads((output / "original-checks.json").read_text())
    assert checks["raw_voltage_values_checked"] == 320_000
    assert checks["raw_wheel_samples_checked"] == 16_285
    assert checks["max_error_v"] < 1e-12
    assert checks["fit_pulses"] == 12
    assert checks["held_out_pulses"] == 4
    assert checks["held_out_max_abs_error_ms"] < checks["held_out_limit_ms"]
    qc = json.loads((output / "qc.json").read_text())
    assert qc["passed"] and not qc["allow_missing_age"] and qc["schema_errors"] == []
    archive = json.loads((output / "dandi.json").read_text())
    assert archive["passed"] and archive["dandi_findings"] == archive["license_errors"] == []
    assert len(list((output / "draft").glob("sub-*/*_behavior+ecephys.nwb"))) == 1
