# Conversion benchmark

Measured on macOS 26.4.1, arm64, Python 3.11.13, NeuroConv 0.10.2, PyNWB 4.2.0 and h5py 3.16.0. Inputs were deterministic synthetic int16 HDF5 voltage with 32 channels and matching CSV sidecars. Each row reports the median of three fresh worker processes. All 12 NWB outputs passed source-to-output QC.

| Input frames | Input MiB | Buffer frames | Median seconds | Median peak RSS MiB | Frames/s | Output MiB |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 65,536 | 4.01 | 1,024 | 0.125 | 137.36 | 525,139 | 2.83 |
| 65,536 | 4.01 | 8,192 | 0.126 | 138.62 | 519,084 | 2.83 |
| 1,048,576 | 64.04 | 1,024 | 1.456 | 166.25 | 720,239 | 42.21 |
| 1,048,576 | 64.04 | 8,192 | 1.490 | 168.03 | 703,524 | 42.10 |

```sh
neurobridge make-fixture .local/bench-small --frames 65536 --channels 32
neurobridge make-fixture .local/bench-large --frames 1048576 --channels 32
neurobridge benchmark .local/bench-small/session.json \
  --buffer-frames 1024 8192 --repeats 3 --json .local/bench-small.json
neurobridge benchmark .local/bench-large/session.json \
  --buffer-frames 1024 8192 --repeats 3 --json .local/bench-large.json
```

Elapsed time includes provenance hashing, alignment, compression and the NWB write. It excludes worker startup and validation, which runs after recording conversion time and peak RSS. Throughput counts time frames, each containing 32 samples. The worker reports the process-wide RSS high-water mark and the pre-conversion high-water mark, about 132 MiB in these runs.

The 1,024- and 8,192-frame settings pass 64 KiB and 512 KiB voltage blocks to the iterator respectively. Whole-process RSS also reflects libraries, HDF5 buffering, and output chunk metadata. The 16-fold increase in input size corresponded to a roughly 29 MiB increase in peak RSS at the smaller buffer. This comparison covers the two tested input sizes and configurations on this host. The benchmark uses local disk and can reflect filesystem caching. Its results depend on storage, signal compressibility, channel count, and dependency versions.
