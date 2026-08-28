"""Load a JunoResBench dataset split — single npz or dir format.

dir format (the full-readout era):

    <split>/meta.json    shipped meta (no seed, no detector_config)
    <split>/data.npz     every array except adc (labels, ragged truth,
                         wf_offsets, adc_pmt_ids, t_run_s, ...)
    <split>/adc.npy      uint16 [n_rows, n_samples] — memmap-able, so
                         multi-GB waveform sets never sit in RAM

npz format (frozen v1 contract) is a single compressed file; load_split
returns the same dict shape for both:

    {"meta": dict|None, "adc": ndarray|None, **arrays}
"""

import json
from pathlib import Path

import numpy as np


def load_split(path, mmap_adc: bool = True) -> dict:
    p = Path(path)
    if p.is_dir():
        meta = None
        mf = p / "meta.json"
        if mf.exists():
            meta = json.loads(mf.read_text(encoding="utf-8"))
        z = np.load(p / "data.npz", allow_pickle=False)
        out = {k: z[k] for k in z.files}
        adc = np.load(p / "adc.npy",
                      mmap_mode="r" if mmap_adc else None)
        return {"meta": meta, "adc": adc, **out}
    z = np.load(p, allow_pickle=False)
    out = {k: z[k] for k in z.files}
    meta = json.loads(str(out.pop("meta"))) if "meta" in out else None
    return {"meta": meta, "adc": out.pop("adc", None), **out}
