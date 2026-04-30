"""R=0 pendulum-asymmetry check.

Hypothesis: at R=0 the brake is essentially absent, so the dominant
non-frictional torque is gravity acting on a mass-asymmetric crank/pedal
assembly. That should produce a sinusoidal modulation in ω(t) at the
crank-revolution frequency f_rev (one cycle per rev — heavy side) or 2·f_rev
(symmetric pedal weight imbalance).

For each R=0 segment:
  1. Plot ω(t) (windowed slope of cumulative angle).
  2. Overlay the two-term fit and mark the rev period at the start and end
     so the eye can compare to the rev cadence.
  3. Compute the residual r(t) = ω(t) - ω_fit(t).
  4. FFT |r(t)| (after Hann window) and look for a peak at f_rev.

Output: data/calibration/spindown_plots_v2/r0_pendulum.png
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_nrf_log import parse_log  # noqa: E402
from spindown_fit import find_clean_coastdowns, fit_decay  # noqa: E402
from spindown_fit_video import (LOG, VIDEO_CSV,
                                integrate_to_cumulative,
                                load_video_modpi)  # noqa: E402
from spindown_fit_video_twoterm import fit_two_term_segment  # noqa: E402
from video_segment_bounds import detect_segment_bounds, windowed_omega  # noqa: E402

OUT_PATH = (Path(__file__).resolve().parent.parent
            / "data/calibration/spindown_plots_v2/r0_pendulum.png")


def main():
    rows = parse_log(LOG)
    segs = find_clean_coastdowns(rows)
    t_v, ang_v = load_video_modpi(VIDEO_CSV)
    cum_all = integrate_to_cumulative(ang_v)

    panels = []
    per_R = {}
    for seg, R, term in segs:
        occ = per_R.get(R, 0); per_R[R] = occ + 1
        if R != 0:
            continue
        if occ == 0:
            # R=0 occ=0 is the pre-spindown terminator — skip.
            continue
        b = detect_segment_bounds(seg, t_v, cum_all, R=R)
        if b is None:
            continue
        i0, i1, t_stop, t_floor = b
        if i1 <= i0 + 20:
            continue
        tt = t_v[i0:i1 + 1]
        cum = cum_all[i0:i1 + 1] - cum_all[i0]
        omega = windowed_omega(tt, cum, 0.5)
        # Sign: R=0 spindowns are forward.
        s = np.sign(np.nanmean(omega))
        omega = s * omega
        cum = s * cum

        fcsc = fit_decay(seg)
        lam_seed = fcsc[0] if fcsc is not None else 0.05
        # Match v2: seed ω₀ from CSC's first-rev cadence so we don't blow
        # past the A bound in least_squares.
        c0 = fcsc[3] if fcsc is not None else 60.0
        omega0_seed = c0 * 2 * math.pi / 60
        f2 = fit_two_term_segment(tt, cum,
                                  lam0=max(lam_seed, 0.005),
                                  omega0=max(omega0_seed, 1.0))
        if f2 is None:
            continue
        lam2, A2, B2, off2, cost2 = f2
        sign2 = np.sign(A2) if A2 != 0 else 1.0
        t_rel = tt - tt[0]
        omega_fit = np.abs(A2 * lam2 * np.exp(-lam2 * t_rel) - sign2 * B2)
        resid = omega - omega_fit

        panels.append({
            "R": R, "occ": occ, "t_rel": t_rel,
            "omega": omega, "fit": omega_fit, "resid": resid,
            "lam2": abs(lam2), "tau0": B2 * lam2,
            "n": len(tt), "dur": float(t_rel[-1]),
        })

    if not panels:
        sys.exit("no R=0 panels")

    n = len(panels)
    fig, axes = plt.subplots(n, 3, figsize=(15, 3.2 * n))
    axes = np.atleast_2d(axes)

    for i, p in enumerate(panels):
        ax_om, ax_res, ax_fft = axes[i]
        t = p["t_rel"]; om = p["omega"]; fit = p["fit"]; res = p["resid"]
        rpm = om * 60.0 / (2 * math.pi)
        rpm_fit = fit * 60.0 / (2 * math.pi)

        ax_om.plot(t, rpm, 'k-', lw=0.8, label='video |ω| (rpm)')
        ax_om.plot(t, rpm_fit, 'C2-', lw=1.0, alpha=0.9,
                   label=f'2-term fit λ={p["lam2"]:.3f} τ₀={p["tau0"]:+.2f}')
        # Mark rev period at start, mid, end.
        for tau in [0.15, 0.5, 0.85]:
            j = int(tau * (len(t) - 1))
            if not np.isfinite(om[j]) or om[j] <= 0:
                continue
            T_rev = 2 * math.pi / om[j]
            ax_om.axvspan(t[j], t[j] + T_rev, color='C0',
                          alpha=0.10,
                          label=('1 rev' if tau == 0.5 else None))
        ax_om.set_xlabel('t (s)')
        ax_om.set_ylabel('rpm')
        ax_om.set_title(f"R=0 occ={p['occ']}  n={p['n']} dur={p['dur']:.1f}s")
        ax_om.grid(alpha=0.3)
        ax_om.legend(loc='upper right', fontsize=8)

        ax_res.plot(t, res, 'C3-', lw=0.7)
        ax_res.axhline(0, color='gray', lw=0.5)
        ax_res.set_xlabel('t (s)')
        ax_res.set_ylabel('ω − ω_fit  (rad/s)')
        ax_res.set_title('residual')
        ax_res.grid(alpha=0.3)

        # FFT residual.
        valid = np.isfinite(res)
        if valid.sum() < 32:
            ax_fft.set_visible(False)
            continue
        rv = res[valid] - np.nanmean(res[valid])
        # Window to suppress leakage.
        win = np.hanning(len(rv))
        rw = rv * win
        dt = float(np.median(np.diff(t[valid])))
        F = np.fft.rfft(rw)
        freqs = np.fft.rfftfreq(len(rw), d=dt)
        mag = np.abs(F)
        # Cap plot to 0..5 Hz (rev rates of 0..300 rpm).
        m_show = freqs <= 5.0
        ax_fft.plot(freqs[m_show], mag[m_show], 'C0-', lw=0.8)
        # Mark f_rev at start of segment (≈ peak rpm) and at half-segment.
        for tau, color, label in [(0.1, 'C2', 'f_rev @ start'),
                                  (0.5, 'C1', 'f_rev @ mid')]:
            j = int(tau * (len(t) - 1))
            if not np.isfinite(om[j]) or om[j] <= 0:
                continue
            f_rev = om[j] / (2 * math.pi)
            ax_fft.axvline(f_rev, color=color, lw=0.8, alpha=0.7,
                           label=f'{label}={f_rev:.2f} Hz')
            ax_fft.axvline(2 * f_rev, color=color, lw=0.6, ls='--',
                           alpha=0.5,
                           label=f'2·f_rev={2 * f_rev:.2f} Hz')
        ax_fft.set_xlabel('frequency (Hz)')
        ax_fft.set_ylabel('|FFT(resid)|')
        ax_fft.set_title('residual spectrum (Hann-windowed)')
        ax_fft.grid(alpha=0.3)
        ax_fft.legend(loc='upper right', fontsize=7)

    plt.suptitle("R=0 pendulum-asymmetry check.  "
                 "If gravity·m_offset is the cause, residual peaks at f_rev "
                 "(1 cycle per rev).", fontsize=11)
    plt.tight_layout(rect=[0, 0, 1, 0.97])
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(OUT_PATH, dpi=120)
    plt.close()
    print(f"wrote {OUT_PATH}  ({n} panels)")


if __name__ == "__main__":
    main()
