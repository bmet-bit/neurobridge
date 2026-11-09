"""Self-contained scientific QC report with a bounded preview."""

from __future__ import annotations

from html import escape
from pathlib import Path

import numpy as np
from pynwb import NWBHDF5IO
from pynwb.ecephys import ElectricalSeries

from .recording import SERIES
from .session import Session, new_file
from .sync import ClockFit
from .validation import Validation, voltage_block


def voltage_preview(path: Path) -> str:
    with NWBHDF5IO(path, "r", load_namespaces=True) as io:
        series = io.read().acquisition.get(SERIES)
        if not isinstance(series, ElectricalSeries) or not series.rate or not series.data.shape[0]:
            return ""
        stop = min(800, series.data.shape[0])
        trace = voltage_block(series, 0, stop)[:, 0] * 1e6
        if not np.isfinite(trace).all():
            return "<p>Preview omitted because the first channel has nonfinite values.</p>"
        start_s = series.starting_time
        end_s = start_s + (stop - 1) / series.rate
        label = series.electrodes.table["channel_name"][int(series.electrodes.data[0])]
    return (
        "<h2>Voltage preview</h2>"
        f"<p>{escape(str(label))}, {start_s:.6g} to {end_s:.6g} seconds. "
        f"Range {trace.min():.4g} to {trace.max():.4g} µV.</p>"
        + sparkline(trace, "First channel voltage")
    )


def sparkline(values: np.ndarray, title: str) -> str:
    low, high = float(values.min()), float(values.max())
    scale = high - low or 1.0
    x = np.linspace(12, 708, len(values))
    y = 132 - 120 * (values - low) / scale
    points = " ".join(f"{a:.2f},{b:.2f}" for a, b in zip(x, y, strict=True))
    return (
        '<svg viewBox="0 0 720 144" role="img">'
        f"<title>{escape(title)}</title>"
        f'<polyline points="{points}" fill="none" stroke="#09697a" stroke-width="1.5"/></svg>'
    )


def clock_diagnostics(fit: ClockFit | None) -> str:
    if fit is None:
        return ""
    rows = "".join(
        f'<tr><th scope="row">{escape(pulse.pulse_id)}</th>'
        f"<td>{pulse.behavior_s:.8g}</td><td>{pulse.neural_s:.8g}</td>"
        f"<td>{pulse.residual_s * 1e3:.6g}</td>"
        f"<td>{'Used' if pulse.inlier else 'Outlier'}</td></tr>"
        for pulse in fit.pulses
    )
    used = sum(pulse.inlier for pulse in fit.pulses)
    return f"""<h2>Clock alignment</h2>
<p>Behavior → neural clock. Drift {fit.drift_ppm:+.3f} ppm, {used}/{len(fit.pulses)} matched
pulses used, inlier RMS residual {fit.rms_inlier_s * 1e3:.4g} ms.</p>
<p>Behavior-only pulses {escape(", ".join(fit.behavior_only) or "none")}.
Neural-only pulses {escape(", ".join(fit.neural_only) or "none")}.</p>
<table><thead><tr><th>Pulse ID</th><th>Behavior (s)</th><th>Neural (s)</th>
<th>Residual (ms)</th><th>Fit status</th></tr></thead><tbody>{rows}</tbody></table>"""


def write_report(session: Session, path: Path, result: Validation, output: Path) -> None:
    verdict = "PASS" if result.passed else "FAIL"
    rows = "\n".join(
        f'<tr><th scope="row">{escape(f.check)}</th>'
        f"<td>{'Pass' if f.passed else 'Fail'}</td><td>{escape(f.detail)}</td></tr>"
        for f in result.science
    )
    schema = "".join(f"<li>{escape(error)}</li>" for error in result.schema_errors)
    inspection = "".join(
        f"<li><strong>{escape(msg['importance'])}</strong> {escape(str(msg['check']))} "
        f"({escape(str(msg['location']))}). {escape(str(msg['message']))}</li>"
        for msg in result.inspector
    )
    preview = voltage_preview(path) if result.passed else ""
    origin = "Synthetic fixture" if session.synthetic else "Recorded neuroscience data"
    exception = (
        "<p>Local QC accepts an unspecified age for this session. The Inspector finding "
        "is listed below and still applies to archive validation.</p>"
        if result.allow_missing_age
        else ""
    )
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport"
content="width=device-width, initial-scale=1"><title>NeuroBridge QC</title>
<style>
body{{max-width:56rem;margin:3rem auto;padding:0 1rem;font:1rem/1.6 system-ui;color:#1a292f}}
h1,h2{{line-height:1.2}} table{{border-collapse:collapse;width:100%}}
th,td{{padding:.6rem;text-align:left;border-bottom:1px solid #bdd1d5}}
svg{{width:100%;background:#ecf5f6}} .tag{{color:#09697a;font-weight:bold}}
</style></head>
<body><main><p class="tag">NEUROBRIDGE · {origin}</p>
<h1>Scientific QC for {escape(session.session_id)}</h1>
<p><strong>{verdict}</strong> · {escape(session.description)}</p>
{exception}
<p>{escape(session.source)} · {escape(session.license)}</p>
<p>{escape(session.time_reference)}</p>
<h2>Source-to-output checks</h2>
<table><thead><tr><th>Check</th><th>Result</th><th>Evidence</th></tr></thead>
<tbody>{rows}</tbody></table>
{preview}
{clock_diagnostics(result.clock_fit)}
<h2>PyNWB schema</h2>{f"<ul>{schema}</ul>" if schema else "<p>Zero schema errors.</p>"}
<h2>NWB Inspector</h2>
{f"<ul>{inspection}</ul>" if inspection else "<p>Zero findings.</p>"}
</main></body></html>
"""
    with new_file(output) as temporary:
        temporary.write_text(html, encoding="utf-8")
