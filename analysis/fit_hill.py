"""Refit λ(R) with a saturating Hill form on full ω(t) trajectories.

Data source
-----------
data/calibration/all_spindowns.csv — produced by aggregate_spindowns.py
from the hand-curated bounds in spindown_bounds.json. Per-rev ω where
the window has ≥2 full revs (BLE + long videos), windowed |ω| for the
short high-R videos that don't fit a full rev.

Why Hill, not power-law
-----------------------
The shipped power-law fit (analysis/fit_saturating.py) underestimates λ
at R≥30 by 2–4×, because the per-sample weighting (1/n_i) lets long
low-R BLE segments dominate the regression sums and drags the exponent
down. The deeper issue is the functional form: the per-segment λ shows
a *decreasing* log-log slope as R grows (≈2.1 at low R, ≈1.7 above
R≈50), and a power law has a constant log-log slope by construction.
The data is bending over — that is the signature of saturation, not of
a divergent or pure-power-law shape.

The physics actually predicts saturation. Eddy-brake torque is
τ ∝ B²(d)·ω, and the field B(d) of a finite cylindrical permanent magnet
is bounded above by its own surface field B_r/2·L/√(L²+a²). Coupled to
a linear dial-to-gap mapping d(R) = d_max·(1 − R/R_max), λ(R) bends over
toward a finite asymptote rather than diverging.

The Hill form captures that with three shape parameters:

    λ(R) = β + α · R^p / (R^p + R_c^p)

  R << R_c :  λ ≈ β + α·(R/R_c)^p     — power-law-ish low-R rise
  R >> R_c :  λ → β + α                — saturated upper bound

  α   = saturation amplitude (1/s)
  R_c = half-saturation dial position (dimensionless)
  p   = transition sharpness (dimensionless)
  β   = non-magnetic residual drag at R=0 (1/s) — bearings + air

Fit method
----------
Joint fit on the full ω(t) of every spindown (BLE/CSC + video).
Linearize per segment: ln ω(t) − ⟨ln ω⟩ = −λ_i · (t − ⟨t⟩), then minimise

    Σ_i Σ_j w_ij · (δy_ij + λ(R_i; α, β, R_c, p)·δt_ij)²

with per-sample weight w_ij = 1/Σ_j δt_ij² so each segment contributes
exactly one unit of leverage to the global RSS, regardless of duration
or sample count. (The earlier `fit_saturating.py` used w_ij = 1/n_i,
which left segment leverage proportional to duration² and let long
low-R segments dominate.) Inner loop: linear weighted LS for (α, β) at
fixed (R_c, p). Outer: 2D grid scan over (R_c, p).

The plot overlays the new Hill curve, the shipped power-law for
reference, and the per-segment λ̂_i extracted from the same trajectories
(error bars are slope-σ from the log-linear regression). Visual sanity
check: the new curve should pass through the per-segment cloud.
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

ROOT = Path(__file__).resolve().parent.parent
ALL_SPINDOWNS_CSV = ROOT / "data/calibration/all_spindowns.csv"
OUT_DIR = ROOT / "data/calibration/hill_fit"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Shipped power-law constants — printed/plotted alongside as reference.
SHIPPED_ALPHA = 0.000932
SHIPPED_BETA = 0.0355
SHIPPED_P = 1.33


def collect():
    """Read all_spindowns.csv and return one segment per (id) with shape
    {src, R, occ, t, omega, method}.

    BLE/CSC entries are *excluded* from the modelling fit. Reasoning:
    BLE per-rev tops out at the last completed crank revolution (~5–10
    rpm), so it never sees ω→0; at high R that means fits over a narrow
    high-ω band, and any rider-input contamination during the 3–4-second
    decay window biases λ low. Video covers the same R range (R = 0 to
    93) with full ω→0 dynamic range, so it's strictly more informative
    for fitting λ(R). BLE is retained in all_spindowns.csv for the
    downstream end-to-end correction step (the bike is what we're
    correcting, so we need to know what it reports)."""
    if not ALL_SPINDOWNS_CSV.exists():
        sys.exit(f"missing {ALL_SPINDOWNS_CSV} — run aggregate_spindowns.py first")
    by_id: dict[int, dict] = defaultdict(lambda: {"t": [], "omega": []})
    with ALL_SPINDOWNS_CSV.open() as f:
        for row in csv.DictReader(f):
            sid = int(row["id"])
            s = by_id[sid]
            if "source" not in s:
                src_full = row["source"]
                s["src"] = "ble" if src_full.startswith("ble") else "video"
                s["source"] = src_full
                s["R"] = int(row["R"])
                s["occ"] = int(row["occ"])
                s["method"] = row["method"]
            s["t"].append(float(row["t_s"]))
            s["omega"].append(float(row["omega_rad_s"]))
    out = []
    for sid in sorted(by_id):
        s = by_id[sid]
        if s["src"] == "ble":
            continue
        s["t"] = np.asarray(s["t"], dtype=float)
        s["omega"] = np.asarray(s["omega"], dtype=float)
        out.append(s)
    return out


# ---------------------------------------------------------------------------
# Per-segment slope estimates (just for plotting + diagnostics).
# ---------------------------------------------------------------------------

def per_segment_lambda(segments):
    """Linear regression of ln(ω) on t for each segment. Returns
    (R, λ̂, σ_λ, src, occ, n, dur) per spindown, dropping segments where
    ω goes nonpositive or the fit collapses."""
    out = []
    for s in segments:
        t = np.asarray(s["t"], dtype=float)
        omega = np.asarray(s["omega"], dtype=float)
        if len(t) < 4 or (omega <= 0).any():
            continue
        ln_omega = np.log(omega)
        dt = t - t.mean()
        dy = ln_omega - ln_omega.mean()
        S_tt = float((dt * dt).sum())
        if S_tt < 1e-9:
            continue
        slope = float((dt * dy).sum()) / S_tt
        lam = -slope
        if lam <= 0:
            continue
        resid = dy - slope * dt
        n = len(t)
        sigma_y2 = float((resid * resid).sum()) / max(n - 2, 1)
        sigma_lam = math.sqrt(max(sigma_y2 / S_tt, 0.0))
        # Floor σ to keep one quirky segment from dominating the plot.
        sigma_lam = max(sigma_lam, 0.005)
        out.append({
            "R": s["R"], "occ": s["occ"], "src": s["src"],
            "lam": lam, "sigma": sigma_lam,
            "n": n, "dur": float(t[-1] - t[0]),
        })
    return out


# ---------------------------------------------------------------------------
# Trajectory fit at fixed (R_c, p): solve weighted LS for (α, β).
# ---------------------------------------------------------------------------

def fit_alpha_beta(segments, Rc: float, p: float,
                   beta_fixed: float | None = None):
    """Minimise Σ w_ij · (δy_ij + λ_i·δt_ij)² with λ_i = β + α·u_i,
    u_i = R_i^p / (R_i^p + R_c^p). Returns (α, β, total_wrss) or None.

    If `beta_fixed` is given, β is held at that value and only α is
    solved. This pins λ(R=0) to the directly-measured value (median of
    the per-segment λ̂ at R=0) — physically the most honest β, even
    though letting it float gives a slightly tighter RSS. The fit's
    R<10 region carries negligible weight in practice (you don't ride
    there) and α dominates above R≈15.
    """
    if beta_fixed is None:
        # Solve for both α and β.
        A11 = A12 = A22 = b1 = b2 = 0.0
        for s in segments:
            if len(s["t"]) < 4:
                continue
            omega = np.asarray(s["omega"], dtype=float)
            if (omega <= 0).any():
                continue
            R = float(s["R"])
            u = (R ** p) / (R ** p + Rc ** p) if R > 0 else 0.0
            t = np.asarray(s["t"], dtype=float)
            dt = t - t.mean()
            dy = np.log(omega) - np.log(omega).mean()
            S_tt = float((dt * dt).sum())
            if S_tt < 1e-9:
                continue
            w = 1.0 / S_tt
            x1 = u * dt
            x2 = dt
            A11 += w * float((x1 * x1).sum())
            A12 += w * float((x1 * x2).sum())
            A22 += w * float((x2 * x2).sum())
            b1 += -w * float((x1 * dy).sum())
            b2 += -w * float((x2 * dy).sum())
        det = A11 * A22 - A12 * A12
        if abs(det) < 1e-18:
            return None
        alpha = (A22 * b1 - A12 * b2) / det
        beta = (A11 * b2 - A12 * b1) / det
    else:
        # β fixed → 1D linear regression for α only.
        # Residual: δy_j + (α·u + β_fixed)·δt_j → solve for α.
        A11 = b1 = 0.0
        for s in segments:
            if len(s["t"]) < 4:
                continue
            omega = np.asarray(s["omega"], dtype=float)
            if (omega <= 0).any():
                continue
            R = float(s["R"])
            u = (R ** p) / (R ** p + Rc ** p) if R > 0 else 0.0
            t = np.asarray(s["t"], dtype=float)
            dt = t - t.mean()
            dy = np.log(omega) - np.log(omega).mean()
            S_tt = float((dt * dt).sum())
            if S_tt < 1e-9:
                continue
            w = 1.0 / S_tt
            # δy_j + α·u·δt_j + β_fixed·δt_j ≈ 0
            # → α·u·δt_j ≈ -δy_j - β_fixed·δt_j
            x1 = u * dt
            rhs = -dy - beta_fixed * dt
            A11 += w * float((x1 * x1).sum())
            b1 += w * float((x1 * rhs).sum())
        if A11 < 1e-18:
            return None
        alpha = b1 / A11
        beta = beta_fixed

    # Total weighted RSS.
    rss = 0.0
    for s in segments:
        if len(s["t"]) < 4:
            continue
        omega = np.asarray(s["omega"], dtype=float)
        if (omega <= 0).any():
            continue
        R = float(s["R"])
        u = (R ** p) / (R ** p + Rc ** p) if R > 0 else 0.0
        lam_R = alpha * u + beta
        t = np.asarray(s["t"], dtype=float)
        dt = t - t.mean()
        dy = np.log(omega) - np.log(omega).mean()
        S_tt = float((dt * dt).sum())
        if S_tt < 1e-9:
            continue
        w = 1.0 / S_tt
        pred = -lam_R * dt
        rss += w * float(((dy - pred) ** 2).sum())

    return alpha, beta, rss, 0.0


def grid_search(segments, Rc_grid, p_grid,
                beta_fixed: float | None = None):
    best = None
    for Rc in Rc_grid:
        for p in p_grid:
            res = fit_alpha_beta(segments, Rc, p, beta_fixed=beta_fixed)
            if res is None:
                continue
            alpha, beta, rss, tw = res
            if alpha < 0 or beta < -0.005:
                continue
            if best is None or rss < best[0]:
                best = (rss, Rc, p, alpha, beta)
    return best


# ---------------------------------------------------------------------------
# Plotting + report.
# ---------------------------------------------------------------------------

def plot_lambda_vs_R(points, alpha, beta, Rc, p):
    R = np.array([d["R"] for d in points], dtype=float)
    lam = np.array([d["lam"] for d in points])
    sig = np.array([d["sigma"] for d in points])
    src = np.array([d["src"] for d in points])

    Rg = np.linspace(0, max(R.max() + 5, 100), 400)
    hill = beta + alpha * Rg ** p / (Rg ** p + Rc ** p)
    pl = SHIPPED_BETA + SHIPPED_ALPHA * np.where(Rg > 0, Rg ** SHIPPED_P, 0.0)

    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    for ax in axes:
        for label, mask, color in [("BLE/CSC", src == "ble", "#1f77b4"),
                                    ("video",  src == "video", "#ff7f0e")]:
            if not mask.any():
                continue
            ax.errorbar(R[mask], lam[mask], yerr=sig[mask],
                        fmt="o", ms=5, color=color,
                        ecolor=color, elinewidth=0.8, capsize=2,
                        alpha=0.85, label=f"{label} (n={mask.sum()})")
        ax.plot(Rg, hill, color="#2ca02c", lw=2.0,
                label=(f"Hill: λ = {beta:.4f} + {alpha:.3f}·R^{p:.2f}/"
                       f"(R^{p:.2f} + {Rc:.0f}^{p:.2f})"))
        ax.plot(Rg, pl, color="#d62728", lw=1.2, ls="--",
                label=f"shipped power-law (p={SHIPPED_P})")
        ax.axhline(beta + alpha, color="#2ca02c", lw=0.8, ls=":",
                   alpha=0.5, label=f"Hill asymptote = {beta + alpha:.2f}")
        ax.set_xlabel("resistance dial R")
        ax.set_ylabel("flywheel decay rate λ (1/s)")
        ax.grid(alpha=0.3)
        ax.legend(loc="best", fontsize=9)

    axes[0].set_title("linear")
    axes[1].set_title("log-y")
    axes[1].set_yscale("log")
    fig.suptitle("Hill λ(R) refit on full ω(t) trajectories",
                 fontsize=12, weight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.96])
    out = OUT_DIR / "lambda_vs_R.png"
    fig.savefig(out, dpi=130)
    plt.close()
    print(f"wrote {out}")


def measured_beta(segments) -> float:
    """Median per-segment λ at R=0 — the directly measured residual drag.
    Used as the pinned β for the Hill fit."""
    lams = []
    for s in segments:
        if s["R"] != 0:
            continue
        t = np.asarray(s["t"], dtype=float)
        om = np.asarray(s["omega"], dtype=float)
        if len(t) < 4 or (om <= 0).any():
            continue
        dt = t - t.mean()
        dy = np.log(om) - np.log(om).mean()
        denom = float((dt * dt).sum())
        if denom < 1e-9:
            continue
        slope = float((dt * dy).sum()) / denom
        lams.append(-slope)
    if not lams:
        return 0.04  # fallback; unlikely to trigger with curated dataset
    return float(np.median(lams))


def main():
    segments = collect()
    n_ble = sum(1 for s in segments if s["src"] == "ble")
    n_vid = sum(1 for s in segments if s["src"] == "video")
    print(f"collected {len(segments)} segments  "
          f"(BLE/CSC: {n_ble}, video: {n_vid})")

    # Pin β to the directly-measured residual drag at R=0. The
    # 2D-free fit lets β float to ~2× this value because Hill's R^p shape
    # can't simultaneously hit both the R=0 anchor and the steep R=10–30
    # rise; pinning β makes the low-R end physically honest. R<10 is
    # not a practical riding region, so the fit's RSS at R≥15 (where it
    # actually matters) barely changes.
    beta_pinned = measured_beta(segments)
    print(f"β pinned = {beta_pinned:.4f} 1/s "
          f"(median per-segment λ̂ at R=0)")

    # 2D grid scan, then refine.
    Rc_coarse = np.linspace(15.0, 250.0, 95)
    p_coarse = np.linspace(1.0, 6.0, 51)
    best = grid_search(segments, Rc_coarse, p_coarse,
                       beta_fixed=beta_pinned)
    if best is None:
        sys.exit("coarse grid search failed")
    rss0, Rc0, p0, a0, b0 = best
    Rc_fine = np.linspace(max(5.0, Rc0 * 0.7), Rc0 * 1.4, 80)
    p_fine = np.linspace(max(1.0, p0 - 0.4), min(6.0, p0 + 0.4), 81)
    best2 = grid_search(segments, Rc_fine, p_fine,
                        beta_fixed=beta_pinned)
    rss, Rc, p, alpha, beta = best2 if best2 is not None else best

    # Compare to the shipped power-law evaluated on the same loss.
    # It's a different functional form, so we evaluate its wRSS by
    # bypassing the (α,β) solve.
    pl_rss = 0.0
    for s in segments:
        if len(s["t"]) < 4:
            continue
        omega = np.asarray(s["omega"], dtype=float)
        if (omega <= 0).any():
            continue
        R = float(s["R"])
        lam_R = (SHIPPED_ALPHA * (R ** SHIPPED_P) if R > 0 else 0.0) + SHIPPED_BETA
        t = np.asarray(s["t"], dtype=float)
        dt = t - t.mean()
        dy = np.log(omega) - np.log(omega).mean()
        S_tt = float((dt * dt).sum())
        if S_tt < 1e-9:
            continue
        w = 1.0 / S_tt
        pred = -lam_R * dt
        pl_rss += w * float(((dy - pred) ** 2).sum())

    print("\n=== Hill fit ===")
    print(f"  α   = {alpha:.4f}        (saturation amplitude, 1/s)")
    print(f"  β   = {beta:.4f}         (residual drag at R=0, 1/s)")
    print(f"  R_c = {Rc:.2f}           (half-saturation dial)")
    print(f"  p   = {p:.3f}            (transition sharpness)")
    print(f"  asymptote α + β = {alpha + beta:.3f}  1/s  "
          f"(λ as R → ∞)")
    print(f"  weighted RSS (Hill):              {rss:.4f}")
    print(f"  weighted RSS (shipped power-law): {pl_rss:.4f}")
    if rss > 0:
        print(f"  Hill is {pl_rss / rss:.1f}× better on weighted RSS")

    points = per_segment_lambda(segments)
    print(f"\nper-segment λ̂ (n={len(points)}):")
    print(f"  {'R':>3} {'occ':>3} {'src':>5} "
          f"{'λ̂':>7} {'σ_λ':>7} {'n':>4} {'dur':>5}")
    for d in sorted(points, key=lambda x: (x["R"], x["occ"])):
        print(f"  {d['R']:>3} {d['occ']:>3} {d['src']:>5} "
              f"{d['lam']:>7.3f} {d['sigma']:>7.4f} "
              f"{d['n']:>4} {d['dur']:>5.1f}")

    plot_lambda_vs_R(points, alpha, beta, Rc, p)

    out_json = OUT_DIR / "fit.json"
    out_json.write_text(json.dumps({
        "alpha": alpha, "beta": beta, "Rc": Rc, "p": p,
        "asymptote": alpha + beta,
        "wrss_hill": rss, "wrss_shipped_powerlaw": pl_rss,
        "n_segments": len(segments),
    }, indent=2))
    print(f"\nwrote {out_json}")

    print("\n=== to apply ===")
    print(f"  bridge/lib/physics/calibration.dart:")
    print(f"    defaultAlpha   → {alpha:.4f}")
    print(f"    defaultBeta    → {beta:.4f}    (pinned to median R=0 λ̂)")
    print(f"    defaultRcDial  → {Rc:.2f}")
    print(f"    defaultPower   → {p:.3f}")
    print(f"  analysis/correct_power.py + analysis/pin_inertia.py:  same swap")
    print(f"  λ(R) = β + α · R^p / (R^p + R_c^p)")
    print(f"\nAlso re-run analysis/pin_inertia.py to re-anchor I_crank under")
    print(f"the new λ(R) shape, then propagate the new I_crank to")
    print(f"calibration.dart, correct_power.py, plot_surge_examples.py.")


if __name__ == "__main__":
    main()
