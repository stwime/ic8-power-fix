"""Diagnostic: is λ truly constant across ω within each spin-down?

Background: our model assumes brake torque τ ∝ ω (giving steady-state P ∝ ω²),
so a coastdown ω(t) is a pure exponential and a single λ describes it.
At high R the spin-downs only span low ω (~5 rad/s ceiling) because the
brake stops the wheel fast — yet riders pedal at ω ~ 8–12 rad/s. We're
extrapolating cad² up two octaves outside the calibration window, and the
user's experience says the bridge over-shoots IC8 there.

Test: extract λ_apparent(ω) = -d(ln ω)/dt locally along each coastdown,
group by R bin, and look for systematic ω-dependence.

  * Constant λ → λ_app is flat in ω (eddy-brake unsaturated regime, P=λIω²).
  * Saturated brake (τ → τ_max at high ω) → λ_app rises as ω drops.
  * Sub-quadratic regime (τ ∝ ω^q with q<1) → λ_app drifts with ω.

Sources scanned:
  * data/calibration/spin_downs_apr29.csv     (CSC per-rev, R ≤ 74)
  * data/calibration/spin_downs_apr30.csv     (CSC per-rev, R ≤ 80)
  * data/calibration/crank_video.csv          (video-derived, up to R = 89)

For BLE/CSC sources we use per-rev (Δt) → ω samples (1/1024 s timing).
For video we use cumulative-angle slope → ω samples at frame rate.
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_nrf_log import parse_log  # noqa: E402
from spindown_fit import (find_clean_coastdowns, _crank_rev_obs,  # noqa: E402
                          fit_decay)
from spindown_fit_video import (LOG as VIDEO_LOG, VIDEO_CSV,  # noqa: E402
                                integrate_to_cumulative,
                                load_video_modpi)
from spindown_fit_video_v3 import phase_lock_resample  # noqa: E402
from video_segment_bounds import detect_segment_bounds  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BLE_SOURCES = [
    ROOT / "data/calibration/spin_downs_apr29.csv",
    ROOT / "data/calibration/spin_downs_apr30.csv",
]
OUT_DIR = ROOT / "data/calibration/lambda_vs_omega"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Bridge defaults — for overlay reference.
LAMBDA_ALPHA = 0.000932
LAMBDA_BETA = 0.0355
LAMBDA_P = 1.33


def lam_model(R: float) -> float:
    return LAMBDA_ALPHA * (R ** LAMBDA_P if R > 0 else 0.0) + LAMBDA_BETA


# ---------------------------------------------------------------------------
# Local λ_apparent extraction: log-linear slope of ω vs t in a sliding window.
# ---------------------------------------------------------------------------

def local_lambda(t: np.ndarray, omega: np.ndarray,
                 window_n: int = 7) -> tuple[np.ndarray, np.ndarray]:
    """For samples (t_i, ω_i) compute λ_app at each i via log-linear fit
    on the n nearest samples (n = window_n, odd). Returns (ω_centers,
    lam_app). Drops windows where ω≤0 anywhere or the fit is degenerate.
    """
    n = len(t)
    if n < window_n:
        return np.array([]), np.array([])
    half = window_n // 2
    centers_w, lams = [], []
    for i in range(half, n - half):
        ts = t[i - half:i + half + 1]
        ws = omega[i - half:i + half + 1]
        if (ws <= 0).any():
            continue
        y = np.log(ws)
        x = ts - ts[half]
        # Linear least squares on (x, y) — slope = -λ_app.
        sx = x.sum(); sy = y.sum(); sxx = (x * x).sum(); sxy = (x * y).sum()
        denom = window_n * sxx - sx * sx
        if abs(denom) < 1e-12:
            continue
        slope = (window_n * sxy - sx * sy) / denom
        centers_w.append(float(ws[half]))
        lams.append(float(-slope))
    return np.asarray(centers_w), np.asarray(lams)


# ---------------------------------------------------------------------------
# BLE/CSC → per-rev (t_mid, ω) samples for one segment.
# ---------------------------------------------------------------------------

def ble_segment_to_omega(seg) -> tuple[np.ndarray, np.ndarray]:
    obs = _crank_rev_obs(seg)
    if len(obs) < 4:
        return np.array([]), np.array([])
    revs = np.array([o[0] for o in obs], dtype=float)
    et = np.array([o[1] for o in obs])
    d_revs = np.diff(revs)
    dt = np.diff(et)
    cad = 60.0 * d_revs / dt          # rpm over each interval
    omega = cad * math.pi / 30.0      # rad/s (crank)
    t_mid = 0.5 * (et[:-1] + et[1:])
    return t_mid, omega


# ---------------------------------------------------------------------------
# Video → time-series ω from cumulative angle, restricted to the spindown's
# motion range. Uses the same segment-bounds detection as fit_lambda_R_v3.
# ---------------------------------------------------------------------------

def video_segment_to_omega(seg, R, t_v, cum_all,
                           smooth_n: int = 7
                           ) -> tuple[np.ndarray, np.ndarray]:
    """Per-frame (t, ω) from cumulative angle (no phase-locking — keeps
    full video frame count, which is plentiful even at very high R since
    the brake decay is short but well-sampled). Sub-rev gravity-pendulum
    oscillation is preserved in the trace; we leave it visible rather
    than masking it. Light boxcar smoothing of the ω derivative reduces
    pixel-level PCA jitter without erasing the oscillation."""
    b = detect_segment_bounds(seg, t_v, cum_all, R=R)
    if b is None:
        return np.array([]), np.array([])
    i0, i1, _, _ = b
    if i1 <= i0 + 30:
        return np.array([]), np.array([])
    tt = t_v[i0:i1 + 1]
    cum = cum_all[i0:i1 + 1] - cum_all[i0]
    if cum[-1] - cum[0] == 0:
        return np.array([]), np.array([])
    cum = np.sign(cum[-1] - cum[0]) * cum

    n = len(tt)
    omega = np.zeros(n)
    for i in range(1, n - 1):
        if tt[i + 1] > tt[i - 1]:
            omega[i] = (cum[i + 1] - cum[i - 1]) / (tt[i + 1] - tt[i - 1])
    omega[0] = (cum[1] - cum[0]) / max(tt[1] - tt[0], 1e-6)
    omega[-1] = (cum[-1] - cum[-2]) / max(tt[-1] - tt[-2], 1e-6)
    if smooth_n > 1:
        k = np.ones(smooth_n) / smooth_n
        omega = np.convolve(omega, k, mode="same")
    return tt, omega


# ---------------------------------------------------------------------------
# Main: collect per-source samples, group by R bin, plot.
# ---------------------------------------------------------------------------

def collect_samples():
    """Return list of dicts: {source, R, omega, lam_app}."""
    samples = []  # one dict per (source, R, occ) with arrays inside

    # 1. BLE/CSC sources.
    for src in BLE_SOURCES:
        rows = list(csv.DictReader(open(src)))
        segs = find_clean_coastdowns(rows)
        per_R: dict[int, int] = {}
        for seg, R, _term in segs:
            occ = per_R.get(R, 0); per_R[R] = occ + 1
            t_mid, omega = ble_segment_to_omega(seg)
            if len(t_mid) < 8:
                continue
            ws, lams = local_lambda(t_mid, omega, window_n=7)
            if len(ws) == 0:
                continue
            # Single-exponential global λ as reference.
            fit = fit_decay(seg)
            lam_global = fit[0] if fit is not None else float("nan")
            samples.append({
                "src": "ble", "file": src.name, "R": R, "occ": occ,
                "omega": ws, "lam_app": lams, "lam_global": lam_global,
                "n_pts": len(ws),
            })

    # 2. Video source — only for R ≥ 33 where BLE/CSC's reach is poor.
    rows_v = parse_log(VIDEO_LOG)
    segs_v = find_clean_coastdowns(rows_v)
    t_v, ang_v = load_video_modpi(VIDEO_CSV)
    cum_all = integrate_to_cumulative(ang_v)
    per_R_v: dict[int, int] = {}
    for seg, R, _term in segs_v:
        occ = per_R_v.get(R, 0); per_R_v[R] = occ + 1
        if R < 33:
            continue  # video offers no advantage at low R
        t_mid, omega = video_segment_to_omega(seg, R, t_v, cum_all)
        if len(t_mid) < 5:
            continue
        # Local-window slope on phase-locked per-rev points.
        # Use window_n=5 since segments at R≥73 may have <10 revs total.
        wn = 7 if len(t_mid) >= 10 else 5
        ws, lams = local_lambda(t_mid, omega, window_n=wn)
        if len(ws) == 0:
            continue
        samples.append({
            "src": "video", "file": "crank_video.csv", "R": R, "occ": occ,
            "omega": ws, "lam_app": lams, "lam_global": float("nan"),
            "n_pts": len(ws),
        })

    return samples


def summarize(samples):
    by_R = {}
    for s in samples:
        by_R.setdefault(s["R"], []).append(s)
    print(f"\n{'R':>3} {'src':>5} {'occ':>3} {'n_pts':>5} "
          f"{'ω_min':>6} {'ω_max':>6} "
          f"{'λ@ω_lo':>7} {'λ@ω_hi':>7} {'rise%':>6}")
    for R in sorted(by_R.keys()):
        for s in by_R[R]:
            ws = s["omega"]; lams = s["lam_app"]
            if len(ws) == 0:
                continue
            order = np.argsort(ws)
            ws_s = ws[order]; lams_s = lams[order]
            n = len(ws_s)
            lo = max(1, n // 5)
            lam_lo = float(np.median(lams_s[:lo]))    # bottom 20% of ω
            lam_hi = float(np.median(lams_s[-lo:]))   # top 20% of ω
            rise = 100.0 * (lam_hi - lam_lo) / max(abs(lam_lo), 1e-6)
            print(f"{R:>3} {s['src']:>5} {s['occ']:>3} {s['n_pts']:>5} "
                  f"{ws.min():>6.2f} {ws.max():>6.2f} "
                  f"{lam_lo:>7.4f} {lam_hi:>7.4f} {rise:>+6.1f}")


# R bins for plotting (low/mid/high regimes).
R_BINS = [(0, 11), (11, 25), (25, 40), (40, 55), (55, 70), (70, 95)]


def plot_panels(samples):
    fig, axes = plt.subplots(2, 3, figsize=(15, 9), sharey=False)
    axes = axes.ravel()
    color_ble = "#1f77b4"
    color_vid = "#ff7f0e"
    for ax, (lo, hi) in zip(axes, R_BINS):
        in_bin = [s for s in samples if lo <= s["R"] < hi]
        Rs_in_bin = sorted({s["R"] for s in in_bin})
        ax.set_title(f"R ∈ [{lo}, {hi})  (n={len(in_bin)} segs, "
                     f"R={Rs_in_bin})", fontsize=10)
        for s in in_bin:
            color = color_ble if s["src"] == "ble" else color_vid
            ax.scatter(s["omega"], s["lam_app"], s=8, alpha=0.45,
                       color=color, edgecolor="none")
        # Overlay the bridge model's λ at the median R of the bin.
        if Rs_in_bin:
            R_med = float(np.median(Rs_in_bin))
            lam_m = lam_model(R_med)
            ax.axhline(lam_m, color="#555", lw=1.2, ls="--",
                       label=f"model λ(R={R_med:.0f}) = {lam_m:.3f}")
            ax.legend(loc="upper right", fontsize=8)
        ax.set_xlabel("ω (rad/s, crank)")
        ax.set_ylabel("λ_apparent (1/s)")
        ax.grid(alpha=0.3)

    # Legend hack: add color key.
    fig.text(0.01, 0.98, "blue = BLE/CSC per-rev,  orange = video frame-rate",
             fontsize=9, color="#444")
    plt.suptitle("λ_apparent vs ω within each spin-down — does λ depend on ω?",
                 fontsize=12, weight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.95])
    out = OUT_DIR / "lambda_vs_omega_panels.png"
    plt.savefig(out, dpi=130)
    plt.close()
    print(f"\nwrote {out}")


def plot_high_R_detail(samples):
    """One panel per R for R≥44, so each spin-down's λ(ω) is visible."""
    high = [s for s in samples if s["R"] >= 44]
    Rs = sorted({s["R"] for s in high})
    if not Rs:
        return
    n = len(Rs)
    cols = min(3, n); rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(5 * cols, 4 * rows),
                             squeeze=False)
    for ax, R in zip(axes.ravel(), Rs):
        for s in high:
            if s["R"] != R: continue
            color = "#1f77b4" if s["src"] == "ble" else "#ff7f0e"
            ax.scatter(s["omega"], s["lam_app"], s=12, alpha=0.6,
                       color=color, edgecolor="none",
                       label=f"{s['src']} occ{s['occ']}")
        lam_m = lam_model(R)
        ax.axhline(lam_m, color="#555", lw=1.2, ls="--",
                   label=f"model λ = {lam_m:.3f}")
        ax.set_title(f"R = {R}")
        ax.set_xlabel("ω (rad/s, crank)")
        ax.set_ylabel("λ_apparent (1/s)")
        ax.grid(alpha=0.3)
        ax.legend(loc="best", fontsize=8)
    # Hide unused axes
    for ax in axes.ravel()[len(Rs):]:
        ax.axis("off")
    plt.suptitle("Within-spindown λ(ω) at high R — flat = ω² holds; "
                 "sloped = breakdown",
                 fontsize=12, weight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    out = OUT_DIR / "lambda_vs_omega_high_R.png"
    plt.savefig(out, dpi=130)
    plt.close()
    print(f"wrote {out}")


def main():
    samples = collect_samples()
    if not samples:
        sys.exit("no samples")
    summarize(samples)
    plot_panels(samples)
    plot_high_R_detail(samples)


if __name__ == "__main__":
    main()
