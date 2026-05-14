"""Fit Wouterse eddy-brake torque model on full ω(t) spindown trajectories.

Why this replaces a linear-damping λ(R) model
---------------------------------------------
Wouterse (1991), Smythe (1942), and Wiederick (1987) all show that a
permanent-magnet eddy brake on a conducting disc (the IC8 is exactly this
— aluminum flywheel, gap-adjustable PM brake) has braking torque

    τ(R, ω) = τ_max(R) · 2(ω/ω_c(R)) / (1 + (ω/ω_c(R))²)

Three regimes in ω at fixed R:
  ω << ω_c :  τ ≈ (2τ_max/ω_c)·ω           [linear damping; this is what
                                            the old λ(R) model assumed]
  ω = ω_c  :  τ = τ_max                    [peak]
  ω >> ω_c :  τ ≈ 2τ_max·(ω_c/ω)           [decreasing — induced reaction
                                            field opposes the source]

ω_c shrinks fast as R rises (gap closes → B² grows → ω_c ∝ 1/B²). For the
IC8 at high R the riding cadence is past ω_c, so a linear extrapolation
of low-R-fit λ wildly overshoots. This is the source of the high-R
runaway in the old bridge.

Strict Wouterse coupling: τ_max ∝ B²(R), ω_c ∝ 1/B²(R), so both R-
functions share a single B²(R) shape. This is the physics-honest
formulation — the extra freedom of the non-strict version exists only
to absorb second-order effects (skin depth, edge effects) and is not
warranted by the constraint power of our data.

R-shape
-------
Parameterise B²(R) as a sum of two Hill curves (smooth, monotone,
continuous, zero at R=0):

    H(R) = w · R^p1/(R^p1 + R_h1^p1) + (1−w) · R^p2/(R^p2 + R_h2^p2)

A single 2-param Hill systematically over-brakes by ~0.3–0.85 rad/s
across R = 22..44 in the middle of the spin-down — the empirical
B²(R) has a shoulder a single sigmoid can't bend to. Two Hills give
the H(R) curve enough flexibility to absorb that mid-band structure.
The decomposition is empirical, not a clean physical mapping to the
two magnet pairs (which both engage over the same R range and already
sum to a smooth ramp in the geometric H_geom — see physics_first_brake.py
and fit_geom_hill.py). Plausible physical sources of the residual
shoulder are yoke flux saturation, anti-polar pair coupling varying
with proximity, or σ_Al frequency dependence — none clean enough to
derive a single second-Hill knob from physics.

Pin both eddy-brake R-dependences to the same H(R) via the strict
Wouterse law:

    τ_max(R) = α · H(R)
    1/ω_c(R) = κ · H(R)

so τ_max(R)·ω_c(R) = α/κ — a single constant set by disc geometry
(conductivity × thickness × pole-area × radius²). Plus the residual:

    τ_total(R, ω) = τ_c + I·β·ω + 2·α·κ·H(R)²·ω / (1 + (κ·H(R)·ω)²)

Eight free parameters total — {α, w, R_h1, p1, R_h2, p2, κ, β, τ_c}
minus α pinned to the 1000 W spec.

Fit
---
For each segment we numerically integrate dω/dt = -τ_total/I from
ω(t=0) = ω_data[0] and compute residuals (ω_data - ω_model)/scale/√n
so each segment contributes ~1 unit of leverage.
"""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parent.parent
ALL_SPINDOWNS_CSV = ROOT / "data/calibration/all_spindowns.csv"
OUT_DIR = ROOT / "data/calibration/wouterse_fit"
OUT_DIR.mkdir(parents=True, exist_ok=True)

I_CRANK = 9.09  # kg·m² (effective, at the crank). Derived from geometry,
                # not fit. 18 kg total flywheel (manufacturer spec): a 5 mm
                # uniform Al disc plus two lead weight-rings, one on each
                # face. Disc radius R = 0.23 m (46 cm OD); rings measured
                # by ruler against the outer edge:
                #   Disc:   π·R²·t·ρ_Al = π·(0.23)²·0.005·2700 = 2.24 kg
                #   Ring A: r = 14–18 cm, h ≤ 2.0 cm  (4 cm wide)
                #   Ring B: r = 13–17 cm, h ≤ 1.5 cm  (4 cm wide)
                # Both rings have ~2-3 mm chamfered edges extending
                # past the flat-top radii (chamfer cuts the corner,
                # not all the way to zero thickness); the chamfer
                # volume closes the 18 kg budget at flat-top h
                # comfortably within the bounds → m_A = 9.25 kg,
                # m_B = 6.50 kg at ρ_Pb = 11340 kg/m³. Symmetric
                # chamfers shift I by <0.3%, below the flat-ring
                # formula's precision. Iron would need rings 46% over
                # the bounds, brass 35%, copper 28%, bismuth 18% —
                # all ruled out. Lead is the only material consistent
                # with the ring volumes and the 18 kg flywheel total.
                #   I_disc    = ½·m·R²              = 0.0594 kg·m²
                #   I_ring_A  = m·(r_in² + r_out²)/2 = 0.2405 kg·m²
                #   I_ring_B  = m·(r_in² + r_out²)/2 = 0.1490 kg·m²
                #   I_flywheel                       = 0.4488 kg·m²
                #   I_crank   = g²·I_flywheel = 9.09 kg·m²   (g = 4.5)

# α is pinned, not fit. The data only constrains the product 2ακ·H²/I
# plus the H(R) shape — α and κ slide along a degenerate ridge unless
# one is anchored.
#
# Our coastdown set sits mostly in the linear-damping regime ω << ω_c,
# so the bell-curve saturation isn't directly observed. Releasing α
# with the H(R) shape free walks the optimizer to the upper bound
# (α/κ → ∞, model degenerates into power-law). α has to be set by an
# external prior.
#
# α = 165 N·m anchors α/κ ≈ 1000 W, matching the manufacturer's max-
# output spec. This anchors absolute scale without invoking perceived
# effort, and keeps the asymptotic saturation ceiling at a defensible,
# specification-grounded number.
#
# κ is also pinned (KAPPA_PINNED below) so the 1000 W anchor stays
# meaningful: with five H-shape knobs the optimizer would otherwise
# shrink κ to keep ακH²·ω matched in the linear regime, since H_max
# is unconstrained by the data. Pinning κ at the previous single-Hill
# optimum (0.1585) preserves α/κ = 1041 W and forces all the model
# flexibility into the H(R) shape, which is where the structural
# mid-band sag actually lives.
#
# See analysis/physics_first_brake.py for the brake-geometry derivation
# and analysis/fit_h_alternatives.py for the comparison that picked
# the two-Hill form over Richards (3-param) and 8-knot monotone spline.
ALPHA_PINNED = 165.0
KAPPA_PINNED = 0.1585


# ---------------------------------------------------------------------------
# Data.
# ---------------------------------------------------------------------------

def collect():
    """Read all_spindowns.csv → list of segments {source, R, occ, method,
    t (rebased to 0), omega}."""
    if not ALL_SPINDOWNS_CSV.exists():
        sys.exit(f"missing {ALL_SPINDOWNS_CSV} — run aggregate_spindowns.py first")
    by_id: dict[int, dict] = defaultdict(lambda: {"t": [], "omega": []})
    with ALL_SPINDOWNS_CSV.open() as f:
        for row in csv.DictReader(f):
            sid = int(row["id"])
            s = by_id[sid]
            if "source" not in s:
                s["source"] = row["source"]
                s["R"] = int(row["R"])
                s["occ"] = int(row["occ"])
                s["method"] = row["method"]
            s["t"].append(float(row["t_s"]))
            s["omega"].append(float(row["omega_rad_s"]))
    out = []
    for sid in sorted(by_id):
        s = by_id[sid]
        t = np.asarray(s["t"], dtype=float)
        om = np.asarray(s["omega"], dtype=float)
        if len(t) < 4 or (om <= 0).any():
            continue
        order = np.argsort(t)
        t, om = t[order], om[order]
        s["t"] = t - t[0]
        s["omega"] = om
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# Model.
# ---------------------------------------------------------------------------

def hill(R: float, R_h: float, p: float) -> float:
    if R <= 0:
        return 0.0
    return R**p / (R**p + R_h**p)


def H_two(R: float, w: float, R_h1: float, p1: float,
          R_h2: float, p2: float) -> float:
    """Sum-of-two-Hills H(R), [0,1)-valued, monotone, zero at R=0."""
    if R <= 0:
        return 0.0
    return w * hill(R, R_h1, p1) + (1.0 - w) * hill(R, R_h2, p2)


def tau_total(R: float, omega: float, params) -> float:
    """Strict-Wouterse brake + Coulomb + viscous residual drag at the crank.

    params = (w, R_h1, p1, R_h2, p2, β, τ_c). α is held fixed at
    ALPHA_PINNED and κ at KAPPA_PINNED. Sum-of-two-Hills H(R) drives
    both τ_max and 1/ω_c via the strict Wouterse coupling τ_max ∝ B²,
    ω_c ∝ 1/B². Residual drag is Coulomb (τ_c, constant, bearings +
    belt friction) plus viscous (β, e.g. windage, air film). The
    Coulomb term dominates at low ω and straightens the spin-down
    curve where pure viscous would over-curve.
    """
    w, R_h1, p1, R_h2, p2, beta, tau_c = params
    H = H_two(R, w, R_h1, p1, R_h2, p2)
    x = KAPPA_PINNED * H * omega
    tau_eddy = ALPHA_PINNED * H * 2.0 * x / (1.0 + x * x)
    # For ω > 0 (always true within a segment) Coulomb is simply +τ_c.
    tau_residual = tau_c + I_CRANK * beta * omega
    return tau_eddy + tau_residual


def integrate_segment(R, t_eval, omega0, params):
    """Solve I·dω/dt = -τ_total(R, ω), return ω at t_eval, or None on fail."""
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
        # Per-segment scale + 1/√n so each segment has ≈ unit leverage.
        scale = max(omega0, 0.1)
        out.append((omega - omega_pred) / scale / math.sqrt(len(t)))
    return np.concatenate(out)


# ---------------------------------------------------------------------------
# Fit driver.
# ---------------------------------------------------------------------------

def run_fit(segments, x0=None):
    if x0 is None:
        # Warm start: two-Hill optimum from fit_h_alternatives.py.
        x0 = np.array([
            0.50,    # w      (mixing weight on H1)
            30.0,    # R_h1   (sharp/back Hill midpoint)
            2.50,    # p1     (sharp Hill sharpness)
            80.0,    # R_h2   (broad/front Hill midpoint)
            2.00,    # p2     (broad Hill sharpness)
            0.022,   # β      (1/s)
            1.20,    # τ_c    (N·m)
        ])
    lo = np.array([0.0,  5.0,   0.5,  5.0,   0.5,  0.0,  0.0])
    hi = np.array([1.0,  500.,  10.,  500.,  10.,  0.5,  20.0])
    res = least_squares(residuals, x0, args=(segments,),
                        bounds=(lo, hi), x_scale="jac",
                        max_nfev=600, verbose=2)
    return res


# ---------------------------------------------------------------------------
# Diagnostics & plots.
# ---------------------------------------------------------------------------

def per_segment_lambda_data(segments):
    """Effective λ̂ from log-linear fit on data (for comparison plot)."""
    pts = []
    for s in segments:
        t, om = s["t"], s["omega"]
        if len(t) < 4 or (om <= 0).any():
            continue
        dt = t - t.mean()
        dy = np.log(om) - np.log(om).mean()
        S_tt = float((dt * dt).sum())
        if S_tt < 1e-9:
            continue
        slope = float((dt * dy).sum()) / S_tt
        lam = -slope
        if lam <= 0:
            continue
        pts.append({"R": s["R"], "lam": lam,
                    "method": s["method"], "n": len(t)})
    return pts


def plot_R_curves(params, segments, out_path):
    w, R_h1, p1, R_h2, p2, beta, tau_c = params
    alpha = ALPHA_PINNED
    kappa = KAPPA_PINNED
    Rg = np.linspace(0.01, 100, 600)
    H = np.array([H_two(R, w, R_h1, p1, R_h2, p2) for R in Rg])
    tau_max = alpha * H
    inv_omc = kappa * H
    safe_inv = np.where(inv_omc > 1e-9, inv_omc, np.nan)
    omega_c = 1.0 / safe_inv
    P_peak_at_critical = tau_max * omega_c  # = α/κ (constant, by strict Wouterse)

    fig, axes = plt.subplots(1, 3, figsize=(16, 4.5))
    ax = axes[0]
    ax.plot(Rg, tau_max, "C0", lw=2)
    ax.set_xlabel("R")
    ax.set_ylabel("τ_max(R)  [N·m]")
    ax.set_title("Peak brake torque vs R")
    ax.grid(alpha=0.3)

    ax = axes[1]
    ax.plot(Rg, omega_c, "C1", lw=2)
    ax.set_yscale("log")
    ax.set_xlabel("R")
    ax.set_ylabel("ω_c(R)  [rad/s]")
    ax.set_title("Critical angular speed vs R")
    ax.grid(alpha=0.3, which="both")
    ax.axhline(60 * 2 * math.pi / 60, color="k", ls=":", lw=0.8,
               label="ω at cad=60")
    ax.axhline(100 * 2 * math.pi / 60, color="k", ls="--", lw=0.8,
               label="ω at cad=100")
    ax.legend(fontsize=8)

    ax = axes[2]
    ax.plot(Rg, P_peak_at_critical, "C2", lw=2,
            label=f"τ_max·ω_c = α/κ = {alpha/kappa:.1f} W")
    ax.set_xlabel("R")
    ax.set_ylabel("τ_max·ω_c  [W]")
    ax.set_title("Peak power at critical ω (constant in strict Wouterse)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=9)

    fig.suptitle(
        f"Two-Hill Wouterse fit:  α={alpha:.0f} (pinned)  κ={kappa:.4f} (pinned)  "
        f"w={w:.3f}  R_h1={R_h1:.1f}  p1={p1:.2f}  R_h2={R_h2:.1f}  p2={p2:.2f}  "
        f"β={beta:.4f}  τ_c={tau_c:.3f}",
        fontsize=10, weight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(out_path, dpi=130)
    plt.close()
    print(f"wrote {out_path}")


def plot_segment_overlay(params, segments, out_path):
    """Grid of ω(t) data vs model for every segment."""
    n = len(segments)
    cols = 6
    rows = math.ceil(n / cols)
    fig, axes = plt.subplots(rows, cols, figsize=(cols * 2.6, rows * 1.9),
                             sharex=False, sharey=False)
    axes = np.atleast_1d(axes).flatten()
    segs_sorted = sorted(segments, key=lambda s: (s["R"], s["occ"]))
    for i, s in enumerate(segs_sorted):
        ax = axes[i]
        R = float(s["R"])
        t, om = s["t"], s["omega"]
        omega_pred = integrate_segment(R, t, float(om[0]), params)
        ax.plot(t, om, "k.", ms=2, alpha=0.5)
        if omega_pred is not None:
            ax.plot(t, omega_pred, "C3-", lw=1.2)
        ax.set_title(f"R={s['R']} occ={s['occ']} {s['method'][:3]}",
                     fontsize=8)
        ax.tick_params(labelsize=7)
        ax.grid(alpha=0.25)
    for j in range(n, len(axes)):
        axes[j].set_visible(False)
    fig.suptitle("Wouterse model vs data — per-segment ω(t)",
                 fontsize=11, weight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(out_path, dpi=130)
    plt.close()
    print(f"wrote {out_path}")


def plot_lambda_compare(params, points_data, out_path):
    """Compare per-segment λ̂ from data vs effective λ from model.

    Effective λ from model at low ω (linear regime):
        λ_eff(R) = β + 2·α·κ·H(R)² / I.
    At high ω (near or past ω_c) the data trajectory is non-exponential
    so a log-linear λ̂ doesn't apply — the curve diverges from data
    points there, which is expected and is the whole point of the
    Wouterse model."""
    w, R_h1, p1, R_h2, p2, beta, tau_c = params
    alpha = ALPHA_PINNED
    kappa = KAPPA_PINNED
    Rg = np.linspace(0, 100, 500)
    H = np.array([H_two(R, w, R_h1, p1, R_h2, p2) for R in Rg])
    lam_lin = beta + 2.0 * alpha * kappa * (H * H) / I_CRANK

    fig, ax = plt.subplots(figsize=(8, 5))
    R_pts = np.array([d["R"] for d in points_data])
    lam_pts = np.array([d["lam"] for d in points_data])
    ax.scatter(R_pts, lam_pts, s=22, c="#ff7f0e",
               label=f"per-segment λ̂ from data (n={len(R_pts)})")
    ax.plot(Rg, lam_lin, "C2", lw=2,
            label="model low-ω λ_eff(R) = β + 2ακH(R)²/I")
    ax.set_xlabel("R")
    ax.set_ylabel("effective decay rate λ (1/s)")
    ax.set_title("Wouterse model: linear-regime λ_eff vs measured λ̂")
    ax.grid(alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, dpi=130)
    plt.close()
    print(f"wrote {out_path}")


def report_riding_predictions(params):
    """Sanity check: bridge prediction at a handful of riding conditions.

    P_brake(R, ω) = τ_total(R, ω) · ω
    """
    print("\n=== predicted brake power at riding conditions ===")
    print(f"  {'R':>3}  {'cad':>3}  {'ω':>5}  {'τ':>6}  {'P':>6}")
    for R, cad in [(11, 100), (25, 90), (31, 100), (35, 90),
                   (48, 70), (60, 80), (80, 80), (90, 70)]:
        omega = cad * 2 * math.pi / 60
        tau = tau_total(float(R), omega, params)
        P = tau * omega
        print(f"  {R:>3}  {cad:>3}  {omega:>5.2f}  "
              f"{tau:>6.2f}  {P:>6.1f}")


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

def main():
    segments = collect()
    print(f"collected {len(segments)} video segments "
          f"({sum(len(s['t']) for s in segments)} samples)")

    print("\n=== global two-Hill strict-Wouterse fit ===")
    res = run_fit(segments)
    params = res.x
    w, R_h1, p1, R_h2, p2, beta, tau_c = params
    alpha = ALPHA_PINNED
    kappa = KAPPA_PINNED
    omega_c_sat = 1.0 / kappa
    print("\n=== fit ===")
    print(f"  α     = {alpha:.4f}     N·m   (peak torque amplitude, pinned)")
    print(f"  κ     = {kappa:.4f}     s/rad (1/ω_c at saturation, pinned)")
    print(f"  w     = {w:.4f}      (mix weight on H1)")
    print(f"  R_h1  = {R_h1:.3f}     (sharp Hill midpoint)")
    print(f"  p1    = {p1:.3f}      (sharp Hill sharpness)")
    print(f"  R_h2  = {R_h2:.3f}     (broad Hill midpoint)")
    print(f"  p2    = {p2:.3f}      (broad Hill sharpness)")
    print(f"  β     = {beta:.4f}     1/s   (viscous residual drag)")
    print(f"  τ_c   = {tau_c:.4f}     N·m   (Coulomb residual drag)")
    print(f"  ω_c at saturation = {omega_c_sat:.2f} rad/s "
          f"= {omega_c_sat*60/(2*math.pi):.1f} crank-rpm")
    print(f"  τ_max·ω_c = α/κ = {alpha/kappa:.1f} W "
          f"(geometry-only invariant)")
    print(f"  RSS_norm = {0.5 * float((res.fun**2).sum()):.6f}")
    print(f"  iterations: {res.nfev} fn evals")

    plot_R_curves(params, segments, OUT_DIR / "tau_max_omega_c_vs_R.png")
    plot_segment_overlay(params, segments, OUT_DIR / "segments_overlay.png")
    points_data = per_segment_lambda_data(segments)
    plot_lambda_compare(params, points_data, OUT_DIR / "lambda_compare.png")

    report_riding_predictions(params)

    out_json = OUT_DIR / "fit.json"
    out_json.write_text(json.dumps({
        "alpha": float(alpha),
        "kappa": float(kappa),
        "w": float(w),
        "R_h1": float(R_h1), "p1": float(p1),
        "R_h2": float(R_h2), "p2": float(p2),
        "beta": float(beta),
        "tau_c": float(tau_c),
        "omega_c_at_saturation": float(omega_c_sat),
        "tau_max_times_omega_c": float(alpha / kappa),
        "rss_norm": float(0.5 * (res.fun**2).sum()),
        "n_segments": len(segments),
        "n_samples": int(sum(len(s["t"]) for s in segments)),
    }, indent=2))
    print(f"\nwrote {out_json}")


if __name__ == "__main__":
    main()
