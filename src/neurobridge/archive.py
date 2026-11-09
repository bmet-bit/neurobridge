"""Stage and validate local DANDI drafts with the pinned DANDI CLI."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from importlib.metadata import version
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory

from hdmf.build.errors import ConstructError
from pynwb import NWBHDF5IO

from .convert import PROVENANCE
from .session import Session
from .validation import validate


def run_dandi(*args: str) -> subprocess.CompletedProcess[str]:
    if find_spec("dandi") is None:
        raise ValueError("Install NeuroBridge with the archive extra to use DANDI commands")
    return subprocess.run(
        [sys.executable, "-c", "from dandi.cli.command import main; main()", *args],
        env={**os.environ, "NO_ET": "1"},
        capture_output=True,
        text=True,
        check=False,
    )


def stage_archive(
    session: Session, nwb: Path, directory: Path, *, metadata: Path | None = None
) -> Path:
    """Copy a scientifically verified NWB into a new, local Dandiset draft."""
    if nwb.suffix != ".nwb" or not nwb.is_file():
        raise ValueError("Stage requires an existing NWB file")
    directory = directory.resolve()
    directory.mkdir(parents=True, exist_ok=False)
    try:
        organized = run_dandi(
            "organize",
            "--files-mode",
            "copy",
            "--dandiset-path",
            str(directory),
            "--jobs",
            "1",
            "--required-field",
            "session_id",
            str(nwb.resolve()),
        )
        if organized.returncode:
            raise ValueError(f"DANDI organize failed: {organized.stderr.strip()}")
        assets = list(directory.glob("sub-*/*.nwb"))
        if len(assets) != 1:
            raise ValueError("Expected DANDI organize to produce one NWB asset")
        asset = assets[0]
        result = validate(session, asset, allow_missing_age=True)
        if not result.passed:
            failed = [finding.check for finding in result.science if not finding.passed]
            raise ValueError(f"Staged file failed QC: {', '.join(failed) or 'schema or Inspector'}")
        target_metadata = directory / "dandiset.yaml"
        if metadata is not None:
            shutil.copyfile(metadata, target_metadata)
        else:
            # JSON is a YAML subset. Preserve the upstream license verbatim in the draft.
            draft = {
                "identifier": "LOCAL:000000",
                "name": f"NeuroBridge local review · {session.session_id}",
                "description": f"{session.description}. Source: {session.source}",
                "license": [f"spdx:{session.license}"],
            }
            target_metadata.write_text(json.dumps(draft, indent=2) + "\n", encoding="utf-8")
        return asset
    except BaseException:
        shutil.rmtree(directory)
        raise


def validate_archive(directory: Path) -> dict:
    """Return DANDI findings and a license check using the embedded source provenance."""
    directory = directory.resolve()
    metadata = directory / "dandiset.yaml"
    if not metadata.is_file():
        raise ValueError("Expected a DANDI draft folder containing dandiset.yaml")
    with TemporaryDirectory(prefix="neurobridge-dandi-") as temp:
        output = Path(temp) / "dandi.json"
        process = run_dandi(
            "validate",
            "--min-severity",
            "INFO",
            "--format",
            "json",
            "--output",
            str(output),
            str(directory),
        )
        if not output.is_file() or process.returncode not in (0, 1):
            raise ValueError(f"DANDI validation could not run: {process.stderr.strip()}")
        findings = json.loads(output.read_text(encoding="utf-8"))
    if not isinstance(findings, list):
        raise ValueError("Unexpected DANDI validation output")
    if process.returncode and not any(
        item["severity"] in ("ERROR", "CRITICAL") for item in findings
    ):
        raise ValueError(f"DANDI validation did not complete: {process.stderr.strip()}")
    license_errors = archive_license_errors(directory, metadata)
    return {
        "passed": process.returncode == 0 and not license_errors,
        "dandi_version": version("dandi"),
        "dandi_exit_code": process.returncode,
        "dandi_findings": findings,
        "license_errors": license_errors,
    }


def archive_license_errors(directory: Path, metadata: Path) -> list[str]:
    from dandischema.models import LicenseType
    from ruamel.yaml import YAML
    from ruamel.yaml.error import YAMLError

    try:
        draft = YAML(typ="safe").load(metadata)
    except YAMLError as error:
        return [f"Invalid dandiset.yaml: {error}"]
    declared = draft.get("license") if isinstance(draft, dict) else None
    accepted = {item.value for item in LicenseType}
    if (
        not isinstance(declared, list)
        or not declared
        or any(not isinstance(item, str) or item not in accepted for item in declared)
    ):
        return [
            f"DANDI draft license {declared!r} must use an accepted SPDX value "
            f"({', '.join(sorted(accepted))})"
        ]
    assets = sorted(directory.glob("sub-*/*.nwb"))
    if not assets:
        return ["Draft contains no organized NWB assets"]
    errors = []
    for asset in assets:
        try:
            with NWBHDF5IO(asset, "r") as io:
                scratch = io.read().scratch.get(PROVENANCE)
                license_id = json.loads(scratch.data)["license"] if scratch is not None else None
        except (ConstructError, OSError, ValueError, TypeError, KeyError):
            license_id = None
            if f"spdx:{license_id}" not in declared:
                errors.append(
                    f"{asset.name}: draft license differs from source license {license_id}"
                )
    return errors
