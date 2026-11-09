# EDF calibration regression

The PhysioNet EEGMMIDB S001R03 recording contains 20,000 frames across 64 EEG channels. The initial AI-assisted conversion used the gains and offsets supplied by SpikeInterface through Neo 0.14.6. PyNWB reported zero schema errors, but an independent PyEDFlib read disagreed with the reopened NWB voltages. The worst difference across 1,280,000 values was **0.537040 µV** at zero-based sample **6089** on channel **Fp1.** The source measured **600 µV** and the conversion returned **599.463 µV**. [PhysioNet recording](https://physionet.org/content/eegmmidb/1.0.0/S001/)

EDF maps the digital minimum and maximum to the declared physical endpoints. For a count `d`, the physical value is `p_min + (d − d_min) × (p_max − p_min) / (d_max − d_min)`. Neo 0.14.6 added one to the denominator, changing both the gain and offset. NeuroBridge derives calibration from the declared endpoints and uses SpikeInterface for bounded digital reads. The corrected round trip matched all **1,280,000** values with **0 V** observed difference. [EDF specification](https://www.edfplus.info/specs/edf.html) · [Calibration code](../src/neurobridge/edf.py) · [Independent validation](../src/neurobridge/validation.py)

The regression test checks known physical values on two channels with different units and a nonzero offset. Additional tests alter values, channel mapping, sample rate, or intervals in otherwise schema-valid NWB files and check that scientific validation detects the changes. [Regression tests](../tests/test_edf.py)

```sh
neurobridge fetch-demo .local/physionet
NEUROBRIDGE_REAL_EDF=.local/physionet/S001R03.edf python -m pytest -q tests/test_edf.py
```

A replay of the same EDF sample returned **599.462960 µV** with Neo 0.14.6 and **600 µV** with the upstream reader at revision `50a3e59`. The calibration guard and regression remain in place for the pinned release. [Upstream fix](https://github.com/NeuralEnsemble/python-neo/commit/8dbdfd835a8e870a11378aa776f07896a8211c40)
