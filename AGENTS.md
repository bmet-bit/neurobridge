# NeuroBridge

NeuroBridge converts neuroscience recordings to NWB with NeuroConv and checks scientific fidelity after reopening the result. Start with [the architecture](docs/ARCHITECTURE.md) and [the milestone log](docs/PROGRESS.md).

- Keep data contracts explicit. Document units, axis order, clock origin, channel order, metadata assumptions and tolerances.
- Verify dependency APIs against the pinned installation and official documentation. Run `python -m pytest` and `python -m ruff check .` before committing.
- Pair each conversion path with source-to-output tests. Corrupt otherwise readable NWB output to exercise scientific failure cases. Keep real-data provenance separate from synthetic tests and benchmarks.
- Store downloaded recordings and generated outputs in `.local/`. Keep data, credentials and generated archives out of Git.
- Update the milestone log with actual commands, results, limitations and next steps before each milestone commit.
- Obtain explicit user authorization for any publishing, upload, pull request or external account action.
