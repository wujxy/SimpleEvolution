# JunoResBench — reconstruction task

You are given digitized PMT waveforms from a JUNO-like liquid-scintillator
toy detector (single 20-inch MCP-PMT type, 17612 PMTs on a sphere of
R = 19.37 m). Each event is an electron-like energy deposit at an
unknown position/energy/time.

Per event you receive up to 192 digitized channel waveforms
(1 GSa/s, 14-bit, negative pulses on a positive baseline; the stored channels
are a random subset of the hit channels — the number of hit channels per
event is recoverable from `pmt_offsets`).

## Your task

From the waveforms alone, reconstruct per event:
  - visible energy E_rec (MeV)
  - vertex (x_rec, y_rec, z_rec) in meters (detector center = origin)
  - event time t0_rec (ns; the readout window starts at t0 - 300 ns)

## Data

  train.npz  waveforms + truth (calibrate on this)
  val.npz    waveforms + truth
  test.npz   waveforms only (scored)

Prediction format: an npz with keys E_rec, x_rec, y_rec, z_rec, t0_rec
(each length = number of test events). Score with:

  python3 evaluate.py --data <test truth> --pred prediction.npz

Metrics: energy resolution (std of E_rec/E_true), vertex 68% resolution,
timing resolution. Ranking: energy first, then vertex, then timing.
