"""Phase-plot ω̇ vs ω for each video spindown.

This is the most assumption-free probe of the brake physics:
  - pure linear viscous brake: ω̇ = -λ(R)·ω → straight line through origin
  - linear + R-dependent extra torque: ω̇ = -λ(R)·ω - τ₀(R) → straight line
    with non-zero y-intercept
  - ω-dependent brake (power-law, eddy-current, etc.): the line curves

We plot one panel per (R, occ). Each panel shows raw (ω, ω̇) points from
windowed local-quadratic fits on the cumulative angle, plus an
unweighted least-squares line through them.

Why local-quadratic instead of two-pass linear: ω̇ is the second derivative
of the cumulative angle. Fitting a quadratic on a window of size W gives
both ω (linear coeff) and ω̇/2 (quadratic coeff) in one shot, no double
smoothing. We use a window of ~1 s — enough to suppress per-frame PCA
noise but short enough not to blur shape features at the 3-s spindown
timescale.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_nrf_log import parse_log  # noqa: E402
from spindown_fit import find_clean_coastdowns  # noqa: E402
from spindown_fit_video import (LAG, LOG, VIDEO_CSV,
                                integrate_to_cumulative,
                                load_video_modpi)  # noqa: E402
from spindown_fit_video_twoterm import segment_video_window  # noqa: E402

OUT_PATH = (Path(__file__).resolve().parent.parent
            / "data/calibration/spindown_plots/omega_dot_vs_omega.png")
WINDOW_S = 1.0  # local quadratic-fit window width


def local_quadratic(t: np.ndarray, cum: np.ndarray,
                    window_s: float
                    ) -> tuple[np.ndarray, np.ndarray]:
    """At each frame i, fit cum ≈ a + b·dt + c·dt² over a centred window
    of width ≈ window_s. Returns (omega, omega_dot) where:
        omega    = b   (rad/s)
        omega_dot = 2c (rad/s²)
    """
    n = len(t)
    omega = np.full(n, np.nan)
    omega_dot = np.full(n, np.nan)
    if n < 5:
        return omega, omega_dot
    for i in range(n):
        lo = i
        while lo > 0 and t[i] - t[lo - 1] < window_s / 2:
            lo -= 1
        hi = i
        while hi < n - 1 and t[hi + 1] - t[i] < window_s / 2:
            hi += 1
        if hi - lo < 5:
            continue
        ts = t[lo:hi + 1] - t[i]
        cs = cum[lo:hi + 1]
        # quadratic fit: returns [c, b, a] for c·t² + b·t + a
        c2, c1, _c0 = np.polyfit(ts, cs, 2)
        omega[i] = c1
        omega_dot[i] = 2 * c2
    return omega, omega_dot


def main():
    rows = parse_log(LOG)
    segs = find_clean_coastdowns(rows)
    t_v, ang_v = load_video_modpi(VIDEO_CSV)

    panels = []
    per_R = {}
    for seg, R, term in segs:
        occ = per_R.get(R, 0); per_R[R] = occ + 1
        if R == 0 and occ == 0:
            continue
        tt, aa = segment_video_window(seg, t_v, ang_v)
        if len(tt) < 12:
            continue
        cum = integrate_to_cumulative(aa)
        omega, omega_dot = local_quadratic(tt, cum, WINDOW_S)
        # Take absolute value of ω so all spindowns are in the same quadrant.
        # ω̇ for a decaying ω is negative (ω → 0), so flip its sign with the
        # rotation direction so we plot the decay direction consistently.
        sign = np.sign(np.nanmean(omega))
        omega *= sign
        omega_dot *= sign
        # Drop the first/last 0.25 s of the segment — windowed quadratic
        # at the boundary is one-sided and biased.
        edge = 0.25
        keep = (tt - tt[0] > edge) & (tt[-1] - tt > edge) & np.isfinite(omega) & np.isfinite(omega_dot)
        if keep.sum() < 8:
            continue
        panels.append({
            "R": R, "occ": occ,
            "omega": omega[keep],
            "omega_dot": omega_dot[keep],
            "n": int(keep.sum()),
            "term": term,
        })

    if not panels:
        sys.exit("no panels")

    panels.sort(key=lambda p: (p["R"], p["occ"]))
    n = len(panels)
    cols = 4
    rows_ = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows_, cols,
                             figsize=(4 * cols, 2.8 * rows_),
                             sharex=False, sharey=False)
    axes = np.atleast_2d(axes)

    for idx, pan in enumerate(panels):
        ax = axes[idx // cols, idx % cols]
        w = pan["omega"]
        wd = pan["omega_dot"]
        ax.plot(w, wd, 'k.', ms=3, alpha=0.5)
        # Linear LSQ fit through the (ω, ω̇) cloud; report slope and intercept.
        # If pure linear viscous → intercept ≈ 0, slope = -λ.
        if len(w) >= 3 and np.std(w) > 1e-3:
            slope, intercept = np.polyfit(w, wd, 1)
            xfit = np.linspace(0, max(w.max(), 1.0), 50)
            ax.plot(xfit, slope * xfit + intercept, 'C3-', lw=1,
                    label=f'fit: ω̇={slope:+.3f}ω{intercept:+.3f}')
            # Reference: pure-linear-viscous (intercept=0) at the same slope.
            ax.plot(xfit, slope * xfit, 'C0--', lw=0.8, alpha=0.5,
                    label=f'pure visc (slope only)')
        # Origin reference.
        ax.axhline(0, color='gray', lw=0.5, alpha=0.5)
        ax.axvline(0, color='gray', lw=0.5, alpha=0.5)
        ax.set_title(f"R={pan['R']} occ={pan['occ']} (n={pan['n']})",
                     fontsize=9)
        ax.set_xlabel('ω (rad/s)', fontsize=8)
        ax.set_ylabel('ω̇ (rad/s²)', fontsize=8)
        ax.tick_params(labelsize=8)
        ax.grid(alpha=0.3)
        ax.legend(loc='lower right', fontsize=7)

    for k in range(n, rows_ * cols):
        axes[k // cols, k % cols].set_visible(False)

    plt.suptitle(
        "ω̇ vs ω for each video spindown.  "
        "Pure linear viscous → straight line through origin. "
        "Curves or non-zero intercept → non-trivial brake.",
        fontsize=11)
    plt.tight_layout(rect=[0, 0, 1, 0.97])
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(OUT_PATH, dpi=120)
    plt.close()
    print(f"wrote {OUT_PATH} ({n} panels)")


if __name__ == "__main__":
    main()
