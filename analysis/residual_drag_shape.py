"""Isolate the R=0 spin-downs and fit candidate residual-drag forms.

At R=0 the eddy brake contributes nothing (magnets are fully retracted
off the disc), so the ω(t) curve is shaped entirely by whatever residual
drag the system has. Fit each candidate form independently — whichever
wins tells us what to put into the full model.

Candidates:
  1. Viscous only       τ = I·β·ω
  2. Coulomb only       τ = τ_c
  3. Windage only       τ = I·γ·ω²
  4. Coulomb + viscous  τ = τ_c + I·β·ω
  5. Coulomb + windage  τ = τ_c + I·γ·ω²
  6. All three          τ = τ_c + I·β·ω + I·γ·ω²
"""
from __future__ import annotations

import sys
from pathlib import Path
from collections import defaultdict

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis"))
from fit_geom_hill import collect, I_CRANK


def make_resid_drag(form: str):
    """Return (tau_resid_fn, n_params, bounds_lo, bounds_hi, x0, names)."""
    if form == "viscous":
        return ((lambda p, w: I_CRANK * p[0] * w), 1,
                [0.0], [1.0], [0.05], ["β"])
    if form == "coulomb":
        return ((lambda p, w: p[0]), 1,
                [0.0], [20.0], [2.0], ["τ_c"])
    if form == "windage":
        return ((lambda p, w: I_CRANK * p[0] * w * w), 1,
                [0.0], [1.0], [0.01], ["γ"])
    if form == "coulomb+viscous":
        return ((lambda p, w: p[0] + I_CRANK * p[1] * w), 2,
                [0.0, 0.0], [20.0, 1.0], [1.5, 0.02], ["τ_c", "β"])
    if form == "coulomb+windage":
        return ((lambda p, w: p[0] + I_CRANK * p[1] * w * w), 2,
                [0.0, 0.0], [20.0, 1.0], [1.5, 0.005], ["τ_c", "γ"])
    if form == "coulomb+viscous+windage":
        return ((lambda p, w: p[0] + I_CRANK * p[1] * w + I_CRANK * p[2] * w * w),
                3, [0.0, 0.0, 0.0], [20.0, 1.0, 1.0],
                [1.5, 0.02, 0.005], ["τ_c", "β", "γ"])
    raise ValueError(form)


def fit_R0(segments, form):
    tau_fn, n, lo, hi, x0, names = make_resid_drag(form)

    def integrate(omega0, t_eval, params):
        def rhs(_t, y):
            w = max(0.0, y[0])
            return [-tau_fn(params, w) / I_CRANK]
        sol = solve_ivp(rhs, (float(t_eval[0]), float(t_eval[-1]) + 1e-6),
                        [omega0], t_eval=t_eval,
                        method="LSODA", rtol=1e-7, atol=1e-9)
        if not sol.success or sol.y.shape[1] != len(t_eval):
            return None
        return sol.y[0]

    def residuals(params):
        out = []
        for s in segments:
            t = s["t"]; omega = s["omega"]
            pred = integrate(float(omega[0]), t, params)
            if pred is None:
                out.append(np.full(len(t), 1e3))
                continue
            scale = max(omega[0], 0.1)
            out.append((omega - pred) / scale / np.sqrt(len(t)))
        return np.concatenate(out)

    res = least_squares(residuals, np.array(x0), bounds=(lo, hi),
                        x_scale="jac", max_nfev=500)
    return res, names


def main():
    all_segments = collect()
    R0_segments = [s for s in all_segments if int(s["R"]) == 0]
    print(f"R=0 segments: {len(R0_segments)}  "
          f"total samples: {sum(len(s['t']) for s in R0_segments)}")
    for s in R0_segments:
        print(f"  ω₀={s['omega'][0]:.2f}  ω_end={s['omega'][-1]:.2f}  "
              f"t_end={s['t'][-1]:.1f}s  n={len(s['t'])}")

    print(f"\n{'form':>28}  {'RSS':>10}  params")
    print("-" * 64)
    for form in ["viscous", "coulomb", "windage",
                 "coulomb+viscous", "coulomb+windage",
                 "coulomb+viscous+windage"]:
        res, names = fit_R0(R0_segments, form)
        rss = 0.5 * float((res.fun ** 2).sum())
        param_str = "  ".join(f"{n}={v:.4f}" for n, v in zip(names, res.x))
        print(f"{form:>28}  {rss:>10.6f}  {param_str}")


if __name__ == "__main__":
    main()
