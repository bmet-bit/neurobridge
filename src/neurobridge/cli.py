"""Command-line entry point."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from pathlib import Path

import h5py

from .archive import stage_archive, validate_archive
from .benchmark import benchmark
from .convert import convert
from .demo import fetch_demo
from .edf import inspect_edf
from .fixtures import make_fixture
from .lab import load_behavior, read_lab_metadata
from .report import write_report
from .session import Session, load_session, new_file
from .validation import validate


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("fetch-demo", help="Fetch the pinned public EDF+ recording")
    demo.add_argument("directory", type=Path)
    fixture = commands.add_parser("make-fixture", help="Generate a synthetic lab HDF5 bundle")
    fixture.add_argument("directory", type=Path)
    fixture.add_argument("--frames", type=int, default=20_000)
    fixture.add_argument("--channels", type=int, default=8)
    inspect = commands.add_parser("inspect", help="Describe a session's source data")
    inspect.add_argument("session", type=Path)
    conversion = commands.add_parser("convert", help="Write an NWB file")
    conversion.add_argument("session", type=Path)
    conversion.add_argument("output", type=Path)
    conversion.add_argument("--buffer-frames", type=int, default=4096)
    validation = commands.add_parser("validate", help="Check schema, best practices and fidelity")
    validation.add_argument("session", type=Path)
    validation.add_argument("nwb", type=Path)
    validation.add_argument("--html", type=Path)
    validation.add_argument("--json", type=Path)
    validation.add_argument(
        "--allow-missing-age", action="store_true", help="Allow missing age for local QC only"
    )
    measure = commands.add_parser("benchmark", help="Measure chunked conversion in fresh workers")
    measure.add_argument("session", type=Path)
    measure.add_argument("--buffer-frames", type=int, nargs="+", default=[1024, 8192])
    measure.add_argument("--repeats", type=int, default=1)
    measure.add_argument("--json", type=Path)
    measure.add_argument("--allow-missing-age", action="store_true")
    stage = commands.add_parser("stage-archive", help="Copy a verified NWB into a local draft")
    stage.add_argument("session", type=Path)
    stage.add_argument("nwb", type=Path)
    stage.add_argument("directory", type=Path)
    stage.add_argument(
        "--metadata", type=Path, help="Existing dandiset.yaml to copy into the draft"
    )
    archive = commands.add_parser("validate-archive", help="Run local DANDI and license checks")
    archive.add_argument("directory", type=Path)
    archive.add_argument("--json", type=Path)
    return root


def inspect_source(session: Session) -> dict:
    fields = {
        "session": session.session_id,
        "synthetic": session.synthetic,
        "format": session.format,
    }
    if session.format == "edf":
        info, annotations = inspect_edf(session.recording)
        return {**fields, **asdict(info), "annotations": len(annotations)}
    with h5py.File(session.recording, "r") as file:
        info = read_lab_metadata(file)
    behavior = load_behavior(session, info)
    return {
        **fields,
        **asdict(info.recording),
        "start_s": info.start_s,
        "behavior_kind": behavior.kind,
        "position_samples": len(behavior.times),
        "drift_ppm": behavior.fit.drift_ppm,
        "clock_fit": asdict(behavior.fit),
    }


def emit_json(data: dict, output: Path | None = None) -> None:
    payload = json.dumps(data, indent=2, allow_nan=False)
    if output is not None:
        with new_file(output) as temporary:
            temporary.write_text(payload + "\n", encoding="utf-8")
    print(payload)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "fetch-demo":
            print(fetch_demo(args.directory))
            return 0
        if args.command == "make-fixture":
            print(make_fixture(args.directory, frames=args.frames, channels=args.channels))
            return 0
        if args.command == "validate-archive":
            archive_result = validate_archive(args.directory)
            emit_json(archive_result, args.json)
            return 0 if archive_result["passed"] else 1
        if args.command == "benchmark":
            measurements = benchmark(
                args.session,
                args.buffer_frames,
                args.repeats,
                allow_missing_age=args.allow_missing_age,
            )
            emit_json(measurements, args.json)
            return 0
        session = load_session(args.session)
        if args.command == "inspect":
            emit_json(inspect_source(session))
        elif args.command == "convert":
            convert(session, args.output, buffer_frames=args.buffer_frames)
            print(args.output)
        elif args.command == "validate":
            result = validate(session, args.nwb, allow_missing_age=args.allow_missing_age)
            if args.html:
                write_report(session, args.nwb, result, args.html)
            emit_json(result.to_dict(), args.json)
            return 0 if result.passed else 1
        elif args.command == "stage-archive":
            print(stage_archive(session, args.nwb, args.directory, metadata=args.metadata))
    except (OSError, ValueError, RuntimeError, csv.Error) as error:
        print(f"neurobridge: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
