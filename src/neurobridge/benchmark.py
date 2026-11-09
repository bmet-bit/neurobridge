"""Measure complete conversions in fresh workers and verify each output."""

from __future__ import annotations

import json
import platform
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from time import perf_counter

import h5py

from .convert import convert
from .recording import SERIES
from .session import load_session, sha256
from .validation import validate


def peak_rss_bytes() -> int:
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # Darwin reports bytes. Linux reports KiB.
    return peak if sys.platform == "darwin" else peak * 1024


def measure_worker(
    manifest: Path, nwb: Path, buffer_frames: int, *, allow_missing_age: bool
) -> dict:
    session = load_session(manifest)
    baseline = peak_rss_bytes()
    start = perf_counter()
    convert(session, nwb, buffer_frames=buffer_frames)
    elapsed = perf_counter() - start
    peak = peak_rss_bytes()
    validation = validate(session, nwb, allow_missing_age=allow_missing_age)
    if not validation.passed:
        raise ValueError(f"Benchmark output failed QC: {validation.to_dict()}")
    with h5py.File(nwb, "r") as file:
        data = file[f"acquisition/{SERIES}/data"]
        return {
            "seconds": elapsed,
            "peak_rss_bytes": peak,
            "pre_conversion_peak_rss_bytes": baseline,
            "output_bytes": nwb.stat().st_size,
            "chunk_shape": data.chunks,
            "sample_count": int(data.shape[0]),
            "channel_count": int(data.shape[1]),
        }


def benchmark(
    manifest: Path,
    buffer_frames: list[int],
    repeats: int = 1,
    *,
    allow_missing_age: bool = False,
) -> dict:
    if sys.platform not in {"darwin", "linux"}:
        raise ValueError("RSS benchmarking currently supports Linux and macOS")
    if repeats < 1 or not buffer_frames or any(frames < 1 for frames in buffer_frames):
        raise ValueError("Benchmark needs positive repeat and buffer frame counts")
    session = load_session(manifest)
    cases = []
    with TemporaryDirectory(prefix="neurobridge-benchmark-") as directory:
        for frames in buffer_frames:
            runs = []
            for _ in range(repeats):
                nwb = Path(directory) / "benchmark.nwb"
                process = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "neurobridge.benchmark",
                        str(manifest),
                        str(nwb),
                        str(frames),
                        str(int(allow_missing_age)),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if process.returncode:
                    raise ValueError(f"Benchmark worker failed: {process.stderr.strip()}")
                runs.append(json.loads(process.stdout))
                nwb.unlink()
            elapsed = median(run["seconds"] for run in runs)
            cases.append(
                {
                    "buffer_frames": frames,
                    "median_seconds": elapsed,
                    "median_peak_rss_bytes": median(run["peak_rss_bytes"] for run in runs),
                    "frames_per_second": runs[0]["sample_count"] / elapsed,
                    "runs": runs,
                }
            )
    return {
        "session": session.session_id,
        "format": session.format,
        "synthetic": session.synthetic,
        "input_bytes": session.recording.stat().st_size,
        "input_sha256": sha256(session.recording),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "packages": {
            package: version(package) for package in ("neurobridge", "h5py", "pynwb", "neuroconv")
        },
        "allow_missing_age": allow_missing_age,
        "validated_outputs": len(cases) * repeats,
        "cases": cases,
    }


if __name__ == "__main__":
    manifest_arg, nwb_arg, frames_arg, age_arg = sys.argv[1:]
    print(
        json.dumps(
            measure_worker(
                Path(manifest_arg), Path(nwb_arg), int(frames_arg), allow_missing_age=age_arg == "1"
            )
        )
    )
