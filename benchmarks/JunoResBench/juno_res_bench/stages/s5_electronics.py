"""Stage 5 — PMT electronics: PE -> charge -> waveform.

Reuses the vendored waverec snapshot for SPE charges, pulse shaping, FADC
digitization and noise (E1/E5/E6). Adds per-PMT gain (E2), dark noise (E4),
afterpulse hook (E3, enabled with stage-5 physics) and readout-window
truncation (E8). Dark and afterpulse hits enter waveforms but stay out of
the physics truth.
"""

import numpy as np

from ..config import DetectorConfig
from ..truth import DetectorCalibration, S4Output


def run_s5(
    s4: S4Output,
    event,
    cfg: DetectorConfig,
    calib: DetectorCalibration,
    wavegen,
    rng: np.random.Generator,
    with_waveforms: bool = True,
):
    """Returns dict with ragged truth (in-window only) + optional adc list."""
    window_start_rel = cfg.pre_trigger_ns   # window is [t0-pre, t0-pre+win]
    t_rel = s4.t_hit_ns.astype(np.float64) + window_start_rel

    in_window = (t_rel >= 0.0) & (t_rel < cfg.window_ns)
    pmt_idx_w = s4.pmt_idx[in_window]

    order = np.argsort(pmt_idx_w, kind="stable")
    pmt_idx_w = pmt_idx_w[order]
    sel_idx = np.where(in_window)[0][order]   # index into S4 arrays, per kept PE
    ids, counts = np.unique(pmt_idx_w, return_counts=True)
    n_pe_pmt = counts.astype(np.int32)
    pe_offsets = np.zeros(len(ids) + 1, dtype=np.int64)
    np.cumsum(counts, out=pe_offsets[1:])

    t_rel_w = t_rel[in_window][order]
    pe_type_w = s4.pe_type[in_window][order]
    # per-type amplitude blocks: keeps scintillation draws independent of the
    # Cherenkov chain (stream isolation, mirrors stage 4)
    q_pe = np.empty(int(in_window.sum()))
    for ptype in (0, 1):
        m = pe_type_w == ptype
        if m.any():
            q_pe[m] = wavegen._sample_amplitudes(int(m.sum()), 1.0)

    adc = None
    if with_waveforms:
        adc = _synthesize_hits(ids, n_pe_pmt, t_rel_w, q_pe, calib, cfg, wavegen, rng)

    return {
        "pmt_ids": ids.astype(np.int32),
        "n_pe_pmt": n_pe_pmt,
        "pe_offsets": pe_offsets,
        "pe_type": pe_type_w.astype(np.int8),
        "t_rel_ns": t_rel_w,
        "q_pe": q_pe,
        "n_pe_total": int(in_window.sum()),
        "sel_idx": sel_idx,          # per kept PE, index into S4 arrays
        "adc": adc,
    }


def _synthesize_hits(ids, n_pe_pmt, t_rel, q_pe, calib, cfg, wavegen, rng):
    dark_mean = cfg.dark_rate_hz * cfg.window_ns * 1e-9
    starts = np.concatenate(([0], np.cumsum(n_pe_pmt)))
    adc = []
    for k, pmt in enumerate(ids):
        i0, i1 = int(starts[k]), int(starts[k + 1])
        times = [t_rel[i0:i1]]
        amps = [q_pe[i0:i1] * calib.gain[pmt]]

        # afterpulses (E3): per-PE delayed pulses, waveform only (not truth)
        if cfg.afterpulse_prob > 0:
            n_pe_blk = i1 - i0
            ap_mask = rng.random(n_pe_blk) < cfg.afterpulse_prob
            n_ap = int(ap_mask.sum())
            if n_ap:
                t_ap = t_rel[i0:i1][ap_mask] + rng.exponential(
                    cfg.afterpulse_tau_ns, n_ap
                )
                in_win = t_ap < cfg.window_ns
                n_ap_win = int(in_win.sum())
                if n_ap_win:
                    times.append(t_ap[in_win])
                    amps.append(
                        wavegen._sample_amplitudes(n_ap_win, 1.0)
                        * calib.gain[pmt]
                    )

        n_dark = int(rng.poisson(dark_mean))
        if n_dark:
            times.append(rng.uniform(0.0, cfg.window_ns, n_dark))
            amps.append(wavegen._sample_amplitudes(n_dark, 1.0))
        adc.append(
            wavegen._synthesize(np.concatenate(times), np.concatenate(amps))
        )
    return adc
