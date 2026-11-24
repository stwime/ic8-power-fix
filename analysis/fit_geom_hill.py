"""Refit the spin-downs with the brake shape from the magnet geometry
instead of the empirical two-Hill H(R).

physics_first_brake.py solves the eddy currents for the measured magnet
pairs and gives the brake's damping G(R) as the carrier slides them onto
the disc, with full overlap at R = 100. What it can't know is the dial-to-
carrier mapping, so a one-parameter warp stands in for the cam:

    R_eff(R; q) = 100 · (R/100)^q        (q = 1: linear)
    τ(R, ω) = c · G(R_eff)/G(100) · ω + τ_c + I·β·ω

Four free parameters {c, β, q, τ_c} against fit_spindowns.py's eight. If
the RSS comes close, the geometry explains the brake's dial shape; c is
then the brake damping at full overlap, which physics_first_brake.py
predicts from the magnets.

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
sys.path.insert(0, str(ROOT))

import physics_first_brake as pfb  # noqa: E402
from analysis import bridge_model as bm  # noqa: E402
from fit_spindowns import collect, I_CRANK, residuals as shipped_residuals  # noqa: E402

OUT_DIR = ROOT / "analysis_out"

# G on a 1-R grid out to the warp's reach; interpolated inside the ODE.
_R_GRID = np.arange(0.0, 111.0, 1.0)
_S_GRID = np.array([pfb.G_of_R(R) for R in _R_GRID]) / pfb.G_of_R(100.0)


def shape(R: float, q: float) -> float:
    """G(R_eff)/G(100): the brake's damping relative to full overlap."""
    if R <= 0:
        return 0.0
    return float(np.interp(100.0 * (R / 100.0) ** q, _R_GRID, _S_GRID))


def tau_total(R: float, omega: float, params) -> float:
    c, beta, q, tau_c = params
    return c * shape(R, q) * omega + tau_c + I_CRANK * beta * omega


def integrate_segment(R, t_eval, omega0, params):
    sol = solve_ivp(lambda _t, y: [-tau_total(R, y[0], params) / I_CRANK],
                    (float(t_eval[0]), float(t_eval[-1]) + 1e-6),
                    [omega0], t_eval=t_eval,
                    method="LSODA", rtol=1e-7, atol=1e-9)
    if not sol.success or sol.y.shape[1] != len(t_eval):
        return None
    return sol.y[0]


def residuals(params, segments):
    out = []
    for s in segments:
        t, omega = s["t"], s["omega"]
        omega_pred = integrate_segment(float(s["R"]), t, float(omega[0]), params)
        if omega_pred is None or not np.all(np.isfinite(omega_pred)):
            out.append(np.full(len(t), 1e3))
            continue
        out.append((omega - omega_pred) / max(float(omega[0]), 0.1)
                   / math.sqrt(len(t)))
    return np.concatenate(out)


def main():
    segments = collect()
    print(f"collected {len(segments)} segments "
          f"({sum(len(s['t']) for s in segments)} samples)")

    #              c      β      q     τ_c
    x0 = np.array([14.0,  0.014, 1.0,  1.1])
    res = least_squares(residuals, x0, args=(segments,),
                        bounds=([1.0, 0.0, 0.2, 0.0], [500.0, 0.5, 5.0, 20.0]),
                        x_scale="jac", max_nfev=300)
    c, beta, q, tau_c = res.x
    rss = 0.5 * float((res.fun ** 2).sum())
    shipped = (bm.C_EDDY, bm.W, bm.R_H1, bm.P1, bm.R_H2, bm.P2, bm.BETA, bm.TAU_C)
    rss_shipped = 0.5 * float((shipped_residuals(shipped, segments) ** 2).sum())

    print("\n=== geometry shape + cam warp ===")
    print(f"  c    = {c:8.3f} N·m·s/rad  (brake damping at full overlap)")
    print(f"  β    = {beta:8.4f} 1/s")
    print(f"  τ_c  = {tau_c:8.4f} N·m")
    print(f"  q    = {q:8.3f}  (cam warp; 1 = linear dial)")
    print(f"  RSS  = {rss:.5f}   shipped two-Hill: {rss_shipped:.5f}")
    print(f"\n  Shipped damping at R=100: {bm.C_EDDY * bm.hill(100.0)**2:.2f}; "
          f"physics_first_brake.py predicts it from the magnets.")

    Rg = np.linspace(0, 100, 201)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(Rg, [bm.C_EDDY * bm.hill(R) ** 2 for R in Rg], "k-", lw=2.2,
            label="shipped two-Hill c·H(R)²")
    ax.plot(Rg, [c * shape(R, q) for R in Rg], "C2-", lw=1.8,
            label=f"geometry, q={q:.2f}")
    ax.set_xlabel("R")
    ax.set_ylabel("brake damping at the crank  [N·m·s/rad]")
    ax.set_title(f"Brake shape fits: RSS {rss:.4f} (geometry) vs "
                 f"{rss_shipped:.4f} (two-Hill)")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    out_path = OUT_DIR / "H_geom_vs_hill.png"
    fig.savefig(out_path, dpi=130)
    plt.close()
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
