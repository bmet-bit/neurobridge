"""Affine alignment from explicitly identified synchronization pulses."""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

MAX_PULSES = 512
MAX_POSITIONS = 200_000
PULSE_ATOL_S = 0.002
MAX_DRIFT_PPM = 2_000
MIN_SPAN_S = 1.0


@dataclass(frozen=True)
class PulseResidual:
    pulse_id: str
    behavior_s: float
    neural_s: float
    residual_s: float
    inlier: bool


@dataclass(frozen=True)
class ClockFit:
    behavior_origin_s: float
    neural_origin_s: float
    scale: float
    rms_inlier_s: float
    pulses: tuple[PulseResidual, ...]
    behavior_only: tuple[str, ...]
    neural_only: tuple[str, ...]

    @property
    def drift_ppm(self) -> float:
        return (self.scale - 1.0) * 1e6

    def align(self, times: NDArray[np.float64]) -> NDArray[np.float64]:
        """Interpolate only within the observed inlier span."""
        inlier_times = [pulse.behavior_s for pulse in self.pulses if pulse.inlier]
        if times.min() < inlier_times[0] or times.max() > inlier_times[-1]:
            raise ValueError("Behavior timestamps extend beyond inlier sync pulses")
        aligned = self.neural_origin_s + (times - self.behavior_origin_s) * self.scale
        if not np.isfinite(aligned).all() or np.any(np.diff(aligned) <= 0):
            raise ValueError("Aligned timestamps must remain finite and strictly increasing")
        return aligned


def read_rows(path: Path, columns: tuple[str, ...], limit: int) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream, strict=True)
        if reader.fieldnames != list(columns):
            raise ValueError(f"{path.name} must have columns {', '.join(columns)} in that order")
        rows = []
        for row in reader:
            if len(rows) == limit:
                raise ValueError(f"{path.name} exceeds {limit:,} rows")
            if None in row or any(value is None or not value.strip() for value in row.values()):
                raise ValueError(f"{path.name} contains an incomplete or overlong row")
            rows.append(row)
    if not rows:
        raise ValueError(f"{path.name} contains no data")
    return rows


def read_pulses(path: Path) -> dict[str, float]:
    rows = read_rows(path, ("pulse_id", "time_s"), MAX_PULSES)
    pulses = {row["pulse_id"].strip(): float(row["time_s"]) for row in rows}
    if len(pulses) != len(rows):
        raise ValueError(f"{path.name} contains duplicate pulse IDs")
    require_clock(np.array(list(pulses.values())), path.name)
    return pulses


def require_clock(times: NDArray[np.float64], label: str) -> None:
    if not np.isfinite(times).all() or times[0] < 0 or np.any(np.diff(times) <= 0):
        raise ValueError(f"{label} times must be finite, nonnegative and strictly increasing")


def fit_clock(behavior: dict[str, float], neural: dict[str, float]) -> ClockFit:
    """Find a robust seed from pairwise slopes, then fit inliers by least squares."""
    shared = [key for key in behavior if key in neural]
    if len(shared) < 4 or len(shared) > MAX_PULSES:
        raise ValueError(f"Need 4–{MAX_PULSES} uniquely matched pulse IDs")
    bx = np.array([behavior[key] for key in shared], dtype=np.float64)
    ny = np.array([neural[key] for key in shared], dtype=np.float64)
    require_clock(bx, "Matched behavior")
    require_clock(ny, "Matched neural")
    if np.ptp(bx) < MIN_SPAN_S:
        raise ValueError("Matched pulses must span at least one second")
    origin = float(np.median(bx))
    x = bx - origin
    i, j = np.triu_indices(len(shared), k=1)
    # Avoid slopes dominated by noise in near-coincident pairs.
    separated = x[j] - x[i] >= np.ptp(x) / 4
    i, j = i[separated], j[separated]
    scale = np.median((ny[j] - ny[i]) / (x[j] - x[i]))
    intercept = np.median(ny - x * scale)
    inliers = np.abs(ny - (intercept + x * scale)) <= PULSE_ATOL_S
    if inliers.sum() < max(4, np.ceil(0.8 * len(shared))) or np.ptp(bx[inliers]) < MIN_SPAN_S:
        raise ValueError("Sync pulses lack a stable affine consensus spanning at least one second")
    design = np.column_stack([np.ones(inliers.sum()), x[inliers]])
    intercept, scale = np.linalg.lstsq(design, ny[inliers], rcond=None)[0]
    residuals = ny - (intercept + x * scale)
    if not np.array_equal(inliers, np.abs(residuals) <= PULSE_ATOL_S):
        raise ValueError("Refit changed the sync pulse consensus")
    if abs(scale - 1.0) * 1e6 > MAX_DRIFT_PPM:
        raise ValueError(f"Clock drift exceeds {MAX_DRIFT_PPM:g} ppm")
    return ClockFit(
        behavior_origin_s=origin,
        neural_origin_s=float(intercept),
        scale=float(scale),
        rms_inlier_s=float(np.sqrt(np.mean(residuals[inliers] ** 2))),
        pulses=tuple(
            PulseResidual(key, float(b), float(n), float(residual), bool(inlier))
            for key, b, n, residual, inlier in zip(shared, bx, ny, residuals, inliers, strict=True)
        ),
        behavior_only=tuple(key for key in behavior if key not in neural),
        neural_only=tuple(key for key in neural if key not in behavior),
    )
