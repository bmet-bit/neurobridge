"""Write NWB acquisition and interval containers with NeuroConv and PyNWB."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pynwb
from neuroconv.tools.spikeinterface import add_recording_to_nwbfile
from pyedflib import EdfReader
from pynwb import NWBHDF5IO, NWBFile
from pynwb.file import Subject
from spikeinterface.extractors import read_edf

from .edf import calibration_uv, read_annotations, read_edf_metadata
from .lab import LabHDF5Interface
from .recording import MAX_BLOCK_BYTES, SERIES
from .session import SOURCE_PATH_FIELDS, Session, new_file, sha256

ANNOTATIONS = "task_annotations"
PROVENANCE = "neurobridge_provenance"


def provenance(session: Session) -> dict:
    fields = session.model_dump(mode="json", exclude=set(SOURCE_PATH_FIELDS))
    if session.behavior_kind == "xy":
        fields.pop("behavior_kind")
    sidecars = {
        name: {"file": path.name, "sha256": sha256(path)}
        for name, path in session.source_paths().items()
        if name != "recording"
    }
    return {
        **fields,
        "recording": session.recording.name,
        "sha256": sha256(session.recording),
        **({"sidecars": sidecars} if sidecars else {}),
    }


def make_nwbfile(session: Session) -> NWBFile:
    nwbfile = pynwb.NWBFile(
        session_description=session.description,
        identifier=f"neurobridge-{session.subject_id}-{session.session_id}",
        session_id=session.session_id,
        session_start_time=session.start_time,
        subject=Subject(
            subject_id=session.subject_id,
            species=session.species,
            sex=session.sex,
            age=session.age,
            description="Synthetic fixture subject" if session.synthetic else session.subject_id,
        ),
    )
    nwbfile.add_scratch(
        json.dumps(provenance(session), sort_keys=True),
        name=PROVENANCE,
        description="NeuroBridge input metadata and recording fingerprint",
    )
    return nwbfile


def convert(session: Session, path: Path, *, buffer_frames: int = 4096) -> None:
    if path.suffix != ".nwb" or buffer_frames < 1:
        raise ValueError("Output needs a .nwb suffix and a positive buffer frame count")
    nwbfile = make_nwbfile(session)
    if session.format == "edf":
        add_edf(nwbfile, session, buffer_frames)
    else:
        LabHDF5Interface(session).add_to_nwbfile(nwbfile, buffer_frames=buffer_frames)
    with new_file(path) as temporary, NWBHDF5IO(temporary, "w") as io:
        io.write(nwbfile)


def add_edf(nwbfile: NWBFile, session: Session, buffer_frames: int) -> None:
    with EdfReader(str(session.recording)) as reader:
        info = read_edf_metadata(session.recording, reader)
        if reader.getStartdatetime() != session.start_time.replace(tzinfo=None):
            raise ValueError("Session start wall time disagrees with the EDF header")
        annotations = read_annotations(reader, info.samples / info.rate_hz)
        gains, offsets = calibration_uv(reader, info.units)
    recording = read_edf(session.recording)
    if (
        tuple(recording.get_property("channel_name")) != info.channels
        or recording.get_num_samples() != info.samples
        or recording.get_sampling_frequency() != info.rate_hz
        or recording.get_num_segments() != 1
    ):
        raise ValueError("SpikeInterface and EDF header disagree about the recording shape or rate")
    channels = len(info.channels)
    # Neo 0.14.6 divides by (digital_max - digital_min + 1) for EDF.
    # Replace its calibration, retaining SpikeInterface's bounded digital reads.
    recording.set_channel_gains(gains)
    recording.set_channel_offsets(offsets)
    recording.set_property("group_name", np.full(channels, "electrodes"))
    buffer = min(info.samples, buffer_frames)
    if buffer * channels * 4 > MAX_BLOCK_BYTES:
        raise ValueError("EDF buffer cannot exceed 64 MiB of calibrated voltage")
    metadata = {
        "Devices": {"amplifier": {"name": "amplifier", "description": session.device}},
        "Ecephys": {
            "ElectrodeGroups": {
                "electrodes": {
                    "name": "electrodes",
                    "description": "Source recording channels",
                    "location": session.location,
                    "device_metadata_key": "amplifier",
                }
            },
            "ElectricalSeries": {
                SERIES: {"name": SERIES, "description": "Raw electrophysiology voltage"}
            },
        },
    }
    add_recording_to_nwbfile(
        recording=recording,
        nwbfile=nwbfile,
        metadata=metadata,
        metadata_key=SERIES,
        data_representation="digital_counts" if np.all(offsets == offsets[0]) else "physical_units",
        iterator_options={"buffer_shape": (buffer, channels), "chunk_shape": (buffer, channels)},
    )
    if annotations:
        intervals = nwbfile.create_time_intervals(
            name=ANNOTATIONS, description="EDF+ task annotations in the neural recording clock"
        )
        intervals.add_column("label", "Original EDF+ annotation code")
        intervals.add_column("duration_known", "True when EDF+ supplied the interval duration")
        for event in annotations:
            intervals.add_interval(
                start_time=event.start_s,
                stop_time=event.stop_s,
                label=event.label,
                duration_known=event.duration_known,
            )
