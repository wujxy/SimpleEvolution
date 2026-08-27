# JunoResBench — reconstruction task (white-box)

You are given digitized PMT waveforms from a JUNO-like liquid-scintillator
toy detector (single 20-inch MCP-PMT type, 17612 PMTs on a sphere of
R = 19.37 m). Each event is a single electron at an unknown position/energy/time.

## White-box setting

This is the white-box variant: the COMPLETE forward model that produced the
data ships with the package —

  - `juno_res_bench/`       the full detector simulator (numpy only;
                             stages 1-5: particle chain, photon generation,
                             optics, detection, electronics + trigger)
  - `generate_dataset.py`   command-line entry to generate labeled datasets

You may read, run and modify any of it. Typical uses:

  - generate unlimited synthetic events WITH ground truth under any seed of
    your choosing, to calibrate, validate or train your method;
  - build a forward likelihood and fit (E, vertex, t0) per test event;
  - derive features/weights from the known CE(theta), eps(r), per-PMT
    calibration and trigger models instead of estimating them from data.

The test set was produced by exactly this code with an unknown large random
seed (absent from this package; brute-force search is not the task).

train/val/test, the scorer and the metrics are byte-identical to the blind
variant — scores are directly comparable across the two.

## Readout

Per event you receive up to 192 digitized channel
waveforms (1 GSa/s, 14-bit, negative pulses on a positive baseline; the
stored channels are a random subset of the hit channels — the number of hit
channels per event is recoverable from `pmt_offsets`).

The readout window is defined by the detector's global trigger: sample 0 of
every waveform sits at (trigger time - 300 ns), and the window is
1000 ns long. The trigger fires on the event itself (a fixed charge
threshold on the summed detector rate), so its timing jitters event-by-event
with vertex position, light-collection statistics and dark noise. Waveforms
also contain uncorrelated dark-noise pulses; the per-PE truth arrays in
train/val cover physics photoelectrons only (dark pulses are in the
waveforms but not in the truth lists).

## Your task

From the waveforms alone, reconstruct per event:
  - visible energy E_rec (MeV)
  - vertex (x_rec, y_rec, z_rec) in meters (detector center = origin)
  - event time t0_rec in ns, **measured from the window start** (sample 0 =
    trigger time - 300 ns). This is the time the scintillation light
    was emitted; recovering it means correcting the trigger latency, which
    depends on the vertex — the two tasks are coupled.

## Data

  train.npz  waveforms + truth (calibrate on this)
  val.npz    waveforms + truth
  test.npz   waveforms only (scored; same event population)

Prediction format: an npz with keys E_rec, x_rec, y_rec, z_rec, t0_rec
(each length = number of test events). Score with:

  python3 evaluate.py --data <test truth> --pred prediction.npz

Metrics: energy resolution ((q84-q16)/2 of E_rec/E_ref — quantile width,
gamma escape tails count), vertex 68% resolution, timing resolution
((q84-q16)/2 of t0_rec - t0_ref, ns). Ranking: energy first, then
vertex, then timing.
