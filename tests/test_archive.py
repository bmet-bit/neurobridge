from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from neurobridge.archive import stage_archive, validate_archive
from neurobridge.convert import convert
from neurobridge.demo import fetch_demo
from neurobridge.fixtures import make_fixture
from neurobridge.session import load_session, sha256

pytest.importorskip("dandi")


def test_archive_staging_and_layout(tmp_path: Path) -> None:
    session = load_session(make_fixture(tmp_path / "fixture", frames=3001, channels=2))
    original = tmp_path / "lab.nwb"
    convert(session, original)
    root = tmp_path / "archive"
    asset = stage_archive(session, original, root)
    assert asset.relative_to(root).as_posix() == (
        "sub-fixture/sub-fixture_ses-synthetic-lab_behavior+ecephys.nwb"
    )
    assert sha256(asset) == sha256(original)
    result = validate_archive(root)
    assert result["passed"], result
    asset.rename(asset.with_name("wrong.nwb"))
    result = validate_archive(root)
    assert result["dandi_exit_code"] == 1, result
    assert not result["passed"]


@pytest.mark.real
def test_physionet_draft_reports_archive_blockers(tmp_path: Path) -> None:
    cached = os.environ.get("NEUROBRIDGE_REAL_EDF")
    if cached is None:
        pytest.skip("Set NEUROBRIDGE_REAL_EDF to the pinned demo EDF+ file")
    shutil.copyfile(cached, tmp_path / "S001R03.edf")
    session = load_session(fetch_demo(tmp_path))
    nwb = tmp_path / "real.nwb"
    convert(session, nwb)
    draft = tmp_path / "draft"
    stage_archive(session, nwb, draft)
    result = validate_archive(draft)
    assert not result["passed"]
    assert any(issue["id"] == "NWBI.check_subject_age" for issue in result["dandi_findings"])
    assert any("ODC-BY-1.0" in message for message in result["license_errors"])
