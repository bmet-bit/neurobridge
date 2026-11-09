# Architecture

NeuroBridge separates input preparation, NWB conversion, source-to-output verification, and local DANDI staging.

1. **Real-data slice.** Download and pin PhysioNet EEGMMIDB S001R03 (roughly 2.5 MiB). Use SpikeInterface to read continuous EDF+ EEG, preserve EDF+ task annotations, and write NWB through NeuroConv and PyNWB. Reopen the file and compare bounded windows of calibrated voltage against PyEDFlib physical readings, verify channel identity and order, compare the time axis and annotations, then run schema and Inspector validation. Generate an HTML report and execute a notebook.
2. **Lab bundle.** Define a small HDF5 voltage format and CSV behavior and pulse contracts. Implement a custom NeuroConv DataInterface. Fit an affine clock from explicitly paired pulse IDs, assess residuals and outliers, and guard against extrapolation. Reopen and check synchronized behavior, provenance, and the full neural data stream.
3. **Performance and archive.** Measure conversion with different buffer sizes in fresh subprocesses, including peak RSS, elapsed time, and output size. Stage local DANDI asset paths with official tooling and validate the package without account access.
4. **Release checks.** Pin dependencies, build and install the wheel, run tests and lint in CI, and document real-data evidence, injected corruption, and reproducible commands.

The source EDF recording and derived NWB data are local artifacts. The public dataset is attributed under the Open Data Commons Attribution license. The task annotation clock is the EEG clock. A separate synthetic lab bundle exercises alignment with known drift and corrupt or missing pulse pairs.

## Session and inputs

A JSON manifest declares format, session and subject IDs, species, sex, optional ISO 8601 age, timezone-aware start, device and location, description, provenance, license, clock convention, and whether the input is synthetic. Input paths are resolved relative to the manifest. `fetch-demo` and `make-fixture` write complete example manifests. Keep sources fixed throughout conversion and verification.

### EDF+

The reader accepts continuous EDF+ with unique channel labels, a shared regular sampling grid, and voltage units `V`, `mV`, `uV`, `µV`, or `nV`. The manifest's wall time must match the EDF header. Supported annotations start within the recording and use finite times. An absent EDF duration becomes a zero-length interval with `duration_known=false`. Original labels, onset, and duration are otherwise preserved.

SpikeInterface reads digital samples through Neo. The converter takes each channel's gain from the two EDF digital and physical endpoints and derives the intercept from the minimum endpoint. With a common physical offset, NeuroConv writes digital counts with NWB scalar and per-channel scaling. Heterogeneous offsets use NeuroConv's calibrated float32 path. A separate PyEDFlib physical read supplies the reference values for the validator. [EDF specification](https://www.edfplus.info/specs/edf.html) · [NeuroConv SpikeInterface utilities](https://neuroconv.readthedocs.io/en/stable/api/tools.spikeinterface.html)

### Lab HDF5 and CSV

| Input | Contract |
| --- | --- |
| Recording HDF5 | Root attribute `format="neurobridge-lab-v1"`. Local dataset `/voltage` is shaped `(frames, channels)` with signed int16, int32, float32, or float64 samples and scalar `unit`, `rate_hz`, and `start_s` attributes. The last two specify the regular sampling grid in session-relative seconds. |
| Channel labels | Local `/channel_labels` one-dimensional string dataset in voltage column order. Values are unique, nonempty, and unpadded. |
| Behavior CSV | Default header `time_s,x_m,y_m` with finite planar coordinates in meters. A manifest with `behavior_kind="wheel"` uses `time_s,position_rad` and finite, unwrapped wheel angles in radians. Both use strictly increasing seconds from the behavior device origin and accept at most 200,000 rows. |
| Pulse CSVs | Header `pulse_id,time_s`. One CSV per clock with unique nonempty IDs and finite nonnegative, strictly increasing times. Use the same ID for the same physical pulse. Neural pulse times use the session origin. Limit 512 pulses per clock. |

`LabHDF5Interface.add_to_nwbfile` accepts the manifest-backed session and a PyNWB file supplied by the shared converter. An HDMF `GenericDataChunkIterator` keeps the neural matrix on disk and emits a complete-channel slice at a time. The configured output block and source HDF5 chunks are capped at 64 MiB. Behavior and pulse data are held in memory within the stated row limits. [NeuroConv interface guide](https://neuroconv.readthedocs.io/en/main/developer_guide/build_data_interface.html) · [HDMF iterator API](https://hdmf.readthedocs.io/en/stable/hdmf.data_utils.html)

## Clock model

Fit `neural_s = neural_origin_s + scale × (behavior_s − behavior_origin_s)`. The signed drift is `(scale − 1) × 1e6` ppm. Pulse pairs are determined by identity and their order must agree across clocks. A median slope from well-separated pairs seeds the fit. The converter then refits accepted pairs by least squares and rejects an unstable consensus. The policy requires at least four and at least 80% of the matched pairs within 2 ms, one second of inlier coverage, and drift within ±2,000 ppm. Position is interpolated within inlier coverage. Its timestamps must remain strictly increasing within the neural recording.

A `Position/AlignedPosition` spatial series stores the coordinates and aligned timestamps. `BehaviorClock` stores the raw behavior timestamps as values linked to that same aligned axis. The scratch record contains coefficients, signed observed-minus-predicted residuals, inlier flags, and IDs present on one clock only. This model assumes one stable affine relation. Clock resets, ambiguous pulse IDs, nonaffine drift and extrapolation need a different alignment policy. [PyNWB behavior containers](https://pynwb.readthedocs.io/en/stable/pynwb.behavior.html)

Wheel-angle sessions store `AlignedWheelAngle` in a `BehavioralTimeSeries` container and retain the same linked `BehaviorClock`. The [IBL example](IBL_DEMO.md) prepares the lab contract from pinned native SpikeGLX byte ranges and wheel arrays. A manifest can include a `source_record` sidecar to fingerprint external asset URLs, hashes, and selection decisions.

## Validation and failure handling

- PyNWB checks the cached schema. NWB Inspector checks best practices and required metadata. Scientific validation independently reads source and output data.
- Voltage is compared in bounded windows using `1e-12 V + 2e-7 × |source|` to accommodate NeuroConv's float32 path. The checks cover channel labels and order, dimensions, on-disk units, the first and last times on the regular grid, interval codes and boundaries, and original and aligned behavior. Timestamp tolerance is 1 ns. The synthetic integration test evaluates alignment against the known transform, separate from the production fitter.
- Provenance includes SHA-256 fingerprints of the recording and any sidecar files. The CLI refuses to replace existing outputs. Conversion writes to a temporary file on the destination filesystem and publishes it only after the writer closes. Filesystems must support hard links for these single-file outputs.
- DANDI checks the staged layout, schema and archive metadata rules. An explicit local-only age exception never suppresses the DANDI result. `validate-archive` runs on the staged package. Re-run source-to-output QC with the source manifest after editing an NWB asset. [PyNWB validation](https://pynwb.readthedocs.io/en/stable/validation.html) · [DANDI validation](https://dandi.readthedocs.io/en/latest/cmdline/validate.html)
