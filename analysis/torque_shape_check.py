"""Diagnostic: what's the actual shape of brake torque τ(ω)?

Our model assumes τ_brake = c·ω (linear in ω), which gives steady-state
P = τ·ω = c·ω² and a pure-exponential coastdown ω(t) = ω₀·e^(-λt).

In a coastdown there's no rider input, so I·ω̇ = -τ(ω). Plotting (ω, -I·ω̇)
across a spin-down probes τ(ω) directly:

    pure ω² physics    →  τ ∝ ω,    -ω̇ vs ω is linear through origin
    air-drag dominant  →  τ ∝ ω²,   -ω̇ vs ω curves up
    saturated brake    →  τ ≈ τ_max, -ω̇ vs ω flattens at high ω
    Coulomb floor      →  τ has +const, -ω̇ has positive y-intercept

Equivalently, fitting log(-ω̇) = log(A) + q·log(ω) gives the torque
exponent q. q=1 is the standard eddy-brake low-speed limit; q<1 is what
you'd see in the saturated regime. We fit per-spindown q and look at
how it varies with R, including the high-R video data (R 44-89) where
the BLE/CSC fits run out of revs.

Sources:
  * data/calibration/spin_downs_apr29.csv  (BLE/CSC per-rev, R ≤ 74)
  * data/calibration/spin_downs_apr30.csv  (BLE/CSC per-rev, R ≤ 80)
  * data/calibration/spin_downs_super_high_r.csv (BLE/CSC, R = 80)
  * data/calibration/crank_video.csv       (video phase-locked, R 33-89)
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
from spindown_fit import find_clean_coastdowns, _crank_rev_obs  # noqa: E402
from spindown_fit_video import (LOG as VIDEO_LOG, VIDEO_CSV,  # noqa: E402
                                integrate_to_cumulative,
                                load_video_modpi)
from spindown_fit_video_v3 import phase_lock_resample  # noqa: E402
from video_segment_bounds import detect_segment_bounds  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BLE_SOURCES = [
    ROOT / "data/calibration/spin_downs_apr29.csv",
    ROOT / "data/calibration/spin_downs_apr30.csv",
    ROOT / "data/calibration/spin_downs_super_high_r.csv",
]
OUT_DIR = ROOT / "data/calibration/lambda_vs_omega"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Bridge defaults — for overlay reference.
LAMBDA_ALPHA = 0.000932
LAMBDA_BETA = 0.0355
LAMBDA_P = 1.33
I_CRANK = 22.9  # kg·m² — pinned by outdoor anchor


def lam_model(R: float) -> float:
    return LAMBDA_ALPHA * (R ** LAMBDA_P if R > 0 else 0.0) + LAMBDA_BETA


# ---------------------------------------------------------------------------
# Per-rev (t_mid, ω) extraction.
# ---------------------------------------------------------------------------

def ble_rev_omegas(seg) -> tuple[np.ndarray, np.ndarray]:
    obs = _crank_rev_obs(seg)
    if len(obs) < 4:
        return np.array([]), np.array([])
    revs = np.array([o[0] for o in obs], dtype=float)
    et = np.array([o[1] for o in obs])
    d_revs = np.diff(revs)
    dt = np.diff(et)
    omega = 2.0 * math.pi * d_revs / dt
    t_mid = 0.5 * (et[:-1] + et[1:])
    return t_mid, omega


def video_rev_omegas(seg, R, t_v, cum_all) -> tuple[np.ndarray, np.ndarray]:
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
    t_rev, _ = phase_lock_resample(tt, cum)
    if len(t_rev) < 3:
        return np.array([]), np.array([])
    dt = np.diff(t_rev)
    omega = 2.0 * math.pi / dt
    t_mid = 0.5 * (t_rev[:-1] + t_rev[1:])
    return t_mid, omega


# ---------------------------------------------------------------------------
# Per-segment dω/dt at each rev midpoint (central differences).
# ---------------------------------------------------------------------------

def omega_dot(t_mid: np.ndarray, omega: np.ndarray
              ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (ω_centers, ω̇_at_centers, t_centers). Central differences
    drop the two endpoints."""
    n = len(t_mid)
    if n < 3:
        return np.array([]), np.array([]), np.array([])
    w_c, wd_c, t_c = [], [], []
    for i in range(1, n - 1):
        if t_mid[i + 1] <= t_mid[i - 1]:
            continue
        wd_c.append((omega[i + 1] - omega[i - 1]) / (t_mid[i + 1] - t_mid[i - 1]))
        w_c.append(omega[i])
        t_c.append(t_mid[i])
    return np.asarray(w_c), np.asarray(wd_c), np.asarray(t_c)


def fit_torque_exponent(omega: np.ndarray, omega_dot_arr: np.ndarray
                         ) -> tuple[float, float, int] | None:
    """Fit log(-ω̇) = log(A) + q·log(ω). Returns (A, q, n) or None.
    Drops samples where ω̇ ≥ 0 (transient noise / acceleration)."""
    m = (omega_dot_arr < -1e-4) & (omega > 0.2)
    if m.sum() < 3:
        return None
    x = np.log(omega[m])
    y = np.log(-omega_dot_arr[m])
    sx = x.sum(); sy = y.sum(); sxx = (x * x).sum(); sxy = (x * y).sum()
    n = m.sum()
    denom = n * sxx - sx * sx
    if abs(denom) < 1e-12:
        return None
    q = (n * sxy - sx * sy) / denom
    log_A = (sy - q * sx) / n
    return float(math.exp(log_A)), float(q), int(n)


# ---------------------------------------------------------------------------
# Collect per-segment τ(ω) data.
# ---------------------------------------------------------------------------

def collect_segments():
    """Return list of {src, file, R, occ, omega, omega_dot, q, A, lam_global}."""
    out = []

    # 1. BLE/CSC.
    for src in BLE_SOURCES:
        rows = list(csv.DictReader(open(src)))
        segs = find_clean_coastdowns(rows)
        per_R: dict[int, int] = {}
        for seg, R, _term in segs:
            occ = per_R.get(R, 0); per_R[R] = occ + 1
            t_mid, omega = ble_rev_omegas(seg)
            if len(t_mid) < 4:
                continue
            w_c, wd_c, _ = omega_dot(t_mid, omega)
            if len(w_c) < 3:
                continue
            fit = fit_torque_exponent(w_c, wd_c)
            if fit is None:
                continue
            A, q, n = fit
            out.append({"src": "ble", "file": src.name, "R": R, "occ": occ,
                        "omega": w_c, "omega_dot": wd_c,
                        "A": A, "q": q, "n": n})

    # 2. Video.
    rows_v = parse_log(VIDEO_LOG)
    segs_v = find_clean_coastdowns(rows_v)
    t_v, ang_v = load_video_modpi(VIDEO_CSV)
    cum_all = integrate_to_cumulative(ang_v)
    per_R_v: dict[int, int] = {}
    for seg, R, _term in segs_v:
        occ = per_R_v.get(R, 0); per_R_v[R] = occ + 1
        if R < 33:
            continue
        t_mid, omega = video_rev_omegas(seg, R, t_v, cum_all)
        if len(t_mid) < 4:
            continue
        w_c, wd_c, _ = omega_dot(t_mid, omega)
        if len(w_c) < 3:
            continue
        fit = fit_torque_exponent(w_c, wd_c)
        if fit is None:
            continue
        A, q, n = fit
        out.append({"src": "video", "file": "crank_video.csv", "R": R, "occ": occ,
                    "omega": w_c, "omega_dot": wd_c,
                    "A": A, "q": q, "n": n})
    return out


def summarize(segs):
    """Per-segment torque exponent q, plus τ at ω=5 & ω=10 from the fit."""
    print(f"\n{'R':>3} {'src':>5} {'occ':>3} {'n':>3} "
          f"{'ω_min':>6} {'ω_max':>6} "
          f"{'A':>7} {'q':>6}  τ(ω=5) τ(ω=10) "
          f"P(ω=10)")
    for s in sorted(segs, key=lambda s: (s["R"], s["src"], s["occ"])):
        # Steady-state P = τ·ω = A·I·ω^(q+1)  (and our model says A·I·ω²).
        tau5 = s["A"] * I_CRANK * 5 ** s["q"]
        tau10 = s["A"] * I_CRANK * 10 ** s["q"]
        P10 = tau10 * 10
        print(f"{s['R']:>3} {s['src']:>5} {s['occ']:>3} {s['n']:>3} "
              f"{s['omega'].min():>6.2f} {s['omega'].max():>6.2f} "
              f"{s['A']:>7.4f} {s['q']:>6.3f}  "
              f"{tau5:>6.2f} {tau10:>7.2f} {P10:>7.0f}")


def plot_torque_vs_omega(segs):
    """Per-R panels of (ω, -I·ω̇) = τ. Overlay τ_model = λ(R)·I·ω."""
    Rs = sorted({s["R"] for s in segs})
    n = len(Rs)
    cols = 4
    rows = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows, cols, figsize=(4.5 * cols, 3.2 * rows),
                             squeeze=False)
    for ax, R in zip(axes.ravel(), Rs):
        any_ble = False
        any_vid = False
        all_w = []
        for s in segs:
            if s["R"] != R: continue
            color = "#1f77b4" if s["src"] == "ble" else "#ff7f0e"
            tau = -I_CRANK * s["omega_dot"]
            ax.scatter(s["omega"], tau, s=18, alpha=0.6, color=color,
                       edgecolor="none")
            all_w.append(s["omega"])
            if s["src"] == "ble": any_ble = True
            else: any_vid = True
        if all_w:
            wmax = max(w.max() for w in all_w)
            wg = np.linspace(0.1, max(wmax, 13), 200)
            tau_model = lam_model(R) * I_CRANK * wg
            ax.plot(wg, tau_model, color="#555", lw=1.4, ls="--",
                    label=f"model τ = λ(R={R})·I·ω")
        ax.set_title(f"R = {R}", fontsize=10)
        ax.set_xlabel("ω (rad/s)")
        ax.set_ylabel("τ = -I·dω/dt  (N·m)")
        ax.grid(alpha=0.3)
        ax.legend(loc="best", fontsize=8)
        # Color-key in title row only.
    for ax in axes.ravel()[len(Rs):]:
        ax.axis("off")
    fig.text(0.01, 0.99, "blue = BLE/CSC,  orange = video phase-locked",
             fontsize=9, color="#444")
    plt.suptitle("Brake torque τ vs ω — straight line through origin = "
                 "ω² physics holds; flatten/curve = it doesn't",
                 fontsize=12, weight="bold")
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    out = OUT_DIR / "torque_vs_omega_per_R.png"
    plt.savefig(out, dpi=130)
    plt.close()
    print(f"\nwrote {out}")


def plot_q_vs_R(segs):
    """Per-segment torque exponent q vs R."""
    fig, ax = plt.subplots(figsize=(8, 5))
    for s in segs:
        color = "#1f77b4" if s["src"] == "ble" else "#ff7f0e"
        ax.scatter(s["R"], s["q"], s=24 + s["n"] * 4, alpha=0.7, color=color,
                   edgecolor="white", linewidth=0.7)
    ax.axhline(1.0, color="#555", lw=1.5, ls="--",
               label="q=1 (model assumption: τ∝ω, P∝ω²)")
    ax.axhline(0.5, color="#888", lw=1.0, ls=":",
               label="q=0.5 (P∝ω^1.5 — IC8 firmware exponent)")
    ax.axhline(0.0, color="#888", lw=1.0, ls=":",
               label="q=0 (saturated brake, P∝ω)")
    ax.set_xlabel("R (dial setting)")
    ax.set_ylabel("torque exponent q  (τ ∝ ω^q)")
    ax.set_title("Torque exponent per spin-down: q=1 means ω² model holds",
                 fontsize=12, weight="bold")
    ax.grid(alpha=0.3)
    ax.legend(loc="best", fontsize=9)
    out = OUT_DIR / "q_vs_R.png"
    plt.tight_layout()
    plt.savefig(out, dpi=130)
    plt.close()
    print(f"wrote {out}")


def main():
    segs = collect_segments()
    if not segs:
        sys.exit("no segments")
    summarize(segs)
    plot_torque_vs_omega(segs)
    plot_q_vs_R(segs)


if __name__ == "__main__":
    main()
