"""v3: phase-locked spin-down fit.

v2 fits the full per-frame cumulative angle, which is corrupted at low R
by a sin(θ) gravity-pendulum term from crank/pedal mass asymmetry. That
term is conservative — it does net-zero work over each full revolution —
so sampling cum-angle only at integer multiples of 2π (rev marks)
removes it by construction, regardless of the heavy-side angle θ₀ or its
amplitude. This is exactly why CSC's per-rev λ is gravity-immune.

For each clean coastdown segment with video bounds (t_stop … t_floor):

  1. Build the full per-frame cumulative angle, sign-corrected so it
     monotonically increases over the segment.
  2. Find times t_k where cum crosses 2π·k for k = 1, 2, … (linear
     interpolation between adjacent video frames).
  3. Treat (t_k, 2π·k) as the data and fit the same one-term and
     two-term models as v2.

Sample count is `≈ duration × cadence`, which is plenty for low R
(many revs over 60+ s) and tight for high R (often only 2–3 revs in 2 s).
For high R we expect a near-identical λ to v2 since the pendulum term is
negligible against a strong brake; for low R we expect λ to drop toward
the CSC value.
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
           / "data/calibration/spindown_plots_v3")
TWO_PI = 2 * math.pi


def phase_lock_resample(tt: np.ndarray, cum: np.ndarray
                        ) -> tuple[np.ndarray, np.ndarray]:
    """Return (t_k, cum_k) at every full-rev mark of the segment.

    cum is assumed to start near 0 and to be monotonic in expectation
    (sign-corrected). We pick rev marks at multiples of 2π relative to
    the *minimum* cum value the segment reaches at its start (so we don't
    miss early revs to a small initial dip), and walk forward, linearly
    interpolating the time at each crossing.
    """
    if len(tt) < 4:
        return np.array([]), np.array([])
    c0 = float(cum[0])
    span = float(cum[-1] - c0)
    if span < TWO_PI:
        return np.array([]), np.array([])
    # k_max so that c0 + k_max·2π ≤ cum[-1]
    k_max = int(math.floor(span / TWO_PI))
    targets = c0 + TWO_PI * np.arange(1, k_max + 1)
    t_marks = []
    cum_marks = []
    j = 0
    n = len(tt)
    for tgt in targets:
        # Advance j until cum[j] >= tgt
        while j < n - 1 and cum[j + 1] < tgt:
            j += 1
        if j >= n - 1:
            break
        c_lo, c_hi = cum[j], cum[j + 1]
        if c_hi <= c_lo:
            continue
        frac = (tgt - c_lo) / (c_hi - c_lo)
        t_marks.append(float(tt[j] + frac * (tt[j + 1] - tt[j])))
        cum_marks.append(float(tgt))
    return np.array(t_marks), np.array(cum_marks)


def main():
    rows = parse_log(LOG)
    segs = find_clean_coastdowns(rows)
    t_v, ang_v = load_video_modpi(VIDEO_CSV)
    cum_v_all = integrate_to_cumulative(ang_v)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"{'R':>3} {'occ':>3} {'lam_csc':>8} "
          f"{'lam1_v2':>8} {'lam1_v3':>8} {'lam2_v2':>8} {'lam2_v3':>8} "
          f"{'tau0_v3':>8} {'n_rev':>5} {'dur_s':>6}")
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
        tt = t_v[i0:i1 + 1]
        cum_full = cum_v_all[i0:i1 + 1] - cum_v_all[i0]
        if len(tt) < 12:
            continue
        # Sign-correct so cum is monotonically increasing in expectation.
        s = np.sign(cum_full[-1] - cum_full[0])
        if s == 0:
            continue
        cum_full = s * cum_full

        t_rev, cum_rev = phase_lock_resample(tt, cum_full)
        if len(t_rev) < 4:
            continue

        # CSC reference λ.
        fcsc = fit_decay(seg)
        lam_c = fcsc[0] if fcsc is not None else float("nan")
        c0 = fcsc[3] if fcsc is not None else 60.0
        omega0_seed = c0 * 2 * math.pi / 60
        lam0 = max(lam_c if not math.isnan(lam_c) else 0.1, 0.01)

        # v2 fit on full cumulative trace (for comparison).
        f1_v2 = fit_one_term_segment(tt, cum_full, lam0=lam0, omega0=omega0_seed)
        f2_v2 = fit_two_term_segment(tt, cum_full, lam0=lam0, omega0=omega0_seed)
        # v3 fit on phase-locked cumulative trace.
        f1_v3 = fit_one_term_segment(t_rev, cum_rev, lam0=lam0,
                                     omega0=omega0_seed)
        f2_v3 = fit_two_term_segment(t_rev, cum_rev, lam0=lam0,
                                     omega0=omega0_seed)
        if any(f is None for f in (f1_v2, f2_v2, f1_v3, f2_v3)):
            continue

        lam1_v2 = abs(f1_v2[0]); lam2_v2 = abs(f2_v2[0])
        lam1_v3 = abs(f1_v3[0]); lam2_v3 = abs(f2_v3[0])
        A2_v3 = f2_v3[1]; B2_v3 = f2_v3[2]
        tau0_v3 = B2_v3 * lam2_v3
        dur = float(t_rev[-1] - t_rev[0])
        print(f"{R:>3} {occ:>3} {lam_c:>8.3f} "
              f"{lam1_v2:>8.3f} {lam1_v3:>8.3f} "
              f"{lam2_v2:>8.3f} {lam2_v3:>8.3f} "
              f"{tau0_v3:>+8.3f} {len(t_rev):>5} {dur:>6.2f}")
        rows_out.append({
            "R": R, "occ": occ, "lam_c": lam_c,
            "lam1_v2": lam1_v2, "lam2_v2": lam2_v2,
            "lam1_v3": lam1_v3, "lam2_v3": lam2_v3,
            "tau0_v3": tau0_v3,
            "tt_full": tt, "cum_full": cum_full,
            "t_rev": t_rev, "cum_rev": cum_rev,
            "f1_v3": f1_v3, "f2_v3": f2_v3,
            "f1_v2": f1_v2, "f2_v2": f2_v2,
            "n_rev": len(t_rev), "dur": dur,
        })

    # Per-segment plots.
    for r in rows_out:
        fig, (ax_cum, ax_om) = plt.subplots(2, 1, figsize=(8, 6), sharex=True)
        tt = r["tt_full"]; cum_full = r["cum_full"]
        t_rev = r["t_rev"]; cum_rev = r["cum_rev"]
        t0 = float(tt[0])
        t_full_rel = tt - t0
        t_rev_rel = t_rev - t0

        # v3 prediction.
        lam2 = abs(r["f2_v3"][0]); A2 = r["f2_v3"][1]; B2 = r["f2_v3"][2]
        off2 = r["f2_v3"][3]
        sign2 = np.sign(A2) if A2 != 0 else 1.0
        cum_pred = off2 + A2 * (1 - np.exp(-lam2 * t_full_rel)) - sign2 * B2 * t_full_rel

        ax_cum.plot(t_full_rel, cum_full, 'k.', ms=2, alpha=0.4,
                    label='video cum (per-frame)')
        ax_cum.plot(t_rev_rel, cum_rev, 'C3o', ms=4,
                    label=f'rev marks (n={len(t_rev)})')
        ax_cum.plot(t_full_rel, cum_pred, 'C2-', alpha=0.8,
                    label=f'v3 2-term λ={lam2:.3f} τ₀={r["tau0_v3"]:+.2f}')
        ax_cum.set_ylabel('cumulative θ (rad)')
        ax_cum.legend(loc='best', fontsize=9)
        ax_cum.grid(alpha=0.3)
        ax_cum.set_title(f"R={r['R']} occ={r['occ']}  "
                         f"n_rev={r['n_rev']} dur={r['dur']:.2f}s  "
                         f"λ_v2={r['lam2_v2']:.3f}→λ_v3={r['lam2_v3']:.3f}")

        # ω at rev marks: 2π / Δt (mid-rev cadence).
        if len(t_rev) >= 2:
            t_mid = 0.5 * (t_rev[:-1] + t_rev[1:]) - t0
            rpm_rev = 60.0 / np.diff(t_rev)
            ax_om.plot(t_mid, rpm_rev, 'C3o-', ms=4, lw=0.8,
                       label='per-rev cadence')
        # v3 ω prediction in rpm.
        omega_pred = np.abs(A2 * lam2 * np.exp(-lam2 * t_full_rel)
                            - sign2 * B2)
        rpm_pred = omega_pred * 60.0 / (2 * math.pi)
        ax_om.plot(t_full_rel, rpm_pred, 'C2-', alpha=0.8,
                   label='v3 2-term')
        ax_om.set_xlabel('t since stop (s)')
        ax_om.set_ylabel('rpm')
        ax_om.set_yscale('log')
        ax_om.set_ylim(1, 200)
        ax_om.grid(alpha=0.3, which='both')
        ax_om.legend(loc='best', fontsize=9)

        out = OUT_DIR / f"R{r['R']:03d}_occ{r['occ']}.png"
        plt.tight_layout()
        plt.savefig(out, dpi=110)
        plt.close()

    print(f"\nwrote {OUT_DIR}/  ({len(rows_out)} segments)")


if __name__ == "__main__":
    main()
