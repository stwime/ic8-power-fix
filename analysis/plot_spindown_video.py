"""Per-segment comparison plots: raw mod-π angles vs. CSC and video model fits.

For each spin-down segment in the 19:37 log, makes a 2-panel PNG:
  top:    angle_mod_pi observations + CSC-implied curve (wrapped) +
          video-fit curve (wrapped)
  bottom: ω derived from the same two fits (no per-frame ω points — those
          live in the angle_unwrapped column which we don't trust)
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_nrf_log import parse_log  # noqa: E402
from spindown_fit import find_clean_coastdowns, fit_decay  # noqa: E402
from spindown_fit_video import (LAG, LOG, VIDEO_CSV, fit_segment_video,
                                integrate_to_cumulative,
                                load_video_modpi)  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent.parent / "data/calibration/spindown_plots"


def plot_segment(seg, R, occ, t_v, ang_v):
    fcsc = fit_decay(seg)
    if fcsc is None:
        return
    lam_c, r2_c, n_csc, c0, c1, dur = fcsc
    # Match the spindown_fit_video logic: start at the second crank rev event.
    E0 = T0 = E1 = None
    for r in seg:
        ts = r["timestamp_s"]; et = r.get("crank_event_time_s")
        if ts is None or et is None:
            continue
        if E0 is None:
            E0, T0 = float(et), float(ts)
        elif float(et) > E0 + 1e-6:
            E1 = float(et); break
    t0_log = (E1 + (T0 - E0)) if E1 is not None else seg[0]["timestamp_s"]
    t1_log = seg[-1]["timestamp_s"]
    tv0, tv1 = t0_log - LAG, t1_log - LAG
    m = (t_v >= tv0) & (t_v <= tv1)
    tt = t_v[m]; aa = ang_v[m]
    if len(tt) < 6:
        return
    cum = integrate_to_cumulative(aa)

    omega0_seed = c0 * 2 * math.pi / 60
    out = fit_segment_video(tt, aa, lam0=max(lam_c, 0.01), omega0=omega0_seed)
    if out is None:
        return
    lam_v, w0_v, off_v, _ = out

    # Build CSC's implied curve. CSC fit gave λ at midpoint times. Use ω₀
    # ≈ c0·2π/60 starting at the segment's first FTMS row.
    t_grid = np.linspace(tt[0], tt[-1], 600)
    t_rel = t_grid - tt[0]
    # CSC model (using cad_hi as ω₀): θ(t) = θ₀ + (ω₀/λ)(1 − e^(−λt))
    # The CSC curve's θ_offset is unknown (CSC reports counts, not phase),
    # so fit it visually by aligning the CSC curve's median residual to zero
    # against the observations.
    omega0_csc = c0 * 2 * math.pi / 60
    sign_csc = np.sign(w0_v) if w0_v != 0 else 1
    theta_csc = sign_csc * (omega0_csc / max(lam_c, 1e-6)) * (
        1 - np.exp(-lam_c * t_rel))
    # Same offset trick for the CSC curve.
    pred_at_obs = sign_csc * (omega0_csc / max(lam_c, 1e-6)) * (
        1 - np.exp(-lam_c * (tt - tt[0])))
    diffs = (pred_at_obs - aa + np.pi / 2) % math.pi - np.pi / 2
    csc_off = -float(np.median(diffs))
    theta_csc += csc_off

    # Video fit curve.
    theta_vid = off_v + (w0_v / lam_v) * (1 - np.exp(-lam_v * t_rel))

    fig, (ax1, ax2, ax3) = plt.subplots(3, 1, figsize=(10, 9), sharex=True)
    ax1.plot(tt - tt[0], aa, 'k.', ms=2, alpha=0.6, label='video angle (mod π)')
    ax1.plot(t_rel, np.mod(theta_csc, math.pi), 'C0-', alpha=0.7, lw=1.2,
             label=f'CSC fit  λ={lam_c:.3f}/s')
    ax1.plot(t_rel, np.mod(theta_vid, math.pi), 'C3-', alpha=0.7, lw=1.2,
             label=f'video fit  λ={lam_v:.3f}/s')
    ax1.set_ylabel('θ mod π (rad)')
    ax1.set_title(f'R={R} occ={occ}  '
                  f'(c0={c0:.0f}rpm, c1={c1:.0f}rpm, dur={dur:.1f}s, '
                  f'n_csc={n_csc}, n_vid={len(tt)})')
    ax1.legend(loc='best', fontsize=8)
    ax1.grid(alpha=0.3)
    ax1.set_ylim(0, math.pi)

    # Cumulative angle: video integration vs both fits' predicted curves.
    ax3.plot(tt - tt[0], cum - cum[0], 'k.', ms=2, alpha=0.6,
             label='video cumulative (integrated)')
    csc_cum = sign_csc * (omega0_csc / max(lam_c, 1e-6)) * (
        1 - np.exp(-lam_c * t_rel))
    vid_cum = (w0_v / lam_v) * (1 - np.exp(-lam_v * t_rel))
    # Align endpoints visually.
    ax3.plot(t_rel, csc_cum, 'C0-', alpha=0.7, lw=1.2, label='CSC fit')
    ax3.plot(t_rel, vid_cum, 'C3-', alpha=0.7, lw=1.2, label='video fit')
    ax3.set_ylabel('cumulative θ (rad)')
    ax3.legend(loc='best', fontsize=8)
    ax3.grid(alpha=0.3)

    omega_csc_curve = sign_csc * omega0_csc * np.exp(-lam_c * t_rel)
    omega_vid_curve = w0_v * np.exp(-lam_v * t_rel)
    ax2.plot(t_rel, np.abs(omega_csc_curve), 'C0-', label='CSC fit |ω|')
    ax2.plot(t_rel, np.abs(omega_vid_curve), 'C3-', label='video fit |ω|')
    ax2.set_ylabel('|ω| (rad/s)')
    ax2.legend(loc='best', fontsize=8)
    ax2.grid(alpha=0.3)
    ax3.set_xlabel('t since segment start (s)')
    plt.tight_layout()
    out_path = OUT_DIR / f"R{R:03d}_occ{occ}.png"
    plt.savefig(out_path, dpi=110)
    plt.close()
    print(f"  wrote {out_path.name}  λ_csc={lam_c:.3f} λ_vid={lam_v:.3f}")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = parse_log(LOG)
    segs = find_clean_coastdowns(rows)
    t_v, ang_v = load_video_modpi(VIDEO_CSV)
    per_R = {}
    for seg, R, term in segs:
        occ = per_R.get(R, 0); per_R[R] = occ + 1
        if R == 0 and occ == 0:
            continue
        plot_segment(seg, R, occ, t_v, ang_v)


if __name__ == "__main__":
    main()
