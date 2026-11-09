# Simulated lab intake and handoff

A lab handoff records the source files, scientific assumptions and checks a researcher needs to review before accepting the conversion. This example uses the generated NeuroBridge lab bundle. The scenario and measurements in the fixture are synthetic.

## Intake scenario

A researcher provides `voltage.h5`, `position.csv` and pulse exports from the neural acquisition and tracking devices. Their note says that timestamps are “seconds since start.” The neural recording begins after the session origin. The tracking clock has its own origin and drift. Matching pulse identifiers are the evidence linking the clocks.

### Questions for the researcher

1. What event defines zero in each clock? Did either device restart, pause, or change sample rate?
2. Which signal and edge produced the pulse timestamps? Do matching IDs identify the same physical pulse? Are any dropped or duplicated?
3. What are the neural data units, sample rate and channel order? Which calibration and electrode location belong to each column?
4. What coordinate frame and units does tracking use? How are missed detections or invalid values represented?
5. Which subject ID, age or birth date, species, sex and timezone should appear in NWB? Who can confirm the redistribution license and citation?

## Agreed mapping

| Source | Confirmed interpretation for this fixture | NWB destination |
|---|---|---|
| `/voltage`, `/channel_labels` | Frames by channels in µV, 1,000 Hz, with sample zero at session time **0.25 s**. Channel labels follow voltage column order. | `acquisition/NeuralVoltage` and referenced electrode rows |
| `time_s,x_m,y_m` in position CSV | Device-relative timestamps and planar XY coordinates in meters. Preserve source order and coordinates. | `processing/behavior/Position/AlignedPosition` |
| Tracking clock `time_s` | Original values retained in seconds. The aligned timestamps link to the position series. | `processing/behavior/BehaviorClock` |
| Both `pulse_id,time_s` CSVs | Match by ID. Use neural-clock seconds for NWB timestamps. Keep residuals and unmatched IDs for review. | `scratch/neurobridge_clock_fit` |
| Session JSON and input hashes | Source attribution, units, timing convention, subject metadata, synthetic status and CC0-1.0 fixture license. | NWB session and subject fields and `scratch/neurobridge_provenance` |

## Issues and decisions

| Issue | Owner | Disposition in the simulation |
|---|---|---|
| Ambiguous “seconds since start” | Acquisition operator | Confirm separate origins. The fixture's expected mapping is `neural_s = 1.000175 × behavior_s − 0.125`. Use neural time in the output and retain the tracking clock. |
| One unmatched pulse on each clock and a 50 ms residual | Acquisition operator and converter reviewer | Review the IDs and residual. In this fixture they were deliberately injected. The converter fits eight paired pulses, flags the outlier and lists both unmatched IDs. |
| Position samples outside verified pulse coverage | Researcher | Supply additional matching pulses or approve an explicitly cropped interval. The converter rejects extrapolation. |
| Metadata and redistribution | Lab data steward | Verify real subject metadata, ownership and the intended archive license before a real submission. The generated fixture carries synthetic metadata and CC0-1.0. |

## Researcher handoff

```sh
neurobridge make-fixture .local/handoff/inputs
neurobridge inspect .local/handoff/inputs/session.json
neurobridge convert .local/handoff/inputs/session.json .local/handoff/session.nwb
neurobridge validate .local/handoff/inputs/session.json .local/handoff/session.nwb \
  --html .local/handoff/qc.html --json .local/handoff/qc.json
neurobridge stage-archive .local/handoff/inputs/session.json \
  .local/handoff/session.nwb .local/handoff/draft
neurobridge validate-archive .local/handoff/draft --json .local/handoff/dandi.json
```

Return the NWB, the original manifest, the two QC reports and the approved decisions table. Review the plotted voltage, check the reported drift against **+175 ppm**, confirm the **50 ms** outlier and check the unmatched IDs. Sign off on the clock origin, units and electrode order before approving the derived timestamps for analysis. The tests independently check the fixture's alignment and deliberately alter NWB timestamps to demonstrate detection. [Lab integration tests](../tests/test_lab.py)
