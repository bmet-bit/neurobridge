"""Validate format and compare reopened NWB against the source independently."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path

import h5py
import numpy as np
import pynwb
from numpy.typing import NDArray
from nwbinspector import inspect_nwbfile
from pyedflib import EdfReader
from pynwb import NWBHDF5IO, NWBFile
from pynwb.core import ScratchData
from pynwb.ecephys import ElectricalSeries

from .convert import ANNOTATIONS, PROVENANCE, provenance
from .edf import read_annotations, read_edf_metadata
from .lab import POSITION, RAW_CLOCK, SYNC, WHEEL, Behavior, load_behavior, read_lab_metadata
from .recording import SERIES, VOLTS_PER_UNIT, RecordingInfo
from .session import Session
from .sync import ClockFit

VOLT_ATOL = 1e-12
VOLT_RTOL = 2e-7  # NeuroConv can materialize calibrated microvolts in float32.
TIME_ATOL = 1e-9
COMPARE_FRAMES = 4096


@dataclass(frozen=True)
class Finding:
    check: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Validation:
    science: list[Finding]
    schema_errors: list[str]
    inspector: list[dict]
    allow_missing_age: bool = False
    clock_fit: ClockFit | None = None

    @property
    def passed(self) -> bool:
        blocking = {"CRITICAL", "PYNWB_VALIDATION", "ERROR"}
        return (
            all(finding.passed for finding in self.science)
            and not self.schema_errors
            and not any(
                message["importance"] in blocking
                and not (self.allow_missing_age and message["check"] == "check_subject_age")
                for message in self.inspector
            )
        )

    def to_dict(self) -> dict:
        return {"passed": self.passed, **asdict(self)}


def voltage_block(series: ElectricalSeries, start: int, stop: int) -> NDArray[np.float64]:
    data = np.asarray(series.data[start:stop], dtype=np.float64)
    gains = series.channel_conversion
    data *= series.conversion
    if gains is not None:
        data *= np.asarray(gains[:], dtype=np.float64)
    return data + series.offset


def compare_voltage(
    series: ElectricalSeries,
    info: RecordingInfo,
    start_s: float,
    source_block: Callable[[int, int], NDArray],
) -> list[Finding]:
    rows = np.asarray(series.electrodes.data[:])
    table = series.electrodes.table
    valid_region = rows.ndim == 1 and np.all((rows >= 0) & (rows < len(table)))
    labels = tuple(table["channel_name"][int(row)] for row in rows) if valid_region else ()
    channels = len(info.channels)
    shape_matches = tuple(series.data.shape) == (info.samples, channels)
    time_matches = (
        series.timestamps is None
        and series.rate is not None
        and np.isclose(series.rate, info.rate_hz, atol=0, rtol=1e-12)
        and series.starting_time_unit == "seconds"
        and np.isclose(series.starting_time, start_s, atol=TIME_ATOL, rtol=0)
        and np.isclose(
            series.starting_time + (info.samples - 1) / series.rate,
            start_s + (info.samples - 1) / info.rate_hz,
            atol=TIME_ATOL,
            rtol=0,
        )
    )
    findings = [
        Finding("shape", shape_matches, f"Expected {info.samples} × {channels}"),
        Finding("electrode order", labels == info.channels, f"{len(labels)} referenced labels"),
        Finding(
            "voltage unit",
            series.unit == series.data.attrs.get("unit") == "volts",
            f"NWB data unit: {series.data.attrs.get('unit')}",
        ),
        Finding("sample clock", bool(time_matches), f"{info.rate_hz:g} Hz from t = {start_s:g} s"),
    ]
    if not shape_matches:
        return [*findings, Finding("physical values", False, "Skipped due to shape mismatch")]
    max_error = 0.0
    values_match = True
    worst = (0, 0)
    block = min(COMPARE_FRAMES, max(1, (8 * 1024**2) // (8 * channels)))
    for start in range(0, info.samples, block):
        stop = min(start + block, info.samples)
        source = source_block(start, stop)
        delta = np.abs(source - voltage_block(series, start, stop))
        finite = np.isfinite(delta)
        values_match &= bool(np.all(finite & (delta <= VOLT_ATOL + VOLT_RTOL * np.abs(source))))
        delta[~finite] = np.inf
        index = np.unravel_index(np.argmax(delta), delta.shape)
        if delta[index] > max_error:
            max_error = float(delta[index])
            worst = (start + index[0], index[1])
    findings.append(
        Finding(
            "physical values",
            values_match,
            f"{info.samples * channels:,} values, max error {max_error:.6g} V "
            f"at sample {worst[0]}, channel {info.channels[worst[1]]} "
            f"(tolerance {VOLT_ATOL:g} V + {VOLT_RTOL:g} × |source|)",
        )
    )
    return findings


def compare_edf(nwbfile: NWBFile, series: ElectricalSeries, session: Session) -> list[Finding]:
    with EdfReader(str(session.recording)) as reader:
        info = read_edf_metadata(session.recording, reader)
        scales = np.array([VOLTS_PER_UNIT[unit] for unit in info.units])

        def source_block(start: int, stop: int) -> NDArray:
            return (
                np.column_stack(
                    [reader.readSignal(ch, start, stop - start) for ch in range(len(info.channels))]
                )
                * scales
            )

        findings = compare_voltage(series, info, 0.0, source_block)
        findings.append(
            Finding(
                "EDF wall time",
                reader.getStartdatetime() == session.start_time.replace(tzinfo=None),
                "Manifest wall time matches the EDF start header",
            )
        )
        expected = read_annotations(reader, info.samples / info.rate_hz)
        actual = nwbfile.intervals.get(ANNOTATIONS)
        count = len(actual) if actual is not None else 0
        matches = count == len(expected)
        if actual is not None and matches:
            matches = all(
                np.isclose(actual["start_time"][i], item.start_s, atol=TIME_ATOL, rtol=0)
                and np.isclose(actual["stop_time"][i], item.stop_s, atol=TIME_ATOL, rtol=0)
                and actual["label"][i] == item.label
                and actual["duration_known"][i] == item.duration_known
                for i, item in enumerate(expected)
            )
        findings.append(
            Finding("task annotations", matches, f"{count} / {len(expected)} intervals")
        )
    return findings


def check_behavior(nwbfile: NWBFile, behavior: Behavior) -> list[Finding]:
    module = nwbfile.processing.get("behavior")
    if module is None:
        return [Finding("behavior", False, "Missing behavior processing module")]
    if behavior.kind == "wheel":
        wheel_container = module.get("BehavioralTimeSeries")
        position = wheel_container.time_series.get(WHEEL) if wheel_container else None
    else:
        position_container = module.get("Position")
        position = position_container.spatial_series.get(POSITION) if position_container else None
    raw = module.get(RAW_CLOCK)
    if position is None or raw is None:
        return [Finding("behavior", False, "Missing aligned position or raw clock series")]
    values = position.data[:] * position.conversion + position.offset
    raw_times = raw.data[:] * raw.conversion + raw.offset
    aligned = np.asarray(position.timestamps[:])
    matches = (
        position.unit == behavior.unit
        and values.shape == behavior.values.shape
        and np.allclose(values, behavior.values, atol=1e-12, rtol=0)
    )
    clock_matches = (
        raw.unit == "seconds"
        and position.timestamps_unit == raw.timestamps_unit == "seconds"
        and raw_times.shape == aligned.shape == behavior.times.shape
        and np.all(np.diff(aligned) > 0)
        and np.allclose(raw_times, behavior.times, atol=TIME_ATOL, rtol=0)
        and np.allclose(aligned, behavior.aligned, atol=TIME_ATOL, rtol=0)
        and np.array_equal(raw.timestamps[:], aligned)
    )
    stored_fit = nwbfile.scratch.get(SYNC)
    return [
        Finding(
            "position", bool(matches), f"{len(behavior.times)} position samples in {behavior.unit}"
        ),
        Finding("behavior time axes", bool(clock_matches), "Original and aligned timestamps"),
        Finding(
            "clock diagnostics",
            scratch_matches(stored_fit, json.loads(json.dumps(asdict(behavior.fit)))),
            "Affine coefficients, pulse residuals and unmatched IDs",
        ),
    ]


def compare_lab(
    nwbfile: NWBFile, series: ElectricalSeries, session: Session
) -> tuple[list[Finding], ClockFit]:
    with h5py.File(session.recording, "r") as file:
        info = read_lab_metadata(file)
        voltage = file["voltage"]
        scale = VOLTS_PER_UNIT[info.recording.units[0]]

        def source_block(start: int, stop: int) -> NDArray:
            return voltage[start:stop].astype(np.float64) * scale

        findings = compare_voltage(series, info.recording, info.start_s, source_block)
    behavior = load_behavior(session, info)
    findings.extend(check_behavior(nwbfile, behavior))
    return findings, behavior.fit


def scratch_matches(scratch: ScratchData | None, expected: dict) -> bool:
    try:
        return scratch is not None and json.loads(scratch.data) == expected
    except (ValueError, TypeError):
        return False


def validate(session: Session, path: Path, *, allow_missing_age: bool = False) -> Validation:
    """Run three checks with separate results and a shared failure decision."""
    schema_errors = [str(error) for error in pynwb.validate(path=path)]
    messages = [
        {
            "importance": msg.importance.name,
            "check": msg.check_function_name,
            "message": msg.message,
            "location": msg.location,
        }
        for msg in inspect_nwbfile(path, skip_validate=True)
        if msg is not None
    ]
    age_exception = allow_missing_age and session.age is None
    if schema_errors:
        return Validation(
            [Finding("source comparison", False, "PyNWB reported structural errors")],
            schema_errors,
            messages,
            age_exception,
        )
    clock_fit = None
    with NWBHDF5IO(path, mode="r", load_namespaces=True) as io:
        nwbfile = io.read()
        metadata_matches = (
            nwbfile.session_start_time == session.start_time
            and nwbfile.session_id == session.session_id
            and nwbfile.session_description == session.description
            and nwbfile.subject is not None
            and nwbfile.subject.subject_id == session.subject_id
            and nwbfile.subject.species == session.species
            and nwbfile.subject.sex == session.sex
            and nwbfile.subject.age == session.age
        )
        provenance_matches = scratch_matches(nwbfile.scratch.get(PROVENANCE), provenance(session))
        findings = [
            Finding("session metadata", metadata_matches, "Session identity and clock origin"),
            Finding("provenance", provenance_matches, "Session manifest and source SHA-256"),
        ]
        series = nwbfile.acquisition.get(SERIES)
        if isinstance(series, ElectricalSeries):
            try:
                if session.format == "edf":
                    findings.extend(compare_edf(nwbfile, series, session))
                else:
                    comparisons, clock_fit = compare_lab(nwbfile, series, session)
                    findings.extend(comparisons)
            except (KeyError, IndexError, TypeError, ValueError, AttributeError) as error:
                findings.append(Finding("source comparison", False, str(error)))
        else:
            findings.append(Finding("electrical series", False, f"Missing {SERIES} acquisition"))
    return Validation(findings, schema_errors, messages, age_exception, clock_fit)
