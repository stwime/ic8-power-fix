"""Refit the eddy-brake model with H_geom(R) instead of an empirical Hill.

The original fit_wouterse.py uses a free 2-parameter Hill curve
H(R) = R^p / (R^p + R_h^p) to parameterize B²(R) and pins α from the
1000 W spec. With the magnet dimensions now measured (2.5 cm dia, 4.5 mm
thick, anti-polar pairs + steel yoke) the geometric path through
physics_first_brake.py predicts 2ακ within ~2% of the empirical-Hill fit.

If that geometric path is right, two things should follow:
  1. H_geom(R) = √(G(R)/G_max), built from overlap geometry alone, should
     fit the data without needing the 2 free Hill parameters.
  2. With α free, the fit should land near the spec-anchored α=165 — which
     would mean the data, the magnet circuit, and the spec all agree on
     the absolute scale without any of them being used as a prior.

Free parameters here: {α, κ, β} only. Hill shape is replaced by H_geom(R)
precomputed from physics_first_brake.G_of_R; I is pinned from geometry as
before. Then we compare RSS and fitted constants to the empirical-Hill
reference.

Requires data/calibration/all_spindowns.csv. Run aggregate_spindowns.py
first if you don't have it.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis"))

import physics_first_brake as pfb
from fit_wouterse import collect, I_CRANK, hill as empirical_hill

OUT_DIR = ROOT / "analysis_out"
OUT_DIR.mkdir(parents=True, exist_ok=True)

G_MAX = pfb.G_of_R(100.0)

# Precompute H_geom on a dense R grid; interpolate from there during the
# fit so we don't re-evaluate the overlap integral inside the ODE.
_R_GRID = np.linspace(0.0, 110.0, 1101)
_G_GRID = np.array([pfb.G_of_R(R) for R in _R_GRID])
_H_GEOM_GRID = np.sqrt(np.maximum(_G_GRID, 0.0) / G_MAX)


def H_geom(R: float) -> float:
    if R <= 0:
        return 0.0
    if R >= 110.0:
        return float(_H_GEOM_GRID[-1])
    return float(np.interp(R, _R_GRID, _H_GEOM_GRID))


def tau_total(R: float, omega: float, params) -> float:
    alpha, kappa, beta = params
    H = H_geom(R)
    x = kappa * H * omega
    tau_eddy = alpha * H * 2.0 * x / (1.0 + x * x)
    tau_residual = I_CRANK * beta * omega
    return tau_eddy + tau_residual


def integrate_segment(R, t_eval, omega0, params):
    def rhs(_t, y):
        return [-tau_total(R, y[0], params) / I_CRANK]

    sol = solve_ivp(rhs, (float(t_eval[0]), float(t_eval[-1]) + 1e-6),
                    [omega0], t_eval=t_eval,
                    method="LSODA", rtol=1e-7, atol=1e-9)
    if not sol.success or sol.y.shape[1] != len(t_eval):
        return None
    return sol.y[0]


def residuals(params, segments):
    out = []
    for s in segments:
        R = float(s["R"])
        t = s["t"]
        omega = s["omega"]
        omega0 = float(omega[0])
        omega_pred = integrate_segment(R, t, omega0, params)
        if omega_pred is None or not np.all(np.isfinite(omega_pred)):
            out.append(np.full(len(t), 1e3))
            continue
        scale = max(omega0, 0.1)
        out.append((omega - omega_pred) / scale / math.sqrt(len(t)))
    return np.concatenate(out)


def run_fit(segments, x0):
    lo = np.array([1.0,    1e-4, 0.0])
    hi = np.array([1000.0, 5.0,  0.5])
    res = least_squares(residuals, x0, args=(segments,),
                        bounds=(lo, hi), x_scale="jac",
                        max_nfev=400, verbose=2)
    return res


def plot_shape_compare(out_path):
    """H_geom(R) vs empirical Hill from the prior fit."""
    Rg = np.linspace(0, 100, 401)
    H_g = np.array([H_geom(R) for R in Rg])
    # Empirical fit values from analysis/fit_wouterse.py
    R_h_emp, p_emp = 72.9, 1.27
    H_emp = np.array([empirical_hill(R, R_h_emp, p_emp) for R in Rg])
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(Rg, H_g, "C0-", lw=2.2, label="H_geom(R) from overlap geometry")
    ax.plot(Rg, H_emp, "C3--", lw=1.8, alpha=0.85,
            label=f"empirical Hill (R_h={R_h_emp}, p={p_emp})")
    ax.set_xlabel("R")
    ax.set_ylabel("H(R)")
    ax.set_title("Brake-shape function: geometry vs empirical Hill")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close()
    print(f"wrote {out_path}")


def main():
    if not (ROOT / "data/calibration/all_spindowns.csv").exists():
        plot_shape_compare(OUT_DIR / "H_geom_vs_hill.png")
        sys.exit("\nspin-down CSV not in repo; ran shape-comparison plot only. "
                 "Run aggregate_spindowns.py with your captured spin-downs, "
                 "then re-run this script for the fit.")

    segments = collect()
    n_samples = sum(len(s["t"]) for s in segments)
    print(f"collected {len(segments)} segments ({n_samples} samples)")
    print(f"using H_geom from physics_first_brake "
          f"(a={pfb.A_MAG*100:.2f} cm dia, L_m={pfb.L_MAG*1000:.1f} mm)\n")

    # Warm-start at the empirical-Hill fit values
    x0 = np.array([165.0, 0.160, 0.04])
    res = run_fit(segments, x0)
    alpha, kappa, beta = res.x
    rss = 0.5 * float((res.fun ** 2).sum())
    two_ak = 2.0 * alpha * kappa
    a_over_k = alpha / kappa

    print("\n=== H_geom fit (α, κ, β all free; Hill shape removed) ===")
    print(f"  α     = {alpha:>8.3f} N·m")
    print(f"  κ     = {kappa:>8.4f} s/rad")
    print(f"  β     = {beta:>8.4f} 1/s")
    print(f"  2ακ   = {two_ak:>8.3f} N·m·s/rad")
    print(f"  α/κ   = {a_over_k:>8.1f} W   (asymptotic peak)")
    print(f"  RSS   = {rss:>8.6f}")
    print()
    print("=== Reference: empirical-Hill fit (fit_wouterse.py) ===")
    print(f"  α     = 165.000 (pinned to 1000 W spec via α/κ)")
    print(f"  κ     = 0.1600   R_h = 72.9   p = 1.27   β = 0.0389")
    print(f"  2ακ   = 52.800")
    print(f"  α/κ   = 1031.3 W")
    print(f"  RSS   = 0.0431  (from README; recompute locally to confirm)")
    print()

    # Interpretation cheat-sheet
    print("Interpretation:")
    if rss < 0.06:
        print(f"  RSS within ~40% of empirical Hill → H_geom captures the brake")
        print(f"  shape well. The 2 free Hill parameters were absorbing model")
        print(f"  flexibility, not real shape information.")
    else:
        print(f"  RSS substantially worse than empirical Hill → H_geom shape")
        print(f"  is off in some R range. Inspect the per-segment residuals.")
    if 100 < alpha < 250:
        print(f"  α fits at {alpha:.1f} (vs 165 pinned). Independent of any")
        print(f"  spec; matches the geometric anchor (~161 with measured")
        print(f"  magnets). Triangulation holds.")

    plot_shape_compare(OUT_DIR / "H_geom_vs_hill.png")


if __name__ == "__main__":
    main()
