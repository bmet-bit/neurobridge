"""Convert a pinned 16-second SpikeGLX and wheel excerpt from IBL NYU-39."""

from __future__ import annotations

import argparse
import json
import re
import warnings
from pathlib import Path
from urllib.request import Request, urlopen

import h5py
import mtscomp
import numpy as np
from pynwb import NWBHDF5IO
from spikeinterface.extractors import read_spikeglx

from neurobridge.archive import stage_archive, validate_archive
from neurobridge.convert import convert
from neurobridge.fixtures import write_csv
from neurobridge.lab import RAW_CLOCK, SYNC, WHEEL
from neurobridge.recording import SERIES
from neurobridge.report import write_report
from neurobridge.session import Session, load_session, new_file, sha256
from neurobridge.validation import validate, voltage_block

DATASET = "https://registry.opendata.aws/ibl-brain-wide-map/"
ASSET_METADATA = "https://api.dandiarchive.org/api/assets/d3e8a94d-7f64-4ccb-9f46-ee9148cee05f/"
SESSION_ID = "6ed57216-498d-48a6-b48b-a243a34710ea"
CHANNELS = (0, 4, 8, 12, 16, 20, 24, 28)
ATLAS_ID = 1020  # PO, posterior complex of the thalamus
MAX_PAIR_DELTA_S = 0.020


def fetch_sources(cache: Path, sources: dict) -> None:
    """Require a byte-exact partial response so a full recording is never downloaded."""
    for name, asset in sources.items():
        path = cache / name
        if not path.exists():
            headers = {}
            expected_range = None
            if "byte_range" in asset:
                start, stop = asset["byte_range"]
                headers["Range"] = f"bytes={start}-{stop - 1}"
                expected_range = f"bytes {start}-{stop - 1}/{asset['object_bytes']}"
            with new_file(path) as temporary:
                with urlopen(Request(asset["url"], headers=headers), timeout=40) as response:
                    if expected_range and (
                        response.status != 206
                        or response.headers.get("Content-Range") != expected_range
                    ):
                        raise ValueError(f"Server did not honor the exact range for {name}")
                    data = response.read(asset["bytes"] + 1)
                temporary.write_bytes(data)
                verify_source(temporary, asset)
        else:
            verify_source(path, asset)


def verify_source(path: Path, asset: dict) -> None:
    if path.stat().st_size != asset["bytes"] or sha256(path) != asset["sha256"]:
        raise ValueError(f"Source fingerprint mismatch for {path}")


def read_chunks(cache: Path, name: str, asset: dict) -> tuple[np.ndarray, int, float]:
    """Rebase the official chunk index onto the downloaded bytes for mtscomp."""
    header = json.loads((cache / f"{name}_ch.ch").read_text())
    first, stop = asset["chunks"]
    offset = header["chunk_bounds"][first]
    if [header["chunk_offsets"][first], header["chunk_offsets"][stop]] != asset["byte_range"]:
        raise ValueError("Compressed index disagrees with the pinned byte range")
    for field in ("chunk_offsets", "chunk_bounds"):
        values = header[field][first : stop + 1]
        header[field] = [value - values[0] for value in values]
    reader = mtscomp.Reader()
    reader.open(cache / f"{name}.cbin", header)
    try:
        return reader[:], offset, float(reader.sample_rate)
    finally:
        reader.close()


def file_metadata(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text().splitlines() if "=" in line)


def rising_samples(samples: np.ndarray, offset: int, bit: int) -> np.ndarray:
    digital = (samples[:, -1] >> bit) & 1
    return offset + np.flatnonzero(np.diff(digital) == 1) + 1


def pair_edges(cache: Path, probe_s: np.ndarray, nidq_s: np.ndarray) -> np.ndarray:
    # The published map only identifies which 1 Hz edge belongs to which cycle.
    # The fit and holdout use the measured timestamps on both devices.
    coarse = np.load(cache / "map.npy", allow_pickle=False)
    predicted = np.interp(probe_s, coarse[:, 0], coarse[:, 1])
    candidates = np.abs(nidq_s[None, :] - predicted[:, None]) < MAX_PAIR_DELTA_S
    if not np.all(candidates.sum(axis=1) == 1):
        raise ValueError("Could not unambiguously pair the recorded sync edges")
    matched = candidates.argmax(axis=1)
    if np.any(np.diff(matched) <= 0):
        raise ValueError("Matched sync edges reuse an event or change order")
    return matched


def prepare_voltage(
    cache: Path, directory: Path, probe: np.ndarray, start: int, rate: float
) -> np.ndarray:
    channel_index = np.load(cache / "indices.npy", allow_pickle=False)
    locations = np.load(cache / "location.npy", allow_pickle=False)
    if not np.all(np.isin(CHANNELS, channel_index[locations == ATLAS_ID])):
        raise ValueError("Selected voltage channels must have the published PO region")
    metadata = file_metadata(cache / "lf_meta.meta")
    if float(metadata["imSampRate"]) != rate or int(metadata["nSavedChans"]) != probe.shape[1]:
        raise ValueError("SpikeGLX and mtscomp headers disagree")
    if metadata["imDatPrb_type"] != "0" or metadata["snsApLfSy"] != "0,384,1":
        raise ValueError("Expected the documented NP1 LF channel layout")

    # Keep the native channel order and gain metadata when making the bounded LF file.
    folder = directory / "spikeglx"
    folder.mkdir()
    bin_path = folder / "NYU-39_g0_t0.imec0.lf.bin"
    probe.astype("<i2", copy=False).tofile(bin_path)
    cropped = {
        **metadata,
        "fileName": bin_path.name,
        "fileSizeBytes": str(bin_path.stat().st_size),
        "fileTimeSecs": str(probe.shape[0] / rate),
        "firstSample": str(int(metadata["firstSample"]) + start),
    }
    cropped.pop("fileSHA1", None)
    bin_path.with_suffix(".meta").write_text(
        "".join(f"{key}={value}\n" for key, value in cropped.items()), encoding="utf-8"
    )
    with warnings.catch_warnings():
        # SpikeInterface uses AP metadata for probe geometry. Locations come from IBL ALF.
        warnings.filterwarnings(
            "ignore", message="Unable to find a corresponding metadata file for the recording"
        )
        recording = read_spikeglx(folder, stream_id="imec0.lf", use_names_as_ids=True)
    labels = [f"LF{channel}" for channel in CHANNELS]
    counts = recording.get_traces(channel_ids=labels)
    np.testing.assert_array_equal(counts, probe[:, CHANNELS])

    # Separately verify the reader's gains against the NP1 SpikeGLX header formula.
    imro = [entry.split() for entry in re.findall(r"\(([^()]+)\)", metadata["~imroTbl"])[1:]]
    lf_gains = np.array([float(imro[channel][4]) for channel in CHANNELS])
    expected_v_per_count = float(metadata["imAiRangeMax"]) / 512 / lf_gains
    indices = recording.ids_to_indices(labels)
    gains_uv = recording.get_channel_gains()[indices]
    np.testing.assert_allclose(gains_uv * 1e-6, expected_v_per_count, atol=1e-15, rtol=0)
    np.testing.assert_array_equal(recording.get_channel_offsets()[indices], np.zeros(len(labels)))
    with h5py.File(directory / "voltage.h5", "x") as file:
        file.attrs["format"] = "neurobridge-lab-v1"
        voltage = file.create_dataset("voltage", data=counts * gains_uv, chunks=(4096, len(labels)))
        voltage.attrs.update(unit="uV", rate_hz=rate, start_s=start / rate)
        file.create_dataset("channel_labels", data=labels, dtype=h5py.string_dtype("utf-8"))
    return probe[:, CHANNELS] * expected_v_per_count


def dump_json(path: Path, content: dict) -> None:
    with new_file(path) as temporary:
        temporary.write_text(
            json.dumps(content, indent=2, allow_nan=False) + "\n", encoding="utf-8"
        )


def prepare(directory: Path, cache: Path, sources: dict) -> tuple[np.ndarray, ...]:
    probe, start, probe_rate = read_chunks(cache, "lf", sources["lf.cbin"])
    nidq, nidq_start, nidq_rate = read_chunks(cache, "nidq", sources["nidq.cbin"])
    nidq_meta = file_metadata(cache / "nidq_meta.meta")
    if float(nidq_meta["niSampRate"]) != nidq_rate or nidq_meta["snsMnMaXaDw"] != "0,0,1,1":
        raise ValueError("Unexpected NIDQ sample rate or digital word layout")
    for path, bit in ((cache / "wiring.json", 6), (cache / "nidq_wiring.json", 3)):
        wiring = json.loads(path.read_text())
        if wiring["SYNC_WIRING_DIGITAL"].get(f"P0.{bit}") != "imec_sync":
            raise ValueError("Unexpected imec_sync wiring")
    probe_s = rising_samples(probe, start, 6) / probe_rate
    all_nidq_s = rising_samples(nidq, nidq_start, 3) / nidq_rate
    nidq_s = all_nidq_s[pair_edges(cache, probe_s, all_nidq_s)]
    held_out = np.arange(len(probe_s)) % 4 == 2
    if len(probe_s) != 16 or held_out[0] or held_out[-1]:
        raise ValueError("Unexpected TTL count or holdout coverage")

    wheel_s = np.load(cache / "wheel_timestamps.npy", mmap_mode="r", allow_pickle=False)
    wheel_rad = np.load(cache / "wheel_position.npy", mmap_mode="r", allow_pickle=False)
    start_row, stop_row = np.searchsorted(wheel_s, nidq_s[[0, -1]])
    wheel_s, wheel_rad = wheel_s[start_row:stop_row], wheel_rad[start_row:stop_row]
    write_csv(
        directory / "wheel.csv", ("time_s", "position_rad"), zip(wheel_s, wheel_rad, strict=True)
    )
    ids = np.arange(len(probe_s))[~held_out]
    for name, times in (("nidq", nidq_s), ("probe", probe_s)):
        write_csv(
            directory / f"{name}-pulses.csv",
            ("pulse_id", "time_s"),
            ((f"ttl-{i:02d}", times[i]) for i in ids),
        )

    expected_v = prepare_voltage(cache, directory, probe, start, probe_rate)
    dump_json(
        directory / "source-record.json",
        {
            "source_assets": sources,
            "subject_and_session_metadata": ASSET_METADATA,
            "probe_channels": list(CHANNELS),
            "atlas_structure_id": ATLAS_ID,
            "probe_sample_range": [start, start + probe.shape[0]],
            "wheel_row_range": [int(start_row), int(stop_row)],
            "probe_rate_hz": probe_rate,
            "nidq_rate_hz": nidq_rate,
            "holdout_ids": [f"ttl-{i:02d}" for i in np.flatnonzero(held_out)],
            "holdout_probe_s": probe_s[held_out].tolist(),
            "holdout_nidq_s": nidq_s[held_out].tolist(),
            "holdout_limit_s": 1 / probe_rate + 1 / nidq_rate,
        },
    )
    session = Session(
        format="lab-hdf5",
        recording=Path("voltage.h5"),
        behavior=Path("wheel.csv"),
        behavior_pulses=Path("nidq-pulses.csv"),
        neural_pulses=Path("probe-pulses.csv"),
        behavior_kind="wheel",
        source_record=Path("source-record.json"),
        session_id=SESSION_ID,
        subject_id="NYU-39",
        species="Mus musculus",
        sex="M",
        age="P153DT48829.023776S",
        start_time="2021-05-10T14:33:49.023776-04:00",
        description="IBL NYU-39 decision-making session, 16-second probe00 LF and wheel excerpt",
        device="Neuropixels 1.0 probe00, SpikeGLX",
        location="PO",
        source=(
            f"International Brain Laboratory, Brain-Wide Map, CC BY 4.0. {DATASET} "
            "Dataset DOI 10.48324/dandi.000409/0.260309.1324. "
            "NeuroBridge selected LF0/4/8/12/16/20/24/28, calibrated the voltage, "
            "cropped the ALF wheel series and fitted the independent acquisition clocks. "
            f"Published metadata {ASSET_METADATA}"
        ),
        license="CC-BY-4.0",
        time_reference=(
            "Voltage and aligned wheel timestamps use the original probe file's sample-zero "
            "clock. Raw wheel times use the NIDQ file's sample-zero clock. The published "
            "session start is retained as a metadata reference. The relation of file sample "
            "zero to absolute wall time was not verified."
        ),
        synthetic=False,
    )
    (directory / "session.json").write_text(
        session.model_dump_json(indent=2) + "\n", encoding="utf-8"
    )
    return expected_v, wheel_s, wheel_rad


def check_originals(
    nwb: Path, inputs: Path, expected_v: np.ndarray, wheel_s: np.ndarray, wheel_rad: np.ndarray
) -> dict:
    evidence = json.loads((inputs / "source-record.json").read_text())
    with NWBHDF5IO(nwb, "r") as io:
        file = io.read()
        voltage = voltage_block(file.acquisition[SERIES], 0, expected_v.shape[0])
        np.testing.assert_allclose(voltage, expected_v, atol=1e-12, rtol=0)
        behavior = file.processing["behavior"]
        wheel = behavior["BehavioralTimeSeries"][WHEEL]
        np.testing.assert_array_equal(wheel.data[:], wheel_rad)
        np.testing.assert_array_equal(behavior[RAW_CLOCK].data[:], wheel_s)
        fit = json.loads(file.scratch[SYNC].data)
        fit_ids = {pulse["pulse_id"] for pulse in fit["pulses"] if pulse["inlier"]}
        if len(fit_ids) != 12 or fit_ids.intersection(evidence["holdout_ids"]):
            raise ValueError("Expected twelve inlier fit pulses disjoint from the holdout")
        expected_times = (
            fit["neural_origin_s"] + (wheel_s - fit["behavior_origin_s"]) * fit["scale"]
        )
        np.testing.assert_allclose(wheel.timestamps[:], expected_times, atol=1e-9, rtol=0)
    held_out = np.asarray(evidence["holdout_nidq_s"])
    predicted = fit["neural_origin_s"] + (held_out - fit["behavior_origin_s"]) * fit["scale"]
    residuals = predicted - evidence["holdout_probe_s"]
    np.testing.assert_allclose(residuals, 0, atol=evidence["holdout_limit_s"], rtol=0)
    return {
        "raw_voltage_values_checked": int(expected_v.size),
        "max_error_v": float(np.max(np.abs(voltage - expected_v))),
        "raw_wheel_samples_checked": int(wheel_rad.size),
        "fit_pulses": len(fit["pulses"]),
        "drift_ppm": (fit["scale"] - 1) * 1e6,
        "offset_at_fit_origin_ms": (fit["neural_origin_s"] - fit["behavior_origin_s"]) * 1e3,
        "fit_rms_ms": fit["rms_inlier_s"] * 1e3,
        "held_out_pulses": len(held_out),
        "held_out_residuals_ms": (residuals * 1e3).tolist(),
        "held_out_max_abs_error_ms": float(np.max(np.abs(residuals)) * 1e3),
        "held_out_limit_ms": evidence["holdout_limit_s"] * 1e3,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--cache", type=Path, default=Path(".local/ibl-source"))
    args = parser.parse_args()
    sources = json.loads(Path(__file__).with_name("ibl_sources.json").read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    fetch_sources(args.cache, sources)
    inputs = args.output / "inputs"
    inputs.mkdir()
    expected = prepare(inputs, args.cache, sources)
    session = load_session(inputs / "session.json")
    nwb = args.output / "ibl.nwb"
    convert(session, nwb)
    qc = validate(session, nwb)
    dump_json(args.output / "qc.json", qc.to_dict())
    write_report(session, nwb, qc, args.output / "qc.html")
    if not qc.passed:
        raise ValueError("Strict NWB and source validation failed. See qc.json.")
    original_checks = check_originals(nwb, inputs, *expected)
    dump_json(args.output / "original-checks.json", original_checks)
    draft = args.output / "draft"
    stage_archive(session, nwb, draft)
    archive = validate_archive(draft)
    dump_json(args.output / "dandi.json", archive)
    print(json.dumps({"original_checks": original_checks, "dandi": archive}, indent=2))
    return 0 if archive["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
