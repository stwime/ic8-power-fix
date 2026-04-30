"""Per-segment spin-down fit using video-detected bounds.

Replaces the CSC-rev-1 window in spindown_fit_video.py with the
video-detected (t_stop, t_floor) from video_segment_bounds.detect_segment_bounds.
This sidesteps:
  - the CSC-rev-1 vs. actual-stop delay (variable with R and pedal phase),
  - the global-LAG uncertainty,
since the video has its own internal CFR-accurate clock and we now have
a per-segment measurement of when the rider stopped pedaling and when
the bike came to rest.

Fits both single-exp ω(t) = ω₀·e^(-λt) and the two-term variant
ω̇ = -λ·ω - τ₀ on cumulative angle. Reports the per-segment cost
improvement and prints the resulting τ₀ values so we can re-test the
"non-linear brake" hypothesis with proper windowing.
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
from spindown_fit_video_twoterm import (fit_one_term_segment,
                                        fit_two_term_segment)  # noqa: E402
from video_segment_bounds import detect_segment_bounds  # noqa: E402

OUT_DIR = (Path(__file__).resolve().parent.parent
           / "data/calibration/spindown_plots_v2")


def main():
    rows = parse_log(LOG)
    segs = find_clean_coastdowns(rows)
    t_v, ang_v = load_video_modpi(VIDEO_CSV)
    cum_v_all = integrate_to_cumulative(ang_v)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"{'R':>3} {'occ':>3} {'lam_csc':>8} {'lam1':>7} {'lam2':>7} "
          f"{'tau0':>8} {'cost1':>9} {'cost2':>9} {'Δrss%':>7} "
          f"{'n':>5} {'dur_s':>6}")
    rows_out = []
    per_R = {}
    for seg, R, term in segs:
        occ = per_R.get(R, 0); per_R[R] = occ + 1
        if R == 0 and occ == 0:
            continue
        b = detect_segment_bounds(seg, t_v, cum_v_all, R=R)
        if b is None:
            continue
        i0, i1, t_stop, t_floor = b
        if i1 <= i0 + 5:
            continue
        # Trim a small initial buffer (50 ms) so the boundary smoothing
        # artifact doesn't seed the fit, and a small terminal buffer
        # (50 ms before bike-stop) so we don't fit pure-zero noise.
        tt = t_v[i0:i1 + 1]
        cum_seg = cum_v_all[i0:i1 + 1] - cum_v_all[i0]
        if len(tt) < 12:
            continue

        fcsc = fit_decay(seg)
        lam_c = fcsc[0] if fcsc is not None else float("nan")
        c0 = fcsc[3] if fcsc is not None else 60.0
        omega0_seed = c0 * 2 * math.pi / 60

        f1 = fit_one_term_segment(tt, cum_seg,
                                  lam0=max(lam_c if not math.isnan(lam_c) else 0.1, 0.01),
                                  omega0=omega0_seed)
        f2 = fit_two_term_segment(tt, cum_seg,
                                  lam0=max(lam_c if not math.isnan(lam_c) else 0.1, 0.01),
                                  omega0=omega0_seed)
        if f1 is None or f2 is None:
            continue
        lam1, w0_1, off1, c1_cost = f1
        lam2, A2, B2, off2, c2_cost = f2
        tau0 = B2 * lam2
        improvement = (c1_cost - c2_cost) / max(c1_cost, 1e-12) * 100
        dur = float(tt[-1] - tt[0])
        print(f"{R:>3} {occ:>3} {lam_c:>8.3f} {abs(lam1):>7.3f} "
              f"{abs(lam2):>7.3f} {tau0:>+8.3f} {c1_cost:>9.4f} "
              f"{c2_cost:>9.4f} {improvement:>+7.1f} {len(tt):>5} "
              f"{dur:>6.2f}")
        rows_out.append({
            "R": R, "occ": occ, "lam_c": lam_c,
            "lam1": abs(lam1), "lam2": abs(lam2), "tau0": tau0,
            "cost1": c1_cost, "cost2": c2_cost,
            "n": len(tt), "dur": dur,
            "tt": tt, "cum": cum_seg,
            "w0_1": w0_1, "off1": off1,
            "A2": A2, "B2": B2, "off2": off2,
        })

    # Per-segment plots: cumulative angle + ω(t) overlays.
    for r in rows_out:
        fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
        tt = r["tt"]; cum = r["cum"]
        t_rel = tt - tt[0]
        # ω from windowed slope, in rpm.
        from video_segment_bounds import windowed_omega
        omega = windowed_omega(tt, cum, 0.5)
        rpm = np.abs(omega) * 60.0 / (2 * math.pi)

        # Single-exp prediction.
        lam1 = r["lam1"]; w0_1 = r["w0_1"]; off1 = r["off1"]
        cum_pred1 = off1 + (w0_1 / max(lam1, 1e-6)) * (1 - np.exp(-lam1 * t_rel))
        omega_pred1 = abs(w0_1) * np.exp(-lam1 * t_rel)
        rpm_pred1 = omega_pred1 * 60.0 / (2 * math.pi)

        # Two-term prediction.
        lam2 = r["lam2"]; A2 = r["A2"]; B2 = r["B2"]; off2 = r["off2"]
        sign2 = np.sign(A2) if A2 != 0 else 1.0
        cum_pred2 = off2 + A2 * (1 - np.exp(-lam2 * t_rel)) - sign2 * B2 * t_rel
        omega_pred2 = np.abs(A2 * lam2 * np.exp(-lam2 * t_rel) - sign2 * B2)
        rpm_pred2 = omega_pred2 * 60.0 / (2 * math.pi)

        ax1.plot(t_rel, cum, 'k.', ms=2, alpha=0.6, label='video cum')
        ax1.plot(t_rel, cum_pred1, 'C0-', alpha=0.7,
                 label=f'1-term λ={lam1:.3f}')
        ax1.plot(t_rel, cum_pred2, 'C2-', alpha=0.7,
                 label=f'2-term λ={lam2:.3f} τ₀={r["tau0"]:+.2f}')
        ax1.set_ylabel('cumulative θ (rad)')
        ax1.legend(loc='best', fontsize=9)
        ax1.grid(alpha=0.3)
        ax1.set_title(f"R={r['R']} occ={r['occ']}  "
                      f"n={r['n']} dur={r['dur']:.2f}s  "
                      f"cost1={r['cost1']:.2f} cost2={r['cost2']:.2f}")

        ax2.plot(t_rel, rpm, 'k-', alpha=0.6, lw=0.8, label='video |ω|')
        ax2.plot(t_rel, rpm_pred1, 'C0-', alpha=0.7, label='1-term')
        ax2.plot(t_rel, rpm_pred2, 'C2-', alpha=0.7, label='2-term')
        ax2.set_xlabel('t since stop (s)')
        ax2.set_ylabel('rpm')
        ax2.set_yscale('log')
        ax2.set_ylim(1, 200)
        ax2.legend(loc='best', fontsize=9)
        ax2.grid(alpha=0.3, which='both')

        out = OUT_DIR / f"R{r['R']:03d}_occ{r['occ']}.png"
        plt.tight_layout()
        plt.savefig(out, dpi=110)
        plt.close()

    # Combined log-y rpm overview.
    rows_out.sort(key=lambda p: (p["R"], p["occ"]))
    n = len(rows_out)
    cols = 4
    rows_grid = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows_grid, cols,
                             figsize=(4 * cols, 2.6 * rows_grid))
    axes = np.atleast_2d(axes)
    from video_segment_bounds import windowed_omega
    for i, r in enumerate(rows_out):
        ax = axes[i // cols, i % cols]
        tt = r["tt"]; cum = r["cum"]; t_rel = tt - tt[0]
        omega = windowed_omega(tt, cum, 0.5)
        rpm = np.abs(omega) * 60.0 / (2 * math.pi)
        ax.plot(t_rel, rpm, 'k-', lw=0.8, alpha=0.7)
        # 1-term reference.
        lam1 = r["lam1"]; w0_1 = r["w0_1"]
        rpm_pred1 = abs(w0_1) * np.exp(-lam1 * t_rel) * 60.0 / (2 * math.pi)
        ax.plot(t_rel, rpm_pred1, 'C0--', lw=0.8, alpha=0.7,
                label=f'1-term λ={lam1:.2f}')
        # 2-term reference.
        lam2 = r["lam2"]; A2 = r["A2"]; B2 = r["B2"]
        sign2 = np.sign(A2) if A2 != 0 else 1.0
        omega_pred2 = np.abs(A2 * lam2 * np.exp(-lam2 * t_rel) - sign2 * B2)
        ax.plot(t_rel, omega_pred2 * 60.0 / (2 * math.pi),
                'C2-', lw=1.0, alpha=0.85,
                label=f'2-term τ₀={r["tau0"]:+.2f}')
        ax.set_yscale('log')
        ax.set_ylim(1, 200)
        ax.set_title(f"R={r['R']} occ={r['occ']} (n={r['n']})", fontsize=9)
        ax.set_xlabel('t (s)', fontsize=8); ax.set_ylabel('rpm', fontsize=8)
        ax.grid(alpha=0.3, which='both')
        ax.tick_params(labelsize=8)
        if i == 0:
            ax.legend(loc='upper right', fontsize=7)
    for k in range(n, rows_grid * cols):
        axes[k // cols, k % cols].set_visible(False)
    plt.suptitle(
        "v2: spindowns with video-detected bounds.  "
        "k=video, blue dashed=1-term, green=2-term.",
        fontsize=11)
    plt.tight_layout(rect=[0, 0, 1, 0.97])
    plt.savefig(OUT_DIR / "all_rpm_log.png", dpi=120)
    plt.close()
    print(f"\nwrote {OUT_DIR}/")


if __name__ == "__main__":
    main()
