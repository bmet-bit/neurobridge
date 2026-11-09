# Real SpikeGLX and wheel alignment

This demo converts a bounded excerpt of International Brain Laboratory subject **NYU-39**, session **6ed57216-498d-48a6-b48b-a243a34710ea**, probe **00**. It uses **40,000** low-frequency samples at **2,500 Hz** on each of eight Neuropixels channels and **16,285** irregularly sampled wheel angles. The two observed clocks are the probe's LF acquisition clock and the independent NIDQ clock that recorded the rotary encoder. [IBL open data and CC BY 4.0 license](https://registry.opendata.aws/ibl-brain-wide-map/) · [IBL wheel data](https://docs.internationalbrainlab.org/notebooks_external/docs_wheel_moves.html)

## Reproduce

From the repository root, use the locked environment with all extras.

```sh
uv sync --locked --all-extras --no-editable
uv run --no-sync python examples/ibl_demo.py .local/ibl-demo
```

The command downloads approximately **26.2 MiB** into `.local/ibl-source`, or uses an existing byte-exact cache. The [source descriptor](../examples/ibl_sources.json) pins all 13 assets by SHA-256, including two exact byte ranges for compressed SpikeGLX LF and NIDQ. Use a new output directory to replay. The result includes a 1 MiB NWB, `qc.html`, `original-checks.json`, `dandi.json` and a staged draft. The input folder holds the derived lab bundle, source record, and native LF clip.

### What the pipeline checks

1. **Decode and calibrate.** The example uses mtscomp to decompress complete SpikeGLX chunks, creates a native LF clip, and reads the requested channels with SpikeInterface. It checks every selected raw count against the decoded chunk and independently derives the NP1 gain from the original IMRO table. ALF electrode indices and CCF structure IDs identify LF0/4/8/12/16/20/24/28 as posterior complex of the thalamus (PO). The calibrated signals enter NeuroBridge through the documented lab HDF5 interface. [SpikeGLX metadata](https://billkarsh.github.io/SpikeGLX/More_help/Metadata_3B1.html) · [Allen structure 1020](https://api.brain-map.org/api/v2/data/Structure/1020.json)
2. **Fit the clocks.** Detect rising edges from the two native digital words using the published wiring, probe bit 6 and NIDQ bit 3. The IBL published mapping disambiguates the cycle of the approximately 1 Hz pulses. The fitter receives only the timestamps measured directly from the two streams, with **12** pairs used and **4** withheld. It maps wheel timestamps from NIDQ seconds to probe seconds and preserves the original wheel angles in radians and timestamps in seconds. [IBL probe synchronization](https://docs.internationalbrainlab.org/_modules/ibllib/ephys/sync_probes.html) · [IBL channel map](https://docs.internationalbrainlab.org/_modules/ibllib/io/extractors/ephys_fpga.html)
3. **Verify the reopened export.** The demo compares the NWB against raw LF counts scaled by the independent header formula, the original ALF wheel arrays and the withheld hardware observations. The observed maximum voltage difference was **5.42 × 10⁻²⁰ V**. The fit measured **+5.85 ppm** drift and **+1.503 ms** offset at its center, with **0.00948 ms** RMS inlier residual. The largest error on the four held-out edges was **0.0173 ms** against a **0.433 ms** allowance equal to one sample interval from each device combined. The small residual describes this excerpt and fit. Timing resolution remains limited by acquisition and edge detection.
4. **Stage the archive.** Strict NWB and scientific QC pass without exceptions. NWB Inspector reports four best-practice suggestions. DANDI 0.82.0 returns **0** with zero findings and the CC BY 4.0 source license passes the local license check. The draft is staged for local review. [IBL published archive and citation](https://doi.org/10.48324/dandi.000409/0.260309.1324)

## Scope and provenance

The published DANDI asset metadata supplies age, sex, species and session start. The example keeps that start as a metadata reference. Voltage and aligned wheel timestamps use original probe-file seconds, with the LF excerpt starting at 100 s. The relation of either file's zero to absolute wall time was not independently verified. The source record contains the exact byte ranges, raw channel and sample selection, upstream fingerprints and held-out observations. Its SHA-256 is written to the NWB provenance record. The output is a derived excerpt with eight selected LF channels and cropped wheel data. [Subject and session metadata](https://api.dandiarchive.org/api/assets/d3e8a94d-7f64-4ccb-9f46-ee9148cee05f/)

The preparation script decodes the fixed-size native clip in memory. NeuroBridge's HDF5 conversion reads that prepared input in bounded chunks. The sample does not include the full AP stream, video, or spike-sorted units.

To replay the integration test with the cache already present, run the following command.

```sh
NEUROBRIDGE_IBL_CACHE=.local/ibl-source python -m pytest -q tests/test_ibl.py
```
