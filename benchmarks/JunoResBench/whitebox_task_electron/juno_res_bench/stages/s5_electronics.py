"""Stage 5 — PMT electronics: PE -> charge -> waveform.

Reuses the vendored waverec snapshot for SPE charges, pulse shaping, FADC
digitization and noise (E1/E5/E6). Adds per-PMT gain (E2), dark noise (E4),
afterpulse hook (E3, enabled with stage-5 physics) and a trigger-defined
readout window (E8, trigger architecture v1).

Readout model (replaces the old t0-referenced fixed window):

1. Dark noise is generated on ALL PMT channels over an extended span
   [t0 - dark_span_pre, t0 + dark_span_post] before anything else — it is a
   real signal component, not post-hoc decoration.
2. A global trigger is formed from the total PE rate: physics + dark hit
   times are histogrammed in 1-ns bins and a sliding sum over
   `trigger_window_ns` is searched (within [t0-search_pre, t0+search_post])
   for the first bin where it reaches `trigger_threshold_pe`. That time is
   `t_trigger` (1-ns quantization).
3. The readout window starts pre_trigger_ns before t_trigger and is
   window_ns long in total (times stored relative to the window start);
   physics and dark PEs outside are truncated. Waveforms are referenced to
   the window start, so the observable consequence of the event time t0 is
   its offset from t_trigger — recoverable up to trigger/emission/TOF jitter.

Dark and afterpulse hits enter waveforms but stay out of the physics truth.

RNG draw order (s5 stream, fixed): per-channel dark counts -> dark times ->
[per synthesized channel in pmt_id order: dark amplitudes -> afterpulse
mask -> afterpulse delays -> afterpulse amplitudes]. Physics PE charges are
sampled before waveform synthesis (per-type blocks) so truth-only runs skip
all waveform draws.
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
    t0 = float(event.t0_ns)

    # ---- dark noise: all channels, extended span (E4) --------------------
    span_lo = t0 - cfg.dark_span_pre_ns
    span_hi = t0 + cfg.dark_span_post_ns
    mu_dark = cfg.dark_rate_hz * (span_hi - span_lo) * 1e-9
    n_dark_ch = rng.poisson(mu_dark, len(calib.gain))
    n_dark = int(n_dark_ch.sum())
    dark_t = rng.uniform(span_lo, span_hi, n_dark)
    dark_ch = np.repeat(np.arange(len(calib.gain), dtype=np.int64), n_dark_ch)

    # ---- global trigger: PE-rate sliding window (physics + dark) ---------
    # stages 1-4 keep t0-relative times (see truth.py); s5 owns the lab clock
    t_lab = s4.t_hit_ns.astype(np.float64) + t0
    t_trig = _find_trigger(np.concatenate([t_lab, dark_t]), t0, cfg)

    # ---- readout window ---------------------------------------------------
    window_start = t_trig - cfg.pre_trigger_ns
    t_rel = t_lab - window_start

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

    # in-window dark hits, grouped by channel for waveform synthesis
    dw = (dark_t >= window_start) & (dark_t < window_start + cfg.window_ns)
    dark_t_w = dark_t[dw] - window_start
    dark_ch_w = dark_ch[dw]

    adc = None
    if with_waveforms:
        adc = _synthesize_hits(
            ids, n_pe_pmt, t_rel_w, q_pe, dark_t_w, dark_ch_w, calib, cfg,
            wavegen, rng,
        )

    return {
        "pmt_ids": ids.astype(np.int32),
        "n_pe_pmt": n_pe_pmt,
        "pe_offsets": pe_offsets,
        "pe_type": pe_type_w.astype(np.int8),
        "t_rel_ns": t_rel_w,
        "q_pe": q_pe,
        "n_pe_total": int(in_window.sum()),
        "sel_idx": sel_idx,          # per kept PE, index into S4 arrays
        "t_trigger": t_trig,
        "adc": adc,
    }


def _find_trigger(times, t0, cfg: DetectorConfig) -> float:
    """First time the trailing PE-rate sum reaches threshold (causal).

    `times` (lab time, ns) includes physics and dark PEs. 1-ns histogram;
    the trigger quantity at bin m is the sum over the PAST
    `trigger_window_ns` (bins [m-k+1, m]) — an integrating shaper followed by
    a comparator, which can only fire at or after light arrives. Searched
    within [t0 - trigger_search_pre_ns, t0 + trigger_search_post_ns].
    Returns the time of the first qualifying bin (1-ns quantization); falls
    back to t0 if nothing crosses (not expected for MeV-scale events — the
    threshold sits >20 sigma above the pure-dark window sum).
    """
    lo = t0 - cfg.dark_span_pre_ns
    n_bins = int(round(cfg.dark_span_pre_ns + cfg.dark_span_post_ns))
    bins = np.floor(times - lo).astype(np.int64)
    bins = bins[(bins >= 0) & (bins < n_bins)]
    hist = np.bincount(bins, minlength=n_bins)
    k = int(round(cfg.trigger_window_ns))
    csum = np.concatenate(([0], np.cumsum(hist)))
    # endsum[m] = sum over bins [m-k+1, m] (causal trailing window)
    endsum = np.concatenate((np.zeros(k - 1, dtype=csum.dtype),
                             csum[k:] - csum[:-k]))

    i0 = max(k - 1, int(round(cfg.dark_span_pre_ns - cfg.trigger_search_pre_ns)))
    i1 = min(len(endsum), i0 + int(round(
        cfg.trigger_search_pre_ns + cfg.trigger_search_post_ns)))
    above = np.flatnonzero(endsum[i0:i1] >= cfg.trigger_threshold_pe)
    if len(above) == 0:
        return t0
    return lo + i0 + int(above[0])


def _synthesize_hits(ids, n_pe_pmt, t_rel, q_pe, dark_t, dark_ch, calib, cfg,
                     wavegen, rng):
    starts = np.concatenate(([0], np.cumsum(n_pe_pmt)))
    # dark hits grouped by channel (ids are sorted; dark_ch sorted to match)
    dord = np.argsort(dark_ch, kind="stable")
    dark_ch_s = dark_ch[dord]
    dark_t_s = dark_t[dord]
    adc = []
    for k, pmt in enumerate(ids):
        i0, i1 = int(starts[k]), int(starts[k + 1])
        times = [t_rel[i0:i1]]
        amps = [q_pe[i0:i1] * calib.gain[pmt]]

        j0 = np.searchsorted(dark_ch_s, pmt, "left")
        j1 = np.searchsorted(dark_ch_s, pmt, "right")
        n_dark_ch = j1 - j0
        if n_dark_ch:
            times.append(dark_t_s[j0:j1])
            amps.append(
                wavegen._sample_amplitudes(n_dark_ch, 1.0) * calib.gain[pmt]
            )

        # afterpulses (E3): per-PE delayed pulses, waveform only (not truth)
        if cfg.afterpulse_prob > 0:
            n_all = int(i1 - i0) + int(n_dark_ch)
            if n_all:
                all_t = np.concatenate(times)
                ap_mask = rng.random(n_all) < cfg.afterpulse_prob
                n_ap = int(ap_mask.sum())
                if n_ap:
                    t_ap = all_t[ap_mask] + rng.exponential(
                        cfg.afterpulse_tau_ns, n_ap)
                    in_win = t_ap < cfg.window_ns
                    n_ap_win = int(in_win.sum())
                    if n_ap_win:
                        times.append(t_ap[in_win])
                        amps.append(
                            wavegen._sample_amplitudes(n_ap_win, 1.0)
                            * calib.gain[pmt]
                        )
        adc.append(
            wavegen._synthesize(np.concatenate(times), np.concatenate(amps))
        )
    return adc
