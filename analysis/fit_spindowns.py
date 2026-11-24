"""Fit the IC8's brake and residual drag on full ω(t) spin-down trajectories.

Model
-----
The IC8's brake is two pairs of permanent magnets straddling the
aluminium flywheel at a fixed gap; the dial slides them from just off the
rim onto the disc (see physics_first_brake.py). Below the brake's critical
speed an eddy brake is linear damping, so torque at the crank is

    τ(R, ω) = c · H(R)² · ω  +  τ_c  +  I · β · ω

    H(R) = w · R^p1/(R^p1 + R_h1^p1) + (1−w) · R^p2/(R^p2 + R_h2^p2)

c · H(R)² is the brake's damping at dial position R (N·m·s/rad at the
crank). τ_c is Coulomb drag (bearings, belt, seals) and I·β·ω viscous
drag (windage); R = 0 spin-downs prefer that split over viscous-only by
~15× in RSS (residual_drag_shape.py). H is a sum of two Hill curves, an
empirical shape; physics_first_brake.py derives the same shape within
~15% from R = 40 up from the magnet-overlap geometry.

No roll-off. Above its critical speed an eddy brake's torque falls
(Wouterse: τ ∝ 2x/(1+x²), x = ω/ω_c). The spin-downs show none: with a
fade at a fixed, R-independent ω_c (the gap does not change with R), RSS
rises steadily as ω_c comes down from ∞ (run with --fade-profile), and a
fade with ω_c ∝ 1/H(R) refits to zero strength. So the data put any fade
well above the measured speeds, which reach ~150 rpm at R ≤ 44 and
90–100 rpm at R = 66–85.

Seven free shape parameters {w, R_h1, p1, R_h2, p2, β, τ_c} plus the
amplitude c, at the pinned I.

Fit
---
For each segment we numerically integrate dω/dt = −τ/I from
ω(t=0) = ω_data[0] and compute residuals (ω_data − ω_model)/scale/√n
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
OUT_DIR = ROOT / "data/calibration/spindown_fit"

I_CRANK = 6.66  # kg·m² (effective, at the crank). Flywheel geometry: a
                # 5 mm aluminium disc (R = 23 cm) with a steel ring on
                # each face (r = 14–18 cm × 2.03 cm, 13–17 cm × 1.52 cm;
                # a magnet sticks, so not lead), times g² for the belt
                # ratio g = 4.5. Ruler-measured, so good to roughly 10%.
                # Spin-downs fix only torque/I: c, τ_c and I scale
                # together without changing any fitted ω(t), so I sets
                # the absolute output and the fit carries c and τ_c.

PARAM_NAMES = ("c", "w", "R_h1", "p1", "R_h2", "p2", "beta", "tau_c")


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


def tau_total(R: float, omega: float, params, omega_c: float = math.inf) -> float:
    """Brake + Coulomb + viscous drag at the crank, N·m. omega_c < ∞ adds
    a Wouterse fade 1/(1 + (ω/ω_c)²) to the brake, for --fade-profile."""
    c, w, R_h1, p1, R_h2, p2, beta, tau_c = params
    H = H_two(R, w, R_h1, p1, R_h2, p2)
    fade = 1.0 / (1.0 + (omega / omega_c) ** 2)
    # For ω > 0 (always true within a segment) Coulomb is simply +τ_c.
    return c * H * H * omega * fade + tau_c + I_CRANK * beta * omega


def integrate_segment(R, t_eval, omega0, params, omega_c=math.inf):
    """Solve I·dω/dt = -τ_total(R, ω), return ω at t_eval, or None on fail."""
    def rhs(_t, y):
        return [-tau_total(R, y[0], params, omega_c) / I_CRANK]

    sol = solve_ivp(rhs, (float(t_eval[0]), float(t_eval[-1]) + 1e-6),
                    [omega0], t_eval=t_eval,
                    method="LSODA", rtol=1e-7, atol=1e-9)
    if not sol.success or sol.y.shape[1] != len(t_eval):
        return None
    return sol.y[0]


def residuals(params, segments, omega_c=math.inf):
    out = []
    for s in segments:
        R = float(s["R"])
        t = s["t"]
        omega = s["omega"]
        omega0 = float(omega[0])
        omega_pred = integrate_segment(R, t, omega0, params, omega_c)
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

X0 = np.array([43.8, 0.45, 130.0, 0.65, 79.0, 1.87, 0.014, 1.10])
LO = np.array([1.0,    0.0, 5.0,    0.3, 5.0,   0.3,  0.0, 0.0])
HI = np.array([2000.0, 1.0, 2000.0, 10., 500.0, 10.,  0.5, 20.0])


def run_fit(segments, x0=X0, omega_c=math.inf, verbose=2):
    return least_squares(residuals, x0, args=(segments, omega_c),
                         bounds=(LO, HI), x_scale="jac",
                         max_nfev=600, verbose=verbose)


def rss(res) -> float:
    return 0.5 * float((res.fun ** 2).sum())


# ---------------------------------------------------------------------------
# Diagnostics & plots.
# ---------------------------------------------------------------------------

def plot_damping(params, out_path):
    c, w, R_h1, p1, R_h2, p2, beta, tau_c = params
    Rg = np.linspace(0, 100, 501)
    D = np.array([c * H_two(R, w, R_h1, p1, R_h2, p2) ** 2 for R in Rg])
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(Rg, D, "C0", lw=2, label="brake damping c·H(R)²")
    ax.axhline(I_CRANK * beta, color="C1", ls="--",
               label=f"viscous drag I·β = {I_CRANK * beta:.3f}")
    ax.set_xlabel("R")
    ax.set_ylabel("damping at the crank  [N·m·s/rad]")
    ax.set_title(f"c={c:.2f}  w={w:.3f}  R_h1={R_h1:.1f}  p1={p1:.2f}  "
                 f"R_h2={R_h2:.1f}  p2={p2:.2f}  β={beta:.4f}  τ_c={tau_c:.3f}",
                 fontsize=9)
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
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
    fig.suptitle("Brake model vs data — per-segment ω(t)",
                 fontsize=11, weight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    fig.savefig(out_path, dpi=130)
    plt.close()
    print(f"wrote {out_path}")


RIDING = [(11, 100), (25, 90), (31, 100), (35, 90),
          (48, 70), (60, 80), (80, 80), (85, 50), (90, 70)]


def power(R, cad, params, omega_c=math.inf):
    omega = cad * math.pi / 30
    return tau_total(float(R), omega, params, omega_c) * omega


def report_riding_predictions(params):
    print("\n=== predicted brake power at riding conditions ===")
    print(f"  {'R':>3}  {'cad':>3}  {'P':>6}")
    for R, cad in RIDING:
        print(f"  {R:>3}  {cad:>3}  {power(R, cad, params):>6.1f}")


def fade_profile(segments, params):
    """Refit with a fixed, R-independent fade at each ω_c and print RSS."""
    base = rss(least_squares(residuals, params, args=(segments,),
                             bounds=(LO, HI), x_scale="jac", max_nfev=1))
    print(f"\n=== fade profile (no fade: RSS {base:.5f}) ===")
    for rpm in (120, 160, 200, 250, 300, 400):
        wc = rpm * math.pi / 30
        res = run_fit(segments, params, omega_c=wc, verbose=0)
        cells = "  ".join(f"R{R}/{cad} {power(R, cad, res.x, wc):4.0f} W"
                          for R, cad in ((31, 90), (60, 90), (85, 50), (85, 90)))
        print(f"  ω_c {rpm:3d} rpm: RSS {rss(res):.5f}   {cells}", flush=True)


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

def main():
    segments = collect()
    print(f"collected {len(segments)} video segments "
          f"({sum(len(s['t']) for s in segments)} samples)")

    res = run_fit(segments)
    params = res.x
    print("\n=== fit ===")
    for name, v in zip(PARAM_NAMES, params):
        print(f"  {name:<6} = {v:.4f}")
    print(f"  RSS_norm = {rss(res):.6f}")
    print(f"  iterations: {res.nfev} fn evals")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    plot_damping(params, OUT_DIR / "damping_vs_R.png")
    plot_segment_overlay(params, segments, OUT_DIR / "segments_overlay.png")
    report_riding_predictions(params)

    out_json = OUT_DIR / "fit.json"
    out_json.write_text(json.dumps({
        **{name: float(v) for name, v in zip(PARAM_NAMES, params)},
        "i_crank": I_CRANK,
        "rss_norm": rss(res),
        "n_segments": len(segments),
        "n_samples": int(sum(len(s["t"]) for s in segments)),
    }, indent=2))
    print(f"\nwrote {out_json}")

    if "--fade-profile" in sys.argv:
        fade_profile(segments, params)


if __name__ == "__main__":
    main()
