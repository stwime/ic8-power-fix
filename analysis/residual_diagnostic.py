"""Residual diagnostic for the current H_geom + cam-warp fit.

Re-runs fit_geom_hill (so the params here match what's in fit_geom_hill's
own console output), integrates each segment forward, and plots residuals
in two ways:

  1. ω_data − ω_model vs ω_data, faceted per-R: shows what shape correction
     the data wants for τ(ω) at each R bucket.
  2. ω_data − ω_model vs t, faceted per-R: shows when in the spin-down the
     misfit is concentrated (early/middle/late).

Reading guide
-------------
  + residual = data above model = model is over-decelerating there
  − residual = data below model = model is under-decelerating there

Shape of residual vs ω at one R tells us what τ(ω) correction is needed:
  • residual peaks at high ω, fades to 0 at low ω  → model has too much
    damping at high ω (or missing ω² windage in the residual drag, opposite
    sign), depending on R
  • residual roughly constant in ω across the middle of a single segment
    → model's linear-regime damping coefficient is just slightly off at
    that R; a κ or H(R) shift would fix it
  • residual ramps up as ω → 0 → missing Coulomb / over-strong viscous β
  • residual changes sign within a single R bucket  → model's curvature
    in τ(ω) is wrong (bell falloff vs plateau, etc.)
"""
from __future__ import annotations

import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis"))

from fit_geom_hill import (collect, integrate_segment, run_fit,
                           H_geom_warped, I_CRANK)

OUT_DIR = ROOT / "analysis_out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    segments = collect()
    print(f"collected {len(segments)} segments")

    # Re-fit so we have current params in scope. Cheap (~3 s).
    x0 = np.array([109.0, 0.07, 0.04, 1.24, 1.5])
    res = run_fit(segments, x0)
    alpha, kappa, beta, q, tau_c = res.x
    print(f"\nrefit params: α={alpha:.3f}  κ={kappa:.4f}  "
          f"β={beta:.4f}  q={q:.3f}  τ_c={tau_c:.4f}")

    # Group segments by R for paneling.
    by_R: dict[int, list[dict]] = defaultdict(list)
    for s in segments:
        by_R[s["R"]].append(s)
    R_buckets = sorted(by_R)

    # Compute residuals + saturation x for each segment.
    enriched = []
    for s in segments:
        R = float(s["R"])
        t = s["t"]; omega = s["omega"]
        omega_pred = integrate_segment(R, t, float(omega[0]), res.x)
        if omega_pred is None:
            continue
        H = H_geom_warped(R, q)
        x = kappa * H * omega
        enriched.append(dict(R=int(s["R"]), t=t, omega=omega,
                             omega_pred=omega_pred, resid=omega - omega_pred,
                             x_sat=x, H=H))
    # Cache per-R for plot.
    per_R: dict[int, list[dict]] = defaultdict(list)
    for e in enriched:
        per_R[e["R"]].append(e)

    # Per-R summary statistics.
    print(f"\n{'R':>4} {'n_seg':>6} {'n_samp':>7} {'H':>5} "
          f"{'x_max':>6} {'x_mean':>7} {'mean_resid':>11} "
          f"{'max|resid|':>11}")
    for R in R_buckets:
        es = per_R[R]
        n_samp = sum(len(e["t"]) for e in es)
        all_resid = np.concatenate([e["resid"] for e in es])
        all_x = np.concatenate([e["x_sat"] for e in es])
        print(f"{R:>4} {len(es):>6} {n_samp:>7} {es[0]['H']:>5.3f} "
              f"{all_x.max():>6.3f} {all_x.mean():>7.3f} "
              f"{all_resid.mean():>+11.4f} {np.abs(all_resid).max():>11.4f}")

    # --- Panel 1: residual vs ω, faceted by R --------------------------------
    cols = 4
    rows = (len(R_buckets) + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(3.4 * cols, 2.4 * rows),
                             sharex=False, sharey=False)
    axes = np.atleast_2d(axes)
    for i, R in enumerate(R_buckets):
        ax = axes[i // cols, i % cols]
        for e in per_R[R]:
            ax.plot(e["omega"], e["resid"], ".", ms=2.0, alpha=0.6,
                    color="C0")
        ax.axhline(0, color="k", lw=0.6, alpha=0.5)
        ax.set_title(f"R={R}  H={per_R[R][0]['H']:.3f}  "
                     f"x_max={max(e['x_sat'].max() for e in per_R[R]):.2f}",
                     fontsize=8)
        ax.set_xlabel("ω (rad/s)", fontsize=8)
        ax.set_ylabel("ω_data − ω_model (rad/s)", fontsize=8)
        ax.grid(alpha=0.3)
        ax.tick_params(labelsize=7)
    for k in range(len(R_buckets), rows * cols):
        axes[k // cols, k % cols].set_visible(False)
    fig.suptitle("Residuals vs ω, per R bucket  (current fit_geom_hill)",
                 fontsize=11, weight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.975])
    out = OUT_DIR / "residuals_vs_omega.png"
    fig.savefig(out, dpi=130)
    plt.close()
    print(f"wrote {out}")

    # --- Panel 2: residual vs t, faceted by R --------------------------------
    fig, axes = plt.subplots(rows, cols, figsize=(3.4 * cols, 2.4 * rows),
                             sharex=False, sharey=False)
    axes = np.atleast_2d(axes)
    for i, R in enumerate(R_buckets):
        ax = axes[i // cols, i % cols]
        for e in per_R[R]:
            ax.plot(e["t"], e["resid"], "-", lw=0.8, alpha=0.7)
        ax.axhline(0, color="k", lw=0.6, alpha=0.5)
        ax.set_title(f"R={R}", fontsize=8)
        ax.set_xlabel("t (s)", fontsize=8)
        ax.set_ylabel("residual (rad/s)", fontsize=8)
        ax.grid(alpha=0.3)
        ax.tick_params(labelsize=7)
    for k in range(len(R_buckets), rows * cols):
        axes[k // cols, k % cols].set_visible(False)
    fig.suptitle("Residuals vs t, per R bucket",
                 fontsize=11, weight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.975])
    out = OUT_DIR / "residuals_vs_t.png"
    fig.savefig(out, dpi=130)
    plt.close()
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
