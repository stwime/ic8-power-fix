"""Residual diagnostic for the shipped brake fit (fit_spindowns.py).

Re-runs the fit, integrates each segment forward, and plots residuals in
two ways:

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
    that R; an H(R) shift would fix it
  • residual ramps up as ω → 0 → missing Coulomb / over-strong viscous β
  • residual changes sign within a single R bucket  → model's curvature
    in τ(ω) is wrong (e.g. a fade the linear brake doesn't have)
"""
from __future__ import annotations

import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis"))

from fit_spindowns import (H_two, PARAM_NAMES, collect, integrate_segment,
                           run_fit)

OUT_DIR = ROOT / "analysis_out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    segments = collect()
    print(f"collected {len(segments)} segments")

    res = run_fit(segments, verbose=0)
    print("\nrefit params: " + "  ".join(
        f"{n}={v:.4g}" for n, v in zip(PARAM_NAMES, res.x)))
    _, w, R_h1, p1, R_h2, p2, _, _ = res.x

    # Group segments by R for paneling.
    by_R: dict[int, list[dict]] = defaultdict(list)
    for s in segments:
        by_R[s["R"]].append(s)
    R_buckets = sorted(by_R)

    # Compute residuals for each segment.
    enriched = []
    for s in segments:
        R = float(s["R"])
        t = s["t"]; omega = s["omega"]
        omega_pred = integrate_segment(R, t, float(omega[0]), res.x)
        if omega_pred is None:
            continue
        H = H_two(R, w, R_h1, p1, R_h2, p2)
        enriched.append(dict(R=int(s["R"]), t=t, omega=omega,
                             omega_pred=omega_pred, resid=omega - omega_pred,
                             H=H))
    # Cache per-R for plot.
    per_R: dict[int, list[dict]] = defaultdict(list)
    for e in enriched:
        per_R[e["R"]].append(e)

    # Per-R summary statistics.
    print(f"\n{'R':>4} {'n_seg':>6} {'n_samp':>7} {'H':>5} "
          f"{'mean_resid':>11} {'max|resid|':>11}")
    for R in R_buckets:
        es = per_R[R]
        n_samp = sum(len(e["t"]) for e in es)
        all_resid = np.concatenate([e["resid"] for e in es])
        print(f"{R:>4} {len(es):>6} {n_samp:>7} {es[0]['H']:>5.3f} "
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
        ax.set_title(f"R={R}  H={per_R[R][0]['H']:.3f}", fontsize=8)
        ax.set_xlabel("ω (rad/s)", fontsize=8)
        ax.set_ylabel("ω_data − ω_model (rad/s)", fontsize=8)
        ax.grid(alpha=0.3)
        ax.tick_params(labelsize=7)
    for k in range(len(R_buckets), rows * cols):
        axes[k // cols, k % cols].set_visible(False)
    fig.suptitle("Residuals vs ω, per R bucket  (fit_spindowns)",
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
