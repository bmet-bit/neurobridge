"""Continuous, uniformly sampled EDF+ voltage signals and annotations."""

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from pyedflib import EdfReader

from .recording import VOLTS_PER_UNIT, RecordingInfo


@dataclass(frozen=True)
class Annotation:
    start_s: float
    stop_s: float
    label: str
    duration_known: bool


def calibration_uv(
    reader: EdfReader, units: tuple[str, ...]
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Map each digital endpoint to its declared physical endpoint, in µV."""
    headers = reader.getSignalHeaders()
    dmin = np.array([header["digital_min"] for header in headers])
    dmax = np.array([header["digital_max"] for header in headers])
    pmin = np.array([header["physical_min"] for header in headers])
    pmax = np.array([header["physical_max"] for header in headers])
    if np.any(dmax <= dmin) or np.any(pmax <= pmin) or not np.isfinite([pmin, pmax]).all():
        raise ValueError("Invalid EDF calibration endpoints")
    scale = np.array([VOLTS_PER_UNIT[unit] / 1e-6 for unit in units])
    gain = (pmax - pmin) / (dmax - dmin)
    return gain * scale, (pmin - dmin * gain) * scale


def read_edf_metadata(path: Path, reader: EdfReader) -> RecordingInfo:
    with path.open("rb") as stream:
        header = stream.read(256)
    if header[192:197] != b"EDF+C" or header[:8] != b"0       ":
        raise ValueError("Expected continuous EDF+ (EDF+C)")
    samples = reader.getNSamples()
    rates = reader.getSampleFrequencies()
    labels = tuple(reader.getSignalLabels())
    units = tuple(reader.getPhysicalDimension(i) for i in range(len(labels)))
    if (
        not labels
        or len(set(labels)) != len(labels)
        or not all(labels)
        or not np.all(samples == samples[0])
        or samples[0] < 1
        or not np.all(rates == rates[0])
        or not np.isfinite(rates[0])
        or rates[0] <= 0
        or any(unit not in VOLTS_PER_UNIT for unit in units)
    ):
        raise ValueError("Expected unique channels with voltage units and a shared time grid")
    return RecordingInfo(int(samples[0]), labels, float(rates[0]), units)


def read_annotations(reader: EdfReader, duration_s: float) -> list[Annotation]:
    starts, durations, labels = reader.readAnnotations()
    result = []
    for start, duration, label in zip(starts, durations, labels, strict=True):
        stop = start + max(duration, 0.0)
        if (
            not np.isfinite([start, duration, stop]).all()
            or duration < 0
            and duration != -1
            or start < 0
            or stop > duration_s + 1e-9
            or not label.strip()
        ):
            raise ValueError(f"Invalid EDF annotation at {start} s")
        result.append(Annotation(float(start), float(stop), str(label), duration != -1))
    return result


def inspect_edf(path: Path) -> tuple[RecordingInfo, list[Annotation]]:
    with EdfReader(str(path)) as reader:
        info = read_edf_metadata(path, reader)
        return info, read_annotations(reader, info.samples / info.rate_hz)
