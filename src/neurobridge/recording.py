"""The shared time-major voltage contract."""

from dataclasses import dataclass

SERIES = "NeuralVoltage"
VOLTS_PER_UNIT = {"V": 1.0, "mV": 1e-3, "uV": 1e-6, "µV": 1e-6, "nV": 1e-9}
MAX_BLOCK_BYTES = 64 * 1024**2


@dataclass(frozen=True)
class RecordingInfo:
    samples: int
    channels: tuple[str, ...]
    rate_hz: float
    units: tuple[str, ...]
