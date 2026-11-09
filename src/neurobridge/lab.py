"""A NeuroConv interface for chunked HDF5 voltage and synchronized XY behavior."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

import h5py
import numpy as np
from hdmf.backends.hdf5.h5_utils import H5DataIO
from hdmf.data_utils import GenericDataChunkIterator
from neuroconv import BaseDataInterface
from numpy.typing import NDArray
from pynwb import NWBFile, TimeSeries
from pynwb.behavior import BehavioralTimeSeries, Position, SpatialSeries
from pynwb.ecephys import ElectricalSeries

from .recording import MAX_BLOCK_BYTES, SERIES, VOLTS_PER_UNIT, RecordingInfo
from .session import Session
from .sync import MAX_POSITIONS, ClockFit, fit_clock, read_pulses, read_rows, require_clock

SYNC = "neurobridge_clock_fit"
POSITION = "AlignedPosition"
WHEEL = "AlignedWheelAngle"
RAW_CLOCK = "BehaviorClock"


@dataclass(frozen=True)
class LabInfo:
    recording: RecordingInfo
    start_s: float
    dtype: np.dtype

    @property
    def end_s(self) -> float:
        return self.start_s + self.recording.samples / self.recording.rate_hz


@dataclass(frozen=True)
class Behavior:
    times: NDArray[np.float64]
    values: NDArray[np.float64]
    aligned: NDArray[np.float64]
    fit: ClockFit
    kind: Literal["xy", "wheel"]

    @property
    def unit(self) -> str:
        return "radians" if self.kind == "wheel" else "meters"


def read_lab_metadata(file: h5py.File) -> LabInfo:
    if file.attrs.get("format") != "neurobridge-lab-v1":
        raise ValueError("HDF5 file is missing the neurobridge-lab-v1 format marker")
    for name in ("voltage", "channel_labels"):
        if not isinstance(file.get(name, getlink=True), h5py.HardLink):
            raise ValueError(f"Expected a local HDF5 dataset named {name}")
        if not isinstance(file[name], h5py.Dataset) or file[name].is_virtual or file[name].external:
            raise ValueError(f"{name} must store its data within this HDF5 file")
    voltage = file["voltage"]
    channels = file["channel_labels"]
    if (
        voltage.ndim != 2
        or not all(voltage.shape)
        or voltage.dtype.name not in {"int16", "int32", "float32", "float64"}
        or channels.shape != (voltage.shape[1],)
    ):
        raise ValueError("Expected [samples, channels] int16/int32/float32/float64 voltage")
    if h5py.check_string_dtype(channels.dtype) is None:
        raise ValueError("channel_labels must contain strings")
    labels = tuple(channels.asstr()[:])
    if any(not label or label.strip() != label for label in labels) or len(set(labels)) != len(
        labels
    ):
        raise ValueError("Channel labels must be unique, nonempty, and unpadded")
    try:
        rate = float(voltage.attrs["rate_hz"])
        start = float(voltage.attrs["start_s"])
        unit = voltage.attrs["unit"]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Voltage needs scalar rate_hz, start_s and unit attributes") from error
    if (
        not np.isfinite([rate, start]).all()
        or rate <= 0
        or start < 0
        or not isinstance(unit, str)
        or unit not in VOLTS_PER_UNIT
        or not np.isfinite(start + voltage.shape[0] / rate)
    ):
        raise ValueError("Invalid HDF5 voltage rate, start time or physical unit")
    if voltage.chunks and np.prod(voltage.chunks) * voltage.dtype.itemsize > MAX_BLOCK_BYTES:
        raise ValueError("HDF5 source chunks exceed the 64 MiB block limit")
    info = RecordingInfo(voltage.shape[0], labels, rate, (unit,) * len(labels))
    return LabInfo(info, start, voltage.dtype)


def load_behavior(session: Session, info: LabInfo) -> Behavior:
    if session.behavior is None or session.behavior_pulses is None or session.neural_pulses is None:
        raise ValueError("Lab HDF5 requires a behavior CSV and both pulse CSVs")
    columns = ("position_rad",) if session.behavior_kind == "wheel" else ("x_m", "y_m")
    rows = read_rows(session.behavior, ("time_s", *columns), MAX_POSITIONS)
    coordinates = np.array(
        [[float(row[column]) for column in ("time_s", *columns)] for row in rows]
    )
    times = coordinates[:, 0]
    values = coordinates[:, 1] if session.behavior_kind == "wheel" else coordinates[:, 1:]
    require_clock(times, session.behavior.name)
    if not np.isfinite(values).all():
        raise ValueError("Behavior position values must be finite")
    behavior = read_pulses(session.behavior_pulses)
    neural = read_pulses(session.neural_pulses)
    if min(neural.values()) < info.start_s or max(neural.values()) > info.end_s:
        raise ValueError("Neural pulses fall outside the recording")
    fit = fit_clock(behavior, neural)
    aligned = fit.align(times)
    if aligned[0] < info.start_s or aligned[-1] > info.end_s:
        raise ValueError("Aligned behavior falls outside the recording")
    return Behavior(times, values, aligned, fit, session.behavior_kind)


class VoltageIterator(GenericDataChunkIterator):
    """Read one bounded HDF5 block at a time, with no long-lived input handle."""

    def __init__(self, path: Path, info: LabInfo, buffer_frames: int):
        self.path = path
        self.info = info
        frames = min(buffer_frames, info.recording.samples)
        columns = len(info.recording.channels)
        if frames < 1 or frames * columns * info.dtype.itemsize > MAX_BLOCK_BYTES:
            raise ValueError("Voltage buffer must be positive and at most 64 MiB")
        super().__init__(buffer_shape=(frames, columns), chunk_shape=(frames, columns))

    def _get_dtype(self) -> np.dtype:
        return self.info.dtype

    def _get_maxshape(self) -> tuple[int, int]:
        return self.info.recording.samples, len(self.info.recording.channels)

    def _get_data(self, selection: tuple[slice, ...]) -> NDArray:
        with h5py.File(self.path, "r") as source:
            data = source["voltage"][selection]
        if not np.isfinite(data).all():
            raise ValueError(f"Nonfinite voltage in source selection {selection}")
        return data


class LabHDF5Interface(BaseDataInterface):
    """Translate the documented lab bundle into standard NWB containers."""

    def __init__(self, session: Session):
        super().__init__(file_path=str(session.recording))
        self.session = session

    def add_to_nwbfile(
        self, nwbfile: NWBFile, metadata: dict | None = None, *, buffer_frames: int = 4096
    ) -> None:
        with h5py.File(self.session.recording, "r") as file:
            info = read_lab_metadata(file)
        behavior = load_behavior(self.session, info)
        recording = info.recording
        device = nwbfile.create_device(name="amplifier", description=self.session.device)
        group = nwbfile.create_electrode_group(
            name="electrodes",
            description="Source recording channels",
            location=self.session.location,
            device=device,
        )
        nwbfile.add_electrode_column(name="channel_name", description="Original channel label")
        for label in recording.channels:
            nwbfile.add_electrode(group=group, location=self.session.location, channel_name=label)
        electrodes = nwbfile.create_electrode_table_region(
            region=list(range(len(recording.channels))), description="Voltage column order"
        )
        nwbfile.add_acquisition(
            ElectricalSeries(
                name=SERIES,
                description="Raw electrophysiology voltage from lab HDF5",
                electrodes=electrodes,
                data=H5DataIO(
                    VoltageIterator(self.session.recording, info, buffer_frames),
                    compression="gzip",
                    compression_opts=4,
                    shuffle=True,
                ),
                rate=recording.rate_hz,
                starting_time=info.start_s,
                conversion=VOLTS_PER_UNIT[recording.units[0]],
            )
        )
        module = nwbfile.create_processing_module(
            name="behavior", description="Position and original clock aligned to neural time"
        )
        if behavior.kind == "wheel":
            position = TimeSeries(
                name=WHEEL,
                data=behavior.values,
                timestamps=behavior.aligned,
                unit=behavior.unit,
                description="Unwrapped wheel angle with timestamps in the neural recording clock",
            )
            module.add(BehavioralTimeSeries(time_series=position))
        else:
            position = SpatialSeries(
                name=POSITION,
                data=behavior.values,
                timestamps=behavior.aligned,
                reference_frame="XY coordinates in the acquisition system's tracking plane",
                unit=behavior.unit,
                description="XY position with timestamps in the neural recording clock",
            )
            module.add(Position(spatial_series=position))
        module.add(
            TimeSeries(
                name=RAW_CLOCK,
                data=behavior.times,
                unit="seconds",
                timestamps=position,
                description="Original position timestamps in the behavior device clock",
            )
        )
        nwbfile.add_scratch(
            json.dumps(asdict(behavior.fit), sort_keys=True, allow_nan=False),
            name=SYNC,
            description="Affine clock parameters, residuals, and unmatched synchronization pulses",
        )
