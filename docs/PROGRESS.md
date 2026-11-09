# Progress

## Initial inspection

Downloaded the 2.5 MiB PhysioNet S001R03 EDF+ run and pinned its SHA-256. The [architecture guide](ARCHITECTURE.md) describes the supported formats and validation checks.

**Next:** Verify installed EDF extractor scaling and NeuroConv write APIs, then run the real-data slice.

## Milestone 1 · Real EDF+ round trip

- Executed `neurobridge fetch-demo .local`, `neurobridge inspect .local/session.json`, `neurobridge convert .local/session.json .local/demo.nwb`, and `neurobridge validate .local/session.json .local/demo.nwb --allow-missing-age --html .local/demo.html --json .local/demo.json`.
- Reopened 20,000 frames × 64 channels at 160 Hz. Compared all 1,280,000 voltage values against PyEDFlib physical reads with a maximum observed difference of 0 V after calibration. Verified 30 task intervals, channel order, units, regular time grid, session metadata and source digest. PyNWB reported zero schema errors.
- NWB Inspector reported a critical missing-age finding and four suggestions. Strict validation exits 1. The explicit local-only missing-age exception preserves the finding and permits a passing local QC run. Subject age is unavailable and the EDF wall time's timezone is unknown. The fixture preserves relative timing and labels the source wall time as UTC for NWB serialization.
- Executed all 3 code cells in `examples/real_eeg.ipynb` with nbclient and the project kernel. The executed notebook and standalone HTML report are in `.local/`.
- `NEUROBRIDGE_REAL_EDF=.local/S001R03.edf .venv/bin/pytest -q` passed 6 tests. Tests include heterogeneous calibration and schema-valid corruption of sample values, electrode mapping, annotation duration and sample rate.

### Calibration and validation regressions

The first converter trusted SpikeInterface's EDF calibration from Neo 0.14.6. The real file passed PyNWB schema validation but the independent physical-value comparison measured a maximum error of 0.53704 µV. The installed Neo reader divided the physical range by the digital endpoint difference plus one, adding both a gain and offset error. The converter now derives gains and offsets from the two declared endpoints, still using SpikeInterface for bounded digital reads. A synthetic two-unit, nonzero-offset regression test checks known physical values independently.

The first validation run also compared two NWB Inspector `Importance` enum objects with `>=`. The CLI smoke run raised a TypeError. The validation code now consumes the Inspector's own filtering and evaluates severity names for the policy decision.

**Next:** Implement the HDF5 DataInterface, synchronized behavior, and independent bundle comparisons.

## Milestone 2 · Lab HDF5 and independent behavior clock

- Added the NeuroConv `LabHDF5Interface`, a bounded HDMF voltage iterator, a CSV position stream and two labeled pulse streams. The NWB contains a regular electrical series, a position series with explicit aligned timestamps, the original behavior clock, and a pulse-by-pulse fit audit.
- Executed `neurobridge make-fixture .local/lab-final`, `neurobridge convert .local/lab-final/session.json .local/lab-final.nwb`, and `neurobridge validate .local/lab-final/session.json .local/lab-final.nwb --html .local/lab-final.html --json .local/lab-final.json`. All 160,000 voltage values matched exactly, all 60 position samples were verified, and PyNWB reported zero schema errors. Recovered +175 ppm drift, rejected the +50 ms pulse outlier, and reported one unmatched pulse from each clock.
- `NEUROBRIDGE_REAL_EDF=.local/S001R03.edf .venv/bin/pytest -q` passed 22 tests. The integration test computes aligned time directly from the fixture's known affine transform and tests schema-valid corruption of coordinates, raw and aligned timestamps, and clock diagnostics. A late nonfinite source block leaves no published NWB artifact.
- The fixture is synthetic. Affine interpolation requires explicit pulse IDs, a minimum inlier span, and behavior times bracketed by the accepted pulses.

**Next:** Measure memory and throughput in isolated workers, stage and check a DANDI layout locally, then run package and CI-equivalent checks.

## Milestone 3 · Performance, local archive and release checks

- Measured two synthetic HDF5 recordings of 4.01 MiB and 64.04 MiB at 1,024 and 8,192 buffer frames, three fresh workers per case. All 12 outputs passed source-to-output checks. The [benchmark log](BENCHMARKS.md) records measured wall time, RSS, throughput and output sizes with exact commands and methodology.
- Executed `neurobridge stage-archive` and `neurobridge validate-archive` on both demos. DANDI 0.82.0 returned 0 with zero findings for the synthetic local draft. The real EDF draft returned 1 with `NWBI.check_subject_age`, and NeuroBridge separately flagged the source license `ODC-BY-1.0`. The synthetic draft also passed with process network access denied.
- Locked all extras with uv 0.13.7. Fresh non-editable installs passed `NEUROBRIDGE_REAL_EDF=.local/physionet/S001R03.edf python -m pytest -q` with 26 tests each on macOS arm64 with Python 3.11.13 and 3.12.16. The suite exercises local DANDI asset layout, the real archive blockers, malformed NWB structure, verified benchmark worker output, and scientific corruption that leaves the NWB schema valid.
- Ran Ruff lint and formatting checks, built the wheel and source distribution with `python -m build`, and checked the wheel for the typed package marker and absence of raw recording files. Added a GitHub Actions workflow for Python 3.11 and 3.12. These results are local.
- Re-executed `jupyter execute --timeout=120 --output ../.local/real_eeg examples/real_eeg.ipynb`. All three code cells ran and the executed notebook contained the rendered PNG plot. The first headless execution used `plt.show()` under the Agg backend and produced no plot output. Checking the notebook output MIME bundles caught this. The final cell explicitly emits a PNG.

The real dataset establishes EDF and interval fidelity. The synthetic lab bundle establishes known clock behavior, chunked HDF5 conversion, and a locally valid archive draft. For a new submission, supply study-specific metadata and a source license accepted by the archive, then repeat conversion, source validation and local DANDI checks.

## Milestone 4 · Real clock alignment and archive validation

- `.venv/bin/python examples/ibl_demo.py .local/ibl-demo` fetched the 13 checksum-pinned assets for IBL subject NYU-39. The combined payload was 27,484,508 bytes. The LF and NIDQ files were fetched as exact byte ranges, with the remaining assets supplying wheel data, wiring, chunk indices, and channel mapping. [Provenance and reproduction](IBL_DEMO.md)
- The 16-second excerpt contains eight 2,500 Hz LF channels and 16,285 recorded wheel samples. The IBL map identifies matching pulse cycles, and the fitted and held-out timestamps are measured on both clocks. Reopened voltage differed from the native header-derived reference by at most 5.42 × 10⁻²⁰ V. Twelve pulse pairs gave +5.85 ppm drift and +1.503 ms offset at the fit center. The maximum absolute residual on four withheld pulses was 0.0173 ms against the 0.433 ms allowance.
- Replayed the final demo as `sandbox-exec -p '(version 1) (allow default) (deny network*)' .venv/bin/python examples/ibl_demo.py .local/ibl-release`. Strict QC passed with zero schema errors and four Inspector suggestions. DANDI 0.82.0 returned 0 with zero findings and zero license errors. An initial draft used a generic wheel TimeSeries and organized with the ecephys modality alone. The final NWB uses BehavioralTimeSeries for wheel angle, and the integration test checks that DANDI organizes it under both behavior and ecephys.
- `NEUROBRIDGE_REAL_EDF=.local/physionet/S001R03.edf NEUROBRIDGE_IBL_CACHE=.local/ibl-source python -m pytest -q` passed **28 tests** in locked non-editable installs on Python 3.11.13 and 3.12.16. The cached IBL replay tests the entire real draft path. The wheel test checks radians, aligned timestamps and detection of altered provenance. Ruff lint and formatting passed.
- Executed the [simulated researcher handoff](LAB_HANDOFF.md) through local DANDI validation and re-executed the three-cell EEG notebook, including the rendered plot. The [EDF calibration case study](EDF_CALIBRATION.md) documents the independent comparison and regression test. A real-sample replay reproduced the error in Neo 0.14.6 and confirmed that the upstream EDF reader at revision `50a3e59` returns the correct value.
- The real draft covers the selected LF excerpt. Published session start is retained as metadata, and time comparisons are relative to the observed acquisition clocks. The relationship between file sample zero and absolute wall time remains unverified. Keep the EDF regression and revisit the calibration workaround when changing the Neo pin.

## Documentation review

Consolidated the EDF calibration findings and reproduction commands in the [calibration note](EDF_CALIBRATION.md). Ruff lint and formatting checks passed. The full local suite passed all 28 tests with the PhysioNet recording and IBL source cache enabled, using the command above. The documented format and wall-time limits still apply.
