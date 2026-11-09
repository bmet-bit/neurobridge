"""Download exactly one public, versioned EEG recording."""

from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

from .session import Session, new_file, sha256

DATASET = "https://doi.org/10.13026/C28G6P"
URL = "https://physionet.org/files/eegmmidb/1.0.0/S001/S001R03.edf"
SHA256 = "3427c8d01bff1380bc9ab9f27a35ece2af5dfadf3e291bbc05eb66e4dadbfe2e"
MAX_BYTES = 3 * 1024**2


def fetch_demo(directory: Path) -> Path:
    """Reuse the recording only if its contents match the pinned digest."""
    recording = directory / "S001R03.edf"
    manifest = directory / "session.json"
    if not recording.exists():
        with new_file(recording) as temporary:
            with urlopen(URL, timeout=30) as response, temporary.open("wb") as stream:
                data = response.read(MAX_BYTES + 1)
                if len(data) > MAX_BYTES:
                    raise ValueError("Unexpected demo download size")
                stream.write(data)
            if sha256(temporary) != SHA256:
                raise ValueError("Demo download checksum mismatch")
    if sha256(recording) != SHA256:
        raise ValueError(f"Demo checksum mismatch for {recording}")
    session = Session(
        format="edf",
        recording=Path(recording.name),
        session_id="S001R03",
        subject_id="S001",
        species="Homo sapiens",
        sex="U",
        start_time=datetime.fromisoformat("2009-08-12T16:15:00+00:00"),
        description="PhysioNet EEGMMIDB subject S001 run 03, executed left/right fist movements",
        device="BCI2000",
        location="Scalp",
        source=f"Schalk et al., EEG Motor Movement/Imagery Dataset v1.0.0. {DATASET}. {URL}",
        license="ODC-BY-1.0",
        time_reference="EDF wall time labeled UTC for NWB. Source timezone is unknown.",
        synthetic=False,
    )
    contents = session.model_dump_json(indent=2) + "\n"
    if manifest.exists():
        if Session.model_validate_json(manifest.read_bytes()) != session:
            raise ValueError(f"Existing demo manifest has different metadata: {manifest}")
    else:
        with new_file(manifest) as temporary:
            temporary.write_text(contents, encoding="utf-8")
    return manifest
