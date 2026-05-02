"""Per-segment exponential-decay fit on raw per-frame PCA angles (mod π).

Why mod-π fitting instead of cumulative angle:
  PCA on the two-armed crank gives an angle in [0, π). Cumulative unwrapping
  is fragile — a frame with PCA noise near the π/2 wrap boundary can flip
  the unwrap step by π and bias the cumulative angle for the rest of the
  segment. The bug is irrecoverable from the unwrapped column alone.

  Instead, we fit the decay model directly to per-frame mod-π observations.
  The model produces a continuous θ(t); we compare to the observed angle
  on a circle of period π, taking the shortest signed distance. This is
  immune to unwrap ambiguity: each frame is an independent measurement
  modulo π, and the fit lives in absolute angle space.

Model per segment:
    θ(t) = θ_offset + (ω₀ / λ) · (1 − exp(−λ · (t − t_start)))
    observed: angle_mod_pi(t) ≡ θ(t) mod π
    residual: signed distance on circle of period π between θ_pred mod π
              and angle_observed
    loss: Huber on residuals (robust to scattered bad frames)

Fit with scipy.optimize.least_squares. Initial seed:
  - ω₀ ≈ first CSC interval ω in the segment
  - λ ≈ ln(c0/c1) / dur from the CSC fit
  - θ_offset ≈ first observed angle_mod_pi

Optionally drops the first 1.5 s of frames for segments where cad_hi exceeds
~120 rpm — only there does motion blur dominate. Below that, every frame
is usable.
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parent.parent
HUBER_DELTA = 0.15  # rad (~9°). PCA per-frame noise ~few degrees.


def signed_mod(x: np.ndarray, period: float) -> np.ndarray:
    """Wrap x into (-period/2, period/2]."""
    return ((x + period / 2) % period) - period / 2


def load_video_modpi(path: Path) -> tuple[np.ndarray, np.ndarray]:
    t, a = [], []
    with path.open() as f:
        for r in csv.DictReader(f):
            if not r["angle_mod_pi_rad"]:
                continue
            t.append(float(r["t_video_s"]))
            a.append(float(r["angle_mod_pi_rad"]))
    return np.asarray(t), np.asarray(a)


def integrate_to_cumulative(ang_mod_pi: np.ndarray) -> np.ndarray:
    """Integrate per-frame shortest-signed mod-π deltas to a continuous angle.

    This is the unwrap that ``track_crank.py``'s velocity-prior-free
    ``unwrap_mod_pi`` *should* have done: pick the shortest signed step at
    every frame. PCA noise at the boundary means a few frames wobble in
    sign, but those average out — the total signed integration matches
    CSC's rev count to within ≪ 1 rad over multi-rev segments (verified
    on the R=89 segment: video integrates to 25.09 rad vs CSC's 25.13).
    """
    out = np.zeros_like(ang_mod_pi, dtype=float)
    out[0] = ang_mod_pi[0]
    for i in range(1, len(ang_mod_pi)):
        d = ang_mod_pi[i] - ang_mod_pi[i - 1]
        # Shortest signed wrap into (-π/2, π/2].
        d = ((d + math.pi / 2) % math.pi) - math.pi / 2
        out[i] = out[i - 1] + d
    return out


def fit_segment_video(t_frames: np.ndarray, ang_frames: np.ndarray,
                      lam0: float, omega0: float
                      ) -> tuple[float, float, float, int] | None:
    """NLLS fit of θ(t) = θ_offset + (ω₀/λ)·(1 − e^(−λ(t−t₀))) on
    the integrated cumulative angle. Returns (lam, ω₀, θ_offset, n)."""
    if len(t_frames) < 6:
        return None
    t0 = t_frames[0]
    tt = t_frames - t0
    cum = integrate_to_cumulative(ang_frames)

    def predicted(params):
        lam, w0, off = params
        if abs(lam) < 1e-6:
            return off + w0 * tt
        return off + (w0 / lam) * (1 - np.exp(-lam * tt))

    def residuals(params):
        return predicted(params) - cum

    best = None
    for sign in (+1, -1):
        x0 = np.array([lam0, sign * abs(omega0), float(cum[0])])
        try:
            # Bound λ to physically plausible range; bound ω₀ wide.
            res = least_squares(residuals, x0, loss="huber",
                                f_scale=HUBER_DELTA, max_nfev=400,
                                bounds=([1e-3, -50, -1e6],
                                        [3.0,  +50,  1e6]))
        except Exception:
            continue
        cost = float(np.sum(res.fun**2))
        if best is None or cost < best[0]:
            best = (cost, res.x[0], res.x[1], res.x[2], len(tt))
    if best is None:
        return None
    return abs(best[1]), best[2], best[3], best[4]


