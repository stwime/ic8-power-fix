"""Refit the eddy-brake model with H_geom(R) + cam-warp instead of empirical Hill.

The original fit_wouterse.py uses a free 2-parameter Hill curve
H(R) = R^p / (R^p + R_h^p) to parameterize B²(R) and pins α from the
1000 W spec. The geometric path through physics_first_brake.py builds
H_geom(R) from the magnet-disc overlap geometry, assuming a linear
mapping from dial value R to carrier position. The user confirmed that
both magnet pairs fully overlap the disc at R=100, so the script's
geometric endpoint is correct (back pair sits at d = R_DISK - A_MAG,
the full-overlap threshold; front pair is comfortably inside). This
means H_geom(R=100) = 1 corresponds to the true operating maximum, not
an extrapolation.

What still needs flexing is the *dial-to-carrier* mapping. The linear
mapping (R → position) doesn't match the empirical Hill's shape across
the range. Real cams are non-linear; we add a one-parameter warp:

    R_eff(R; q) = 100 · (R/100)^q

  q = 1 : linear (current physics_first_brake assumption)
  q > 1 : carrier lags at low-R, accelerates at high-R
  q < 1 : carrier engages early, eases off at high-R

H_geom_warped(R; q) = H_geom(R_eff(R; q)) with H_geom(100) = 1 pinned.
Free fit parameters become {α, κ, β, q} — same count as fit_wouterse.py's
{κ, R_h, p, β} with α pinned, but here α is data-determined and the cam
parameter q has a mechanical reading.

What the fit will tell us. With H_geom pinning the operating maximum at
R=100 to full overlap, the fitted α is the physical brake amplitude at
full overlap. Compare to the magnet-circuit prediction 2ακ ≈ 52 from
physics_first_brake.py. If they disagree, the gap is being absorbed
into σ_Al alloy losses, yoke imperfections, fringe-field reduction of
effective G(R), or similar non-ideal effects.

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


def H_geom_warped(R: float, q: float) -> float:
    """H_geom at the warped dial value R_eff = 100·(R/100)^q.

    q controls dial-to-carrier nonlinearity (q=1 linear). Full overlap
    at R=100 is pinned by the geometry (user confirmed: back pair sits
    at the full-overlap threshold at R=100).
    """
    if R <= 0:
        return 0.0
    R_eff = 100.0 * (R / 100.0) ** q
    if R_eff >= 110.0:
        return float(_H_GEOM_GRID[-1])
    return float(np.interp(R_eff, _R_GRID, _H_GEOM_GRID))


def tau_total(R: float, omega: float, params) -> float:
    alpha, kappa, beta, q = params
    H = H_geom_warped(R, q)
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
    #              α       κ     β    q
    lo = np.array([1.0,    1e-4, 0.0, 0.2])
    hi = np.array([1000.0, 5.0,  0.5, 5.0])
    res = least_squares(residuals, x0, args=(segments,),
                        bounds=(lo, hi), x_scale="jac",
                        max_nfev=600, verbose=2)
    return res


def plot_shape_compare(out_path, q_fit: float | None = None):
    """H_geom(R; q) vs empirical Hill from the prior fit."""
    Rg = np.linspace(0, 100, 401)
    H_lin = np.array([H_geom_warped(R, 1.0) for R in Rg])
    R_h_emp, p_emp = 72.9, 1.27
    H_emp = np.array([empirical_hill(R, R_h_emp, p_emp) for R in Rg])
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(Rg, H_lin, "C0-", lw=2.0, alpha=0.5,
            label="H_geom(R), q=1 (linear cam)")
    if q_fit is not None:
        H_warp = np.array([H_geom_warped(R, q_fit) for R in Rg])
        ax.plot(Rg, H_warp, "C2-", lw=2.4,
                label=f"H_geom(R), q={q_fit:.2f} (fitted)")
    ax.plot(Rg, H_emp, "C3--", lw=1.8, alpha=0.85,
            label=f"empirical Hill (R_h={R_h_emp}, p={p_emp})")
    ax.set_xlabel("R")
    ax.set_ylabel("H(R)")
    ax.set_title("Brake-shape function: geometry + cam warp vs empirical Hill\n"
                 "(full overlap at R=100 pinned by geometry)")
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

    # Warm-start: empirical-Hill values for {α, κ, β}, q=3 to stretch the
    # geometric H_geom toward the empirical-Hill shape (linear H_geom hits
    # H=0.5 around R=35 vs empirical's R=73, so we need q ≈ ln(.35)/ln(.73)
    # ≈ 3.3).
    x0 = np.array([165.0, 0.160, 0.04, 3.0])
    res = run_fit(segments, x0)
    alpha, kappa, beta, q = res.x
    rss = 0.5 * float((res.fun ** 2).sum())
    two_ak = 2.0 * alpha * kappa
    a_over_k = alpha / kappa

    print("\n=== H_geom + cam-warp fit ({α, κ, β, q} free; s=1 from geometry) ===")
    print(f"  α     = {alpha:>8.3f} N·m")
    print(f"  κ     = {kappa:>8.4f} s/rad")
    print(f"  β     = {beta:>8.4f} 1/s")
    print(f"  q     = {q:>8.3f}        (cam nonlinearity; 1 = linear)")
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
        print(f"  RSS within ~40% of empirical Hill → the geometry + cam-warp")
        print(f"  shape captures the data as well as the free Hill did, with")
        print(f"  the same parameter count (4) but a physical interpretation.")
    elif rss < 0.10:
        print(f"  RSS a bit worse than empirical Hill. Geometry + one-parameter")
        print(f"  cam warp gets close; a slightly richer cam model (e.g. two")
        print(f"  break points) might close the gap.")
    else:
        print(f"  RSS substantially worse than empirical Hill. The single-")
        print(f"  parameter cam warp isn't flexible enough, or G_max is off,")
        print(f"  or the linear-regime ακH² coupling itself doesn't capture")
        print(f"  the high-R bell. Inspect per-segment residuals.")
    print()
    print(f"  Fitted 2ακ = {two_ak:.1f} is the linear-regime damping at R=100")
    print(f"  (full overlap, H=1 by geometry). Compare to the magnet-circuit")
    print(f"  prediction 2ακ ≈ 52 from physics_first_brake.py. Any gap")
    print(f"  goes into σ_Al alloy, yoke imperfection, or effective-G")
    print(f"  fringing — all combined.")

    plot_shape_compare(OUT_DIR / "H_geom_vs_hill.png", q_fit=q)


if __name__ == "__main__":
    main()
