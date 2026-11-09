"""Session metadata and artifact I/O."""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, StringConstraints, model_validator

Label = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Identifier = Annotated[str, StringConstraints(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9-]*$")]
SOURCE_PATH_FIELDS = ("recording", "behavior", "behavior_pulses", "neural_pulses", "source_record")


class Session(BaseModel):
    """The acquisition and provenance fields needed to write a session."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    format: Literal["edf", "lab-hdf5"]
    recording: Path
    session_id: Identifier
    subject_id: Identifier
    species: Label
    sex: Literal["M", "F", "U", "O"]
    age: Label | None = None
    start_time: AwareDatetime
    description: Label
    device: Label
    location: Label
    source: Label
    license: Label
    time_reference: Label
    synthetic: bool
    behavior: Path | None = None
    behavior_pulses: Path | None = None
    neural_pulses: Path | None = None
    behavior_kind: Literal["xy", "wheel"] = "xy"
    source_record: Path | None = None

    @model_validator(mode="after")
    def require_sidecars(self) -> Session:
        sidecars = (self.behavior, self.behavior_pulses, self.neural_pulses)
        if self.format == "lab-hdf5" and any(path is None for path in sidecars):
            raise ValueError("A lab-hdf5 session needs behavior, behavior_pulses and neural_pulses")
        if self.format == "edf" and (
            any(path is not None for path in sidecars) or self.behavior_kind != "xy"
        ):
            raise ValueError("An EDF session takes its annotations from the EDF file")
        return self

    def source_paths(self) -> dict[str, Path]:
        return {
            name: path for name in SOURCE_PATH_FIELDS if (path := getattr(self, name)) is not None
        }


def load_session(path: Path) -> Session:
    session = Session.model_validate_json(path.read_bytes())
    return session.model_copy(
        update={name: path.parent / source for name, source in session.source_paths().items()}
    )


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


@contextmanager
def new_file(path: Path) -> Iterator[Path]:
    """Publish a complete local file atomically without replacing existing data."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(path)
    with tempfile.TemporaryDirectory(prefix=".neurobridge-", dir=path.parent) as directory:
        temporary = Path(directory) / path.name
        yield temporary
        os.link(temporary, path)
