"""Toy detector forward model orchestrator: EventInput -> EventTruth.

Runs stages 1-5 (see docs/stage_design.md):

    Stage 1  particle response   E_true -> E_vis          (s1_response)
    Stage 2  photon generation   E_vis -> PhotonSoA       (s2_photons)
    Stage 3  optical response    photons -> arrivals      (s3_optics)
    Stage 4  photon detection    arrivals -> PEs          (s4_detection)
    Stage 5  PMT electronics     PEs -> waveforms         (s5_electronics)

One DetectorSim owns one seeded RNG stream set (rng.make_rngs) and one
per-detector DetectorCalibration; a given (config, seed) reproduces data
bit-for-bit.
"""

import numpy as np

from .config import DetectorConfig
from .geometry import PMTLayout
from .rng import make_rngs
from .truth import DetectorCalibration, EventInput, EventTruth, PhotonSoA
from .stages import s1_response, s2_photons, s3_optics, s4_detection, s5_electronics


def build_calibration(
    cfg: DetectorConfig, layout: PMTLayout, gain_spread: float, rng
) -> DetectorCalibration:
    """Per-PMT calibration truth, drawn once per detector (E2/D3 hooks)."""
    n = layout.n_pmt
    return DetectorCalibration(
        pde_delta=rng.normal(0.0, cfg.pde_sigma, n) if cfg.pde_sigma > 0 else np.zeros(n),
        gain=1.0 + gain_spread * rng.standard_normal(n) if gain_spread > 0 else np.ones(n),
        time_offset_ns=(
            rng.normal(0.0, cfg.time_offset_sigma_ns, n)
            if cfg.time_offset_sigma_ns > 0
            else np.zeros(n)
        ),
        tts_sigma_ns=np.full(n, cfg.tts_sigma_ns),
    )


class DetectorSim:
    """Seeded toy detector: stage pipeline + calibration + waverec bridge."""

    def __init__(
        self,
        config: DetectorConfig,
        layout: PMTLayout,
        seed: int = 0,
        wave_config=None,
    ):
        self.cfg = config
        self.layout = layout
        self.rngs = make_rngs(seed)

        from ._vendor.wavegen_v1 import WaveGenConfig
        from ._vendor.wavegen_v1.generator import WaveformGenerator

        self.wave_cfg = wave_config or WaveGenConfig()
        self.wavegen = WaveformGenerator(self.wave_cfg, seed=seed + 1)

        self.calib = build_calibration(
            config, layout, self.wave_cfg.gain_spread, self.rngs["calibration"]
        )
        self._dir_grid = None    # lazy DirectionGrid for trace mode

    def _get_dir_grid(self):
        if self._dir_grid is None:
            from .geometry import DirectionGrid
            self._dir_grid = DirectionGrid.for_layout(self.layout, n_theta=360)
        return self._dir_grid

    # ------------------------------------------------------------------
    def generate_event(self, event: EventInput,
                       with_waveforms: bool = True) -> EventTruth:
        cfg = self.cfg
        calib = self.calib
        s1 = s1_response.run_s1(event, cfg, self.rngs["s1_response"])
        photons = PhotonSoA.concatenate(
            [
                s2_photons.run_s2_scint(s1, event, cfg, self.rngs["s2_scint"]),
                s2_photons.run_s2_cherenkov(s1, event, cfg, self.rngs["s2_cherenkov"]),
            ]
        )
        if cfg.optics_mode == "trace":
            from .stages import s3_trace
            s3 = s3_trace.trace_photons(
                photons, event, cfg, self.layout, self.rngs["s3_optics"],
                grid=self._get_dir_grid(),
            )
        else:
            s3 = s3_optics.run_s3(
                photons, s1, event, cfg, self.layout, self.rngs["s3_optics"]
            )
        s4 = s4_detection.run_s4(
            s3, event, cfg, calib, self.rngs["s4_detection"],
            photon_type=photons.photon_type,
            photon_dir=photons.dir,
            layout=self.layout,
            photon_pos=(s1.steps.pos_m[photons.step_idx]
                        if s1.steps is not None else None),
        )
        s5 = s5_electronics.run_s5(
            s4, event, cfg, calib, self.wavegen, self.rngs["s5_electronics"],
            with_waveforms=with_waveforms,
        )

        # per-PE photon identity: S4 order -> S3 order -> PhotonSoA order
        pe_photon = s3.photon_idx[s4.arrived_idx[s5["sel_idx"]]]

        return EventTruth(
            x_m=event.x_m,
            y_m=event.y_m,
            z_m=event.z_m,
            direction=tuple(event.direction),
            e_true_mev=event.e_true_mev,
            e_dep_mev=s1.e_dep_mev,
            e_vis_mev=s1.e_vis_mev,
            n_gamma=int((photons.photon_type == 0).sum()),
            n_gamma_cher=int((photons.photon_type == 1).sum()),
            n_arrived=len(s3.pmt_idx),
            n_pe_produced=len(s4.pmt_idx),
            n_pe_total=s5["n_pe_total"],
            t0_ns=event.t0_ns,
            t_trigger_ns=s5["t_trigger"],
            pmt_ids=s5["pmt_ids"],
            n_pe_pmt=s5["n_pe_pmt"],
            pe_offsets=s5["pe_offsets"],
            pe_type=s5["pe_type"],
            t_emit_ns=photons.t_emit_ns[pe_photon],
            t_tof_ns=s3.t_tof_ns[s4.arrived_idx[s5["sel_idx"]]],
            t_rel_ns=s5["t_rel_ns"],
            q_pe=s5["q_pe"],
            adc=s5["adc"],
            adc_ids=s5["adc_ids"],
            particle_type=event.particle_type,
            e_escape_mev=s1.e_escape_mev,
            step_pos_m=s1.steps.pos_m if s1.steps is not None else None,
            step_e_dep_mev=s1.steps.e_dep_mev if s1.steps is not None else None,
            step_e_vis_mev=s1.steps.e_vis_mev if s1.steps is not None else None,
            step_t_ns=s1.steps.t_ns if s1.steps is not None else None,
            step_dir=s1.steps.dir if s1.steps is not None else None,
            step_kind=s1.steps.kind if s1.steps is not None else None,
            pe_step=(photons.step_idx[pe_photon]
                     if s1.steps is not None and len(photons.step_idx) else None),
        )

    # ------------------------------------------------------------------
    def generate(
        self,
        x_m: float,
        y_m: float,
        z_m: float,
        e_true_mev: float,
        t0_ns: float = 0.0,
        with_waveforms: bool = True,
        direction=(0.0, 0.0, 1.0),
        particle_type: "ParticleType" = None,
    ) -> EventTruth:
        """Backward-compatible convenience wrapper."""
        if particle_type is None:
            from .truth import ParticleType
            particle_type = ParticleType.ELECTRON
        return self.generate_event(
            EventInput(
                x_m=x_m, y_m=y_m, z_m=z_m,
                e_true_mev=e_true_mev, t0_ns=t0_ns,
                direction=tuple(direction),
                particle_type=particle_type,
            ),
            with_waveforms=with_waveforms,
        )
