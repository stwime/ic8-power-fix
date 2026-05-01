"""Trajectory-based refit of (α, β, p), with a saturating-torque option.

Why this exists
---------------
The earlier fit (analysis/fit_lambda_R_v3.py) reduced each spindown to a
single λ via per-segment exponential fit, then weighted-regressed the
resulting (R, λ) cloud to find (α, β, p). That approach inflates p
because the per-segment λ at high R is biased: high-R spindowns only
span low ω (ω≤5 rad/s after R≥44), so their fitted λ averages over a
narrow part of the ω-axis and the bias propagates into the (α, β, p)
aggregation, landing at p=1.646 with predictions that blow up at high
R + high cadence (≈ 975 W at R=50, cad=120).

This script fits (α, β, p, optionally ω*) directly to the FULL ω(t)
trajectory of every spindown, weighted so each segment contributes
equally regardless of how many rev events it carries. That gives a
much sharper picture of what (α, β, p) the trajectory shapes actually
imply.

Saturating-torque model (tested + rejected)
-------------------------------------------
A natural hypothesis for the cad² overshoot at high R was eddy-brake
saturation:

    τ(ω, R) = c(R)·ω*·tanh(ω/ω*)

  ω « ω* :  τ ≈ c(R)·ω    — eddy-brake low-speed limit (P ∝ ω²)
  ω » ω* :  τ ≈ c(R)·ω*   — saturated                 (P ∝ ω, flat τ)

with closed-form coastdown ω(t) = ω*·arcsinh[sinh(ω₀/ω*)·exp(-λ(R)·t)],
λ(R) = c(R)/I = α·R^p + β as before.

Result of the joint (α, β, p, ω*) fit on this dataset: ω* runs to ∞
(pure exponential) at all p values; the RSS landscape monotonically
prefers larger ω*. The within-spindown drop in λ_apparent visible at
high R (lambda_vs_omega_check.py) is plausible pendulum-noise inflation
at low ω rather than real saturation. The saturating model adds a
parameter without earning its keep on RSS.

What the fit actually finds
---------------------------
Pure-exponential, weighted-per-trajectory, p free:
    p ≈ 1.33  (vs the old 1.646)
    α ≈ 0.000932
    β ≈ 0.0355

This is a milder R-dependence than the old fit. Predictions at R=50,
cad=120 drop from 975 W to ≈ 300 W, matching rider intuition.

The saturating-torque code is kept here as a bypass: when ω* is included
in the search, the optimum picks ω*≫ω_max and the model collapses to
pure exp. The downstream pipeline (correct_power.py, bridge corrector)
just uses the pure-exp form with the new constants.

Fit method
----------
For each candidate (ω*, p) on a 2D grid:
  1. Per segment, transform observed ω samples to y = ln(sinh(ω/ω*)).
  2. Subtract per-segment means of y and t (eliminates ω₀ as a free
     parameter; this is the same trick as fitting a no-intercept linear
     regression on the centered data).
  3. With λ(R) = α·R^p + β, the model says δy = -λ(R)·δt per sample.
     Stack across all samples and solve weighted linear least squares
     for (α, β) given fixed (ω*, p).
  4. Tally weighted residual sum.

Pick (ω*, p, α, β) that minimises total weighted residual. Plot model
overlays per segment, print the new defaults, and write a JSON dump
that the bridge calibration update can read.

Sample weighting:
  Each segment is weighted to contribute 1 unit of total weight to the
  global fit. Within a segment, samples share that unit equally. This
  prevents the fit from being dominated by long low-R BLE segments
  (which have 30–50 revs each and are intrinsically exponential because
  they live entirely in the unsaturated regime) at the expense of the
  short high-R video segments (5–10 revs each, but the only data that
  probes the saturation transition).
"""
from __future__ import annotations

import csv
import math
import sys
import json
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_nrf_log import parse_log  # noqa: E402
from spindown_fit import (find_clean_coastdowns, _crank_rev_obs)  # noqa: E402
from spindown_fit_video import (LOG as VIDEO_LOG, VIDEO_CSV,  # noqa: E402
                                integrate_to_cumulative,
                                load_video_modpi)
from video_segment_bounds import detect_segment_bounds  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BLE_SOURCES = [
    ROOT / "data/calibration/spin_downs_apr29.csv",
    ROOT / "data/calibration/spin_downs_apr30.csv",
]
OUT_DIR = ROOT / "data/calibration/saturating_fit"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Effective inertia (kg·m²) used to translate λ ↔ c. Pinned outside this
# script (analysis/pin_inertia.py); we only need a value here to print the
# implied torque magnitudes — the (α, β, p, ω*) fit doesn't depend on I.
I_REF = 9.3


# ---------------------------------------------------------------------------
# Per-segment (t, ω) extraction.
# ---------------------------------------------------------------------------

def ble_segment_samples(seg) -> tuple[np.ndarray, np.ndarray] | None:
    obs = _crank_rev_obs(seg)
    if len(obs) < 4:
        return None
    revs = np.array([o[0] for o in obs], dtype=float)
    et = np.array([o[1] for o in obs])
    d_revs = np.diff(revs)
    dt = np.diff(et)
    omega = 2.0 * math.pi * d_revs / dt
    t_mid = 0.5 * (et[:-1] + et[1:])
    return t_mid, omega


def video_segment_samples(seg, R, t_v, cum_all) -> tuple[np.ndarray, np.ndarray] | None:
    b = detect_segment_bounds(seg, t_v, cum_all, R=R)
    if b is None:
        return None
    i0, i1, _, _ = b
    if i1 <= i0 + 30:
        return None
    tt = t_v[i0:i1 + 1]
    cum = cum_all[i0:i1 + 1] - cum_all[i0]
    if cum[-1] - cum[0] == 0:
        return None
    cum = np.sign(cum[-1] - cum[0]) * cum
    # ω from central differences on the cumulative angle.
    n = len(tt)
    omega = np.zeros(n)
    for i in range(1, n - 1):
        if tt[i + 1] > tt[i - 1]:
            omega[i] = (cum[i + 1] - cum[i - 1]) / (tt[i + 1] - tt[i - 1])
    omega[0] = (cum[1] - cum[0]) / max(tt[1] - tt[0], 1e-6)
    omega[-1] = (cum[-1] - cum[-2]) / max(tt[-1] - tt[-2], 1e-6)
    # Light boxcar smooth on ω to suppress per-pixel PCA jitter (preserves
    # gravity-pendulum oscillation, which averages out across many revs).
    if n >= 7:
        k = np.ones(7) / 7
        omega = np.convolve(omega, k, mode="same")
    # Drop tail where ω dips below ~0.3 rad/s — that's near the wheel-stop
    # noise floor, where central differences are dominated by quantisation.
    keep = omega >= 0.3
    if keep.sum() < 6:
        return None
    return tt[keep], omega[keep]


def collect():
    """Return list of dicts: {src, R, occ, t, omega, w} where w is the
    per-sample weight, set so each segment contributes 1 unit total."""
    segments = []

    # BLE/CSC.
    for src in BLE_SOURCES:
        rows = list(csv.DictReader(open(src)))
        segs = find_clean_coastdowns(rows)
        per_R: dict[int, int] = {}
        for seg, R, _term in segs:
            occ = per_R.get(R, 0); per_R[R] = occ + 1
            res = ble_segment_samples(seg)
            if res is None:
                continue
            t, omega = res
            segments.append({"src": "ble", "file": src.name, "R": R, "occ": occ,
                             "t": t, "omega": omega, "w": 1.0 / len(t)})

    # Video.
    rows_v = parse_log(VIDEO_LOG)
    segs_v = find_clean_coastdowns(rows_v)
    t_v, ang_v = load_video_modpi(VIDEO_CSV)
    cum_all = integrate_to_cumulative(ang_v)
    per_R_v: dict[int, int] = {}
    for seg, R, _term in segs_v:
        occ = per_R_v.get(R, 0); per_R_v[R] = occ + 1
        res = video_segment_samples(seg, R, t_v, cum_all)
        if res is None:
            continue
        t, omega = res
        segments.append({"src": "video", "file": "crank_video.csv", "R": R, "occ": occ,
                         "t": t, "omega": omega, "w": 1.0 / len(t)})
    return segments


# ---------------------------------------------------------------------------
# Fit core: at fixed (ω*, p), solve weighted LS for (α, β).
# ---------------------------------------------------------------------------

def y_transform(omega: np.ndarray, omega_star: float) -> np.ndarray:
    """y = ln(sinh(ω / ω*)). Numerically stable for u = ω/ω* up to ~50."""
    u = omega / omega_star
    # log(sinh(u)) = log((e^u - e^-u)/2). For u < 0.5 use series, else direct.
    out = np.empty_like(u)
    small = u < 0.3
    if small.any():
        us = u[small]
        out[small] = np.log(us) + np.log1p(us * us / 6.0 + us ** 4 / 120.0)
    big = ~small
    if big.any():
        ub = u[big]
        # log(sinh(u)) = u + log(1 - exp(-2u)) - log(2)
        out[big] = ub + np.log1p(-np.exp(-2.0 * ub)) - math.log(2)
    return out


def fit_alpha_beta(segments, omega_star: float, p: float):
    """At fixed (ω*, p), solve weighted LS for (α, β). Returns
    (alpha, beta, total_wrss, residual_per_sample_count_used)."""
    # Centered linear regression: δy = -λ(R)·δt with λ(R) = α·u + β,
    # u = R^p. So δy = -(α·u + β)·δt → flatten across all (segment, sample):
    #   for sample j of segment i with weight w_i:
    #     residual = δy_ij + α·(u_i·δt_ij) + β·(δt_ij)
    # Standard 2D linear regression in (X1=u_i·δt, X2=δt) against -δy.
    # Normal equations:
    #   [Σ w·X1²    Σ w·X1·X2 ] [α]   [-Σ w·X1·δy]
    #   [Σ w·X1·X2  Σ w·X2²   ] [β] = [-Σ w·X2·δy]

    A11 = A12 = A22 = b1 = b2 = 0.0
    n_samples = 0
    total_w = 0.0
    for s in segments:
        if len(s["t"]) < 4:
            continue
        u_i = (s["R"] ** p) if s["R"] > 0 else 0.0
        y = y_transform(s["omega"], omega_star)
        t = s["t"]
        if not np.isfinite(y).all():
            continue
        dt = t - t.mean()
        dy = y - y.mean()
        w = s["w"]
        x1 = u_i * dt
        x2 = dt
        A11 += w * (x1 * x1).sum()
        A12 += w * (x1 * x2).sum()
        A22 += w * (x2 * x2).sum()
        b1 += -w * (x1 * dy).sum()
        b2 += -w * (x2 * dy).sum()
        n_samples += len(t)
        total_w += w * len(t)

    det = A11 * A22 - A12 * A12
    if abs(det) < 1e-20:
        return None
    alpha = (A22 * b1 - A12 * b2) / det
    beta = (A11 * b2 - A12 * b1) / det

    # Residuals.
    rss = 0.0
    for s in segments:
        if len(s["t"]) < 4:
            continue
        u_i = (s["R"] ** p) if s["R"] > 0 else 0.0
        lam_R = alpha * u_i + beta
        y = y_transform(s["omega"], omega_star)
        t = s["t"]
        if not np.isfinite(y).all():
            continue
        dt = t - t.mean()
        dy = y - y.mean()
        pred = -lam_R * dt
        rss += s["w"] * float(((dy - pred) ** 2).sum())

    return alpha, beta, rss, total_w


def grid_search(segments,
                omega_star_grid: np.ndarray,
                p_grid: np.ndarray):
    best = None
    for ws in omega_star_grid:
        for p in p_grid:
            res = fit_alpha_beta(segments, ws, p)
            if res is None:
                continue
            alpha, beta, rss, tw = res
            if alpha < 0 or beta < -0.01:
                continue
            if best is None or rss < best[0]:
                best = (rss, ws, p, alpha, beta, tw)
    return best


# ---------------------------------------------------------------------------
# Diagnostics.
# ---------------------------------------------------------------------------

def predict_omega(t_rel: np.ndarray, omega0: float,
                  lam_R: float, omega_star: float) -> np.ndarray:
    """ω(t) = ω*·arcsinh(sinh(ω₀/ω*)·exp(-λ_R·t))."""
    # Compute log(sinh(u₀)) without overflow.
    u0 = omega0 / omega_star
    if u0 > 30:
        log_sinh_u0 = u0 - math.log(2)
    elif u0 > 0.3:
        log_sinh_u0 = math.log(math.sinh(u0))
    else:
        log_sinh_u0 = math.log(u0) + math.log1p(u0 * u0 / 6.0)
    log_sinh_t = log_sinh_u0 - lam_R * t_rel
    sinh_t = np.exp(log_sinh_t)
    u_t = np.arcsinh(sinh_t)
    return omega_star * u_t


def per_segment_lambdas(segments, omega_star: float):
    """Recover the per-segment λ at fixed ω*, by linear regression of
    centered (δt, δy). Just for plotting/diagnostics."""
    rows = []
    for s in segments:
        if len(s["t"]) < 4:
            continue
        y = y_transform(s["omega"], omega_star)
        t = s["t"]
        if not np.isfinite(y).all():
            continue
        dt = t - t.mean()
        dy = y - y.mean()
        denom = float((dt * dt).sum())
        if denom < 1e-12:
            continue
        slope = float((dt * dy).sum()) / denom
        lam = -slope
        rows.append({"R": s["R"], "src": s["src"], "occ": s["occ"],
                     "lam": lam, "n": len(t), "w": s["w"]})
    return rows


def plot_lambda_vs_R(segments, omega_star, alpha, beta, p):
    rows = per_segment_lambdas(segments, omega_star)
    Rs = np.array([r["R"] for r in rows], float)
    lams = np.array([r["lam"] for r in rows])
    src = np.array([r["src"] for r in rows])
    fig, ax = plt.subplots(figsize=(8, 5))
    for label, mask, color in [("BLE/CSC", src == "ble", "#1f77b4"),
                                 ("video",   src == "video", "#ff7f0e")]:
        if not mask.any():
            continue
        ax.scatter(Rs[mask], lams[mask], s=40, alpha=0.7, color=color,
                   edgecolor="white", linewidth=0.7,
                   label=f"{label} (n={mask.sum()})")
    rline = np.linspace(0, max(Rs.max() + 5, 100), 200)
    pred = alpha * np.where(rline > 0, rline ** p, 0.0) + beta
    ax.plot(rline, pred, color="#d62728", lw=2,
            label=f"fit: λ(R) = {alpha:.5f}·R^{p:.3f} + {beta:.4f}")
    ax.set_xlabel("R (dial)")
    ax.set_ylabel("λ (1/s)  — recovered with ω* = " f"{omega_star:.2f} rad/s")
    ax.set_title("Per-segment λ from saturating fit, vs (α, β, p) global form",
                 fontsize=11, weight="bold")
    ax.grid(alpha=0.3); ax.legend(loc="best", fontsize=9)
    fig.tight_layout()
    out = OUT_DIR / "lambda_vs_R.png"
    fig.savefig(out, dpi=130); plt.close()
    print(f"wrote {out}")


def plot_overlays(segments, omega_star, alpha, beta, p):
    """For each segment, overlay observed ω(t) with model prediction."""
    Rs = sorted({s["R"] for s in segments})
    n = len(Rs)
    cols = 4
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(4.5 * cols, 3.0 * rows),
                             squeeze=False)
    for ax, R in zip(axes.ravel(), Rs):
        for s in segments:
            if s["R"] != R:
                continue
            color = "#1f77b4" if s["src"] == "ble" else "#ff7f0e"
            t = s["t"] - s["t"][0]
            omega = s["omega"]
            ax.plot(t, omega, "o", ms=3, alpha=0.5, color=color,
                    label=f"{s['src']} occ{s['occ']}")
            lam_R = alpha * (R ** p if R > 0 else 0.0) + beta
            tp = np.linspace(0, t[-1], 200)
            op = predict_omega(tp, omega[0], lam_R, omega_star)
            ax.plot(tp, op, color=color, lw=1.4, ls="--", alpha=0.9)
        ax.set_title(f"R = {R}", fontsize=10)
        ax.set_xlabel("t (s)"); ax.set_ylabel("ω (rad/s)")
        ax.grid(alpha=0.3)
        ax.legend(loc="best", fontsize=7)
    for ax in axes.ravel()[len(Rs):]:
        ax.axis("off")
    fig.suptitle(f"ω(t) per segment vs saturating-tanh fit "
                 f"(ω* = {omega_star:.2f}, p = {p:.3f})",
                 fontsize=12, weight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.97])
    out = OUT_DIR / "omega_t_overlays.png"
    fig.savefig(out, dpi=130); plt.close()
    print(f"wrote {out}")


def main():
    segments = collect()
    n_ble = sum(1 for s in segments if s["src"] == "ble")
    n_vid = sum(1 for s in segments if s["src"] == "video")
    print(f"collected {len(segments)} segments  (BLE/CSC: {n_ble}, video: {n_vid})")

    # 2D grid scan: ω* on log scale (0.5–30), p on linear scale (1.0–2.5).
    omega_star_grid = np.exp(np.linspace(math.log(0.5), math.log(30), 60))
    p_grid = np.linspace(1.0, 2.5, 31)
    best = grid_search(segments, omega_star_grid, p_grid)
    if best is None:
        sys.exit("grid search failed")
    rss0, ws0, p0, a0, b0, tw0 = best
    # Refine near optimum.
    omega_star_grid_2 = np.exp(np.linspace(math.log(ws0 * 0.7),
                                            math.log(ws0 * 1.4), 80))
    p_grid_2 = np.linspace(max(1.0, p0 - 0.2), min(2.5, p0 + 0.2), 41)
    best2 = grid_search(segments, omega_star_grid_2, p_grid_2)
    rss, ws, p, alpha, beta, tw = best2 if best2 is not None else best

    # Compare to old pure-exponential model: limit ω* → ∞, fit (α, β, p).
    # In that limit y_transform ≈ ln(ω) - const, which recovers the old fit.
    omega_star_inf = 1e3  # effectively infinite — sinh ≈ exp at u<<1
    best_old = grid_search(segments, np.array([omega_star_inf]),
                            np.linspace(1.0, 2.5, 51))
    rss_old = best_old[0] if best_old is not None else float("nan")

    print("\n=== fit results ===")
    print(f"  ω*  = {ws:.3f} rad/s        ( = {ws*60/(2*math.pi):.1f} rpm crank,"
          f"  saturation onset cadence )")
    print(f"  p   = {p:.4f}")
    print(f"  α   = {alpha:.6e}")
    print(f"  β   = {beta:.4f}")
    print(f"  weighted RSS (saturating): {rss:.4f}   (n_samples_w = {tw:.1f})")
    print(f"  weighted RSS (pure exp):   {rss_old:.4f}")
    if math.isfinite(rss_old) and rss_old > 0:
        print(f"  improvement ratio: {rss_old/rss:.2f}×")

    # τ_max(R) implied magnitudes — useful for sanity check.
    print(f"\n=== implied torque (using I_ref = {I_REF}) ===")
    print(f"  {'R':>3}  {'λ(R)':>6}  {'τ_max=c·ω*':>10}  "
          f"{'P at cad=120':>13}")
    for R in [10, 30, 50, 70, 89]:
        lam = alpha * (R ** p) + beta
        c = lam * I_REF                # c(R) = λ(R)·I
        tau_max = c * ws               # saturated torque
        cad = 120; ome = cad * math.pi / 30.0
        P = c * ws * math.tanh(ome / ws) * ome
        print(f"  {R:>3}  {lam:>6.3f}  {tau_max:>10.2f}  {P:>13.0f} W")

    plot_lambda_vs_R(segments, ws, alpha, beta, p)
    plot_overlays(segments, ws, alpha, beta, p)

    # Dump for downstream consumers.
    out_json = OUT_DIR / "fit.json"
    out_json.write_text(json.dumps({
        "alpha": alpha, "beta": beta, "p": p, "omega_star": ws,
        "wrss": rss, "wrss_pure_exp": rss_old,
        "n_segments": len(segments),
        "i_ref_used_for_torque_print_only": I_REF,
    }, indent=2))
    print(f"\nwrote {out_json}")


if __name__ == "__main__":
    main()
