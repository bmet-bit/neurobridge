# NeuroBridge

NeuroBridge converts lab recordings to Neurodata Without Borders (NWB), reopens the result, and checks that the source measurements survived the conversion. It supports independent recording clocks and prepares NWB files for local DANDI validation.

| Capability | Demonstration |
| --- | --- |
| A · Ingestion | PhysioNet EDF+ EEG, a real IBL SpikeGLX excerpt, and HDF5 voltage with CSV position or wheel angle |
| B · Synchronization | Pulse-ID matching, robust affine drift fit, outlier and unmatched-pulse diagnostics, and guarded interpolation |
| C · Conversion | SpikeInterface and NeuroConv for EDF+, a custom NeuroConv `LabHDF5Interface`, and standard PyNWB containers |
| D · Validation | PyNWB schema, NWB Inspector, DANDI, and full source-to-output comparisons with corruption tests |
| E · Performance | Chunked conversion and scientific validation of every benchmark output, with conversion time, peak RSS and output size |
| F · Reporting | Standalone HTML QC and an executable notebook showing the real EEG and task intervals |
| G · Archive preparation | A real CC BY 4.0 IBL draft that passes local DANDI validation, plus a synthetic lab draft |

## Install

Python 3.11 or 3.12. The locked installation includes the notebook, archive, and development extras.

```sh
uv sync --locked --all-extras --no-editable
source .venv/bin/activate
```

For a pip installation, create a virtual environment and run `python -m pip install '.[archive,ibl,notebook,dev]'`. Direct dependencies are pinned in `pyproject.toml`. The uv lock also pins transitive dependencies and distribution hashes.

## Run the real recording

```sh
neurobridge fetch-demo .local/physionet
neurobridge inspect .local/physionet/session.json
neurobridge convert .local/physionet/session.json .local/physionet/demo.nwb
neurobridge validate .local/physionet/session.json .local/physionet/demo.nwb \
  --allow-missing-age --html .local/physionet/qc.html --json .local/physionet/qc.json
jupyter execute --timeout=120 --output ../.local/real_eeg examples/real_eeg.ipynb
```

Open the QC report in a browser or the executed notebook in Jupyter. The [notebook source](examples/real_eeg.ipynb) creates a new run directory on each execution. For other commands, use a fresh output path for each run.

The downloader retrieves only S001R03, verifies the pinned SHA-256, and records provenance. This run contains 20,000 frames across 64 channels at 160 Hz and 30 task intervals. NeuroBridge verified all 1,280,000 physical voltages with a measured maximum error of 0 V. The public data are attributed to Schalk and colleagues under ODC-BY-1.0. [PhysioNet dataset and citation](https://doi.org/10.13026/C28G6P)

The source lacks subject age and a timezone. The manifest labels the EDF wall time as UTC for serialization, and the report records that convention. The explicit missing-age flag permits local QC and retains the Inspector finding. Strict QC and archive validation flag the missing age. The real source license also falls outside the licenses accepted by the pinned DANDI schema. [DANDI data licenses](https://docs.dandiarchive.org/user-guide-sharing/data-licenses/)

## Run the real independent-clock and DANDI demo

```sh
python examples/ibl_demo.py .local/ibl-demo
```

The IBL example fetches about 26.2 MiB of pinned public data, including byte ranges from two SpikeGLX recordings. It aligns the NIDQ wheel clock to probe time using 12 measured TTL pairs, checks the fit against four withheld pairs, and verifies the reopened NWB against original voltages and wheel data. It stages a CC BY 4.0 draft and runs the DANDI CLI locally. Open `qc.html`, `original-checks.json`, and `dandi.json` in the output folder for the evidence. [IBL walkthrough and measured results](docs/IBL_DEMO.md)

## Run the synthetic lab bundle and archive check

```sh
neurobridge make-fixture .local/lab
neurobridge inspect .local/lab/session.json
neurobridge convert .local/lab/session.json .local/lab.nwb --buffer-frames 1024
neurobridge validate .local/lab/session.json .local/lab.nwb --html .local/lab.html
neurobridge stage-archive .local/lab/session.json .local/lab.nwb .local/draft
neurobridge validate-archive .local/draft --json .local/dandi-qc.json
```

The fixture contains deterministic synthetic voltage and irregular XY samples. It tests a +175 ppm clock scale error, a 50 ms outlier and unmatched pulses in both streams. An integration test checks the NWB timestamps against the fixture's known mapping independently of the fitter. Generated synthetic data are dedicated under CC0-1.0. The code is [MIT licensed](LICENSE).

`stage-archive` calls `dandi organize` in copy mode and repeats scientific QC on the staged file. It creates local draft metadata with a `LOCAL:000000` identifier by default. Supply `--metadata /path/to/dandiset.yaml` to stage with existing metadata. `validate-archive` runs DANDI locally and checks that the draft license matches embedded source provenance. Its verdict covers the local draft and assets. Registration and publication require the archive workflow. [DANDI organize](https://dandi.readthedocs.io/en/latest/cmdline/organize.html) · [DANDI validate](https://dandi.readthedocs.io/en/latest/cmdline/validate.html)

## Benchmark and test

```sh
neurobridge make-fixture .local/benchmark --frames 1048576 --channels 32
neurobridge benchmark .local/benchmark/session.json \
  --buffer-frames 1024 8192 --repeats 3 --json .local/benchmark.json
python -m ruff check .
python -m ruff format --check .
NEUROBRIDGE_REAL_EDF=.local/physionet/S001R03.edf python -m pytest -q
python -m build
```

Benchmarking supports Linux and macOS. It times each conversion in a fresh process and independently checks each result after the measured interval. The [performance notes](docs/BENCHMARKS.md) include measured results and what they cover. Omit `NEUROBRIDGE_REAL_EDF` to run the suite without the real recording. Exit status is 0 for success, 1 for a completed validation with blocking findings, and 2 for input or command errors.

See [architecture and input contracts](docs/ARCHITECTURE.md) for file formats, the clock model, and validation tolerances. The [EDF calibration case study](docs/EDF_CALIBRATION.md) shows how comparing against source voltages exposed a scaling error and how the regression test covers it. The [simulated lab handoff](docs/LAB_HANDOFF.md) contains an intake questionnaire, a metadata mapping, and a researcher review checklist. The [progress log](docs/PROGRESS.md) records executed milestones.
