from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from neurobridge.sync import fit_clock, read_pulses


def test_offset_and_negative_drift_with_jitter() -> None:
    time = np.arange(10) * 3.0 + 10
    jitter = 0.0001 * np.sin(time)
    neural = (time - 3.0) * 0.9999 + jitter
    ids = [f"p{i}" for i in range(len(time))]
    fit = fit_clock(dict(zip(ids, time, strict=True)), dict(zip(ids, neural, strict=True)))
    assert fit.drift_ppm == pytest.approx(-100, abs=2)
    assert fit.rms_inlier_s < 0.0001
    np.testing.assert_allclose(
        fit.align(np.array([13.5, 20])), (np.array([13.5, 20]) - 3) * 0.9999, atol=0.0001, rtol=0
    )
    with pytest.raises(ValueError, match="beyond inlier"):
        fit.align(np.array([1.0, 30.0]))


@pytest.mark.parametrize(
    ("neural", "message"),
    [
        ({str(i): float(i) for i in range(3)}, "uniquely matched"),
        ({str(i): i * 1.01 for i in range(8)}, "Clock drift"),
        ({str(i): float(i) for i in range(8)} | {"3": 8}, "strictly increasing"),
        ({str(i): float(i) + (0.2 if i % 2 else 0) for i in range(8)}, "affine consensus"),
    ],
)
def test_reject_invalid_clock_fit(neural: dict[str, float], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        fit_clock({str(i): float(i) for i in range(8)}, neural)


@pytest.mark.parametrize(
    ("rows", "message"),
    [
        ("a,0\na,1\n", "duplicate pulse"),
        ("a,0\nb,nan\n", "finite"),
        ("a,2\nb,1\n", "strictly increasing"),
        ("a,0,1\n", "overlong row"),
    ],
)
def test_reject_bad_pulse_csv(tmp_path: Path, rows: str, message: str) -> None:
    csv = tmp_path / "pulses.csv"
    csv.write_text("pulse_id,time_s\n" + rows, encoding="utf-8")
    with pytest.raises(ValueError, match=message):
        read_pulses(csv)
