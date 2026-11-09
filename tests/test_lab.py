from __future__ import annotations

import csv
from pathlib import Path

import h5py
import numpy as np
import pytest
from pynwb import NWBHDF5IO

from neurobridge.cli import main
from neurobridge.convert import convert
from neurobridge.fixtures import make_fixture
from neurobridge.session import Session, load_session
from neurobridge.validation import validate


@pytest.fixture
def lab_session(tmp_path: Path) -> Session:
    return load_session(make_fixture(tmp_path / "lab", frames=2003, channels=3))


def test_lab_round_trip(lab_session: Session, tmp_path: Path) -> None:
    nwb = tmp_path / "lab.nwb"
    convert(lab_session, nwb, buffer_frames=31)
    result = validate(lab_session, nwb)
    assert result.passed, result.to_dict()
    fit = result.clock_fit
    assert fit is not None
    assert fit.drift_ppm == pytest.approx(175.0, abs=1e-6)
    assert fit.behavior_only == ("behavior-only",)
    assert fit.neural_only == ("neural-only",)
    rejected = [pulse for pulse in fit.pulses if not pulse.inlier]
    assert [pulse.pulse_id for pulse in rejected] == ["pulse-04"]
    assert rejected[0].residual_s == pytest.approx(0.05, abs=1e-12)

    assert lab_session.behavior is not None
    with lab_session.behavior.open(newline="") as stream:
        source = np.array(
            [[float(value) for value in row.values()] for row in csv.DictReader(stream)]
        )
    with NWBHDF5IO(nwb, "r") as io, h5py.File(lab_session.recording) as original:
        file = io.read()
        series = file.acquisition["NeuralVoltage"]
        np.testing.assert_array_equal(series.data[:], original["voltage"][:])
        assert series.data.chunks == (31, 3)
        assert series.starting_time == 0.25
        position = file.processing["behavior"]["Position"]["AlignedPosition"]
        # Analytic fixture truth. This does not call the production fitter or mapper.
        np.testing.assert_allclose(
            position.timestamps[:], source[:, 0] * 1.000175 - 0.125, atol=1e-12, rtol=0
        )
        np.testing.assert_array_equal(position.data[:], source[:, 1:])
        assert not np.allclose(np.diff(position.timestamps[:]), np.diff(position.timestamps[:])[0])


@pytest.mark.parametrize(
    ("dataset", "index", "value", "check"),
    [
        (
            "/processing/behavior/Position/AlignedPosition/timestamps",
            17,
            0.39,
            "behavior time axes",
        ),
        ("/processing/behavior/Position/AlignedPosition/data", (17, 0), 0.0, "position"),
        ("/processing/behavior/BehaviorClock/data", 17, 0.39, "behavior time axes"),
        ("/scratch/neurobridge_clock_fit", (), "{}", "clock diagnostics"),
    ],
)
def test_behavior_corruption(
    lab_session: Session,
    tmp_path: Path,
    dataset: str,
    index: int | tuple,
    value: float | str,
    check: str,
) -> None:
    nwb = tmp_path / "corrupt.nwb"
    convert(lab_session, nwb)
    with h5py.File(nwb, "r+") as file:
        file[dataset][index] = value
    result = validate(lab_session, nwb)
    assert result.schema_errors == []
    assert not result.passed
    assert any(finding.check == check and not finding.passed for finding in result.science)


def test_failed_chunk_does_not_publish_output(lab_session: Session, tmp_path: Path) -> None:
    with h5py.File(lab_session.recording, "r+") as file:
        data = file["voltage"][:].astype(np.float32)
        attrs = dict(file["voltage"].attrs)
        del file["voltage"]
        data[-1, -1] = float("nan")
        file.create_dataset("voltage", data=data).attrs.update(attrs)
    nwb = tmp_path / "incomplete.nwb"
    with pytest.raises(ValueError, match="Nonfinite voltage"):
        convert(lab_session, nwb, buffer_frames=200)
    assert not nwb.exists()
    assert not list(tmp_path.glob(".neurobridge-*"))


def test_lab_cli(tmp_path: Path) -> None:
    folder = tmp_path / "fixture"
    assert main(["make-fixture", str(folder)]) == 0
    session = str(folder / "session.json")
    assert main(["inspect", session]) == 0
    nwb, html = tmp_path / "lab.nwb", tmp_path / "lab.html"
    assert main(["convert", session, str(nwb)]) == 0
    assert main(["validate", session, str(nwb), "--html", str(html)]) == 0
    page = html.read_text(encoding="utf-8")
    assert "+175.000 ppm" in page
    assert "Outlier" in page
    assert "Synthetic fixture" in page


def test_wheel_angles_preserve_units_and_source_fingerprint(
    lab_session: Session, tmp_path: Path
) -> None:
    wheel = tmp_path / "wheel.csv"
    source_record = tmp_path / "upstream.json"
    source_record.write_text('{"version": 1}', encoding="utf-8")
    original_times = np.array([0.8, 1.1, 1.9])
    angles = np.array([-1.0, 2.0, 7.0])
    with wheel.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["time_s", "position_rad"])
        writer.writerows(zip(original_times, angles, strict=True))
    wheel_session = Session.model_validate(
        lab_session.model_dump()
        | {
            "behavior": wheel,
            "behavior_kind": "wheel",
            "source_record": source_record,
        }
    )
    nwb = tmp_path / "wheel.nwb"
    convert(wheel_session, nwb)
    assert validate(wheel_session, nwb).passed
    with NWBHDF5IO(nwb, "r") as io:
        behavior = io.read().processing["behavior"]
        wheel_series = behavior["BehavioralTimeSeries"]["AlignedWheelAngle"]
        assert wheel_series.unit == "radians"
        np.testing.assert_array_equal(wheel_series.data[:], angles)
        np.testing.assert_allclose(
            wheel_series.timestamps[:], original_times * 1.000175 - 0.125, atol=1e-12, rtol=0
        )
    source_record.write_text('{"version": 2}', encoding="utf-8")
    result = validate(wheel_session, nwb)
    assert any(finding.check == "provenance" and not finding.passed for finding in result.science)
