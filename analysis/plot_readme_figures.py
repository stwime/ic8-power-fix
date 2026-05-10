"""Regenerate the README figures that depend on bridge defaults.

Run from repo root: python analysis/plot_readme_figures.py

Figures regenerated:
  docs/figures/power_curves.png   — IC8 broadcast (dashed) vs bridge
                                    corrected (solid) at several R values
                                    across the cadence range, at the
                                    shipped powerScale default.
  docs/figures/indoor_surge.png   — R=28 spin-up from
                                    data/calibration/holding_*.csv,
                                    decomposed into steady + KE terms.
  docs/figures/spindown_fit.png   — representative ω(t) coastdowns at
                                    four R values, model overlay on
                                    every segment, showing the fit
                                    tracks the actual trajectories.

Constants are duplicated from bridge/lib/physics/calibration.dart so
this script has no Flutter/Dart dependency. Keep them in sync if
defaults change.
"""
from __future__ import annotations

import csv
from pathlib import Path
from collections import defaultdict

import numpy as np
import matplotlib.pyplot as plt
from scipy.integrate import solve_ivp

ROOT = Path(__file__).resolve().parent.parent
FIG_DIR = ROOT / "docs" / "figures"
SPRINT_CSV = ROOT / "data/calibration/spin_downs_apr29.csv"
ALL_SPINDOWNS_CSV = ROOT / "data/calibration/all_spindowns.csv"

# Mirror of Calibration defaults — bridge/lib/physics/calibration.dart.
ALPHA = 165.0
BETA = 0.0386
RH = 83.189
P_EXP = 1.214
KAPPA = 0.1639
I_CRANK = 8.0
POWER_SCALE = 1.00

# IC8's own broadcast formula (firmware fit). See README "Why the bike's
# numbers can't be trusted".
IC8_GAIN = 0.019
IC8_R_EXP = 0.83
IC8_CAD_EXP = 1.5


def hill(r):
    r = np.asarray(r, dtype=float)
    h = np.zeros_like(r)
    m = r > 0
    h[m] = r[m] ** P_EXP / (r[m] ** P_EXP + RH ** P_EXP)
    return h


def bridge_steady(r, omega):
    """Bridge steady-state power, mirroring Calibration.brakePowerAt
    with powerScale = POWER_SCALE."""
    h = hill(r)
    x = KAPPA * h * omega
    tau_eddy = ALPHA * POWER_SCALE * h * 2.0 * x / (1.0 + x * x)
    tau_residual = I_CRANK * POWER_SCALE * BETA * omega
    return (tau_eddy + tau_residual) * omega


def ic8_broadcast(r, cad):
    return IC8_GAIN * np.power(r, IC8_R_EXP) * np.power(cad, IC8_CAD_EXP)


def plot_power_curves():
    cad = np.linspace(30, 110, 240)
    omega = cad * np.pi / 30.0
    rs = [10, 20, 30, 45, 60]
    # Distinct categorical colors that also walk cool-to-warm so higher R
    # reads as "harder" without sacrificing legibility between adjacent
    # curves. Picked for readability under both light and dark backgrounds.
    colors = ["#1f77b4",  # blue        (R=10)
              "#17becf",  # cyan        (R=20)
              "#2ca02c",  # green       (R=30)
              "#ff7f0e",  # orange      (R=45)
              "#d62728"]  # red         (R=60)
    fig, ax = plt.subplots(figsize=(7.5, 5.0))
    for r, color in zip(rs, colors):
        p_ic8 = ic8_broadcast(r, cad)
        p_br = bridge_steady(r, omega)
        ax.plot(cad, p_ic8, "--", color=color, lw=1.6, alpha=0.85)
        ax.plot(cad, p_br, "-", color=color, lw=2.2, label=f"R = {r}")

    ax.set_xlabel("Cadence (rpm)")
    ax.set_ylabel("Power (W)")
    ax.set_title(
        "IC8 broadcast (dashed) vs bridge corrected (solid)\n"
        f"powerScale = {POWER_SCALE:.2f}"
    )
    ax.set_xlim(30, 110)
    ax.set_ylim(0, 700)
    ax.grid(True, alpha=0.25)
    ax.legend(loc="upper left", title="Resistance", frameon=False)
    fig.tight_layout()
    out = FIG_DIR / "power_curves.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    print(f"Wrote {out}")


def _read_window(path: Path, t0: float, t1: float):
    t, cad, r = [], [], []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = float(row["timestamp_s"])
            if ts < t0 or ts > t1:
                continue
            t.append(ts)
            cad.append(float(row["cadence_rpm"]))
            r.append(int(row["resistance"]))
    return np.array(t), np.array(cad), np.array(r)


def plot_indoor_surge():
    # R=25 sprint at t≈35–50. Cadence climbs 24 → 125 rpm in ~8 s,
    # holds at the FTMS 125-rpm cap for ~2 s, then decelerates as the
    # rider stops pushing. R is cleanly 25 throughout. Peak KE pulse
    # during the steep ramp is ~150 W on top of ~270 W steady.
    t, cad, r = _read_window(SPRINT_CSV, 33.0, 50.5)
    omega = cad * np.pi / 30.0

    # Median filter on R to mirror corrector's 5-sample window.
    r_smooth = np.array([
        int(np.median(r[max(0, i - 4):i + 1])) for i in range(len(r))
    ])
    # 3-sample central diff on omega — same window the corrector uses.
    omega_dot = np.zeros_like(omega)
    for i in range(len(omega)):
        i0 = max(0, i - 1)
        i1 = min(len(omega) - 1, i + 1)
        dt = t[i1] - t[i0]
        if dt > 0:
            omega_dot[i] = (omega[i1] - omega[i0]) / dt

    p_steady = np.zeros_like(omega)
    p_ke = np.zeros_like(omega)
    for i in range(len(omega)):
        if cad[i] <= 0:
            continue
        p_steady[i] = bridge_steady(r_smooth[i], omega[i])
        p_ke[i] = I_CRANK * POWER_SCALE * omega[i] * omega_dot[i]
    p_total = np.maximum(p_steady + p_ke, 0.0)

    t_rel = t - t[0]
    fig, axes = plt.subplots(2, 1, figsize=(8.5, 5.5),
                             sharex=True,
                             gridspec_kw={"height_ratios": [3, 1]})
    ax, ax_cad = axes
    # Stack-fill: blue under steady, red is the KE delta on top.
    ax.fill_between(t_rel, 0, p_steady,
                    color="#3b6fb1", alpha=0.55,
                    label=r"Steady $\tau_{\rm brake}\cdot\omega$")
    upper = p_steady + p_ke
    ax.fill_between(t_rel, p_steady, upper,
                    where=upper >= p_steady,
                    color="#cc4a4a", alpha=0.55,
                    label=r"KE $I\,\omega\,\dot\omega$")
    ax.fill_between(t_rel, upper, p_steady,
                    where=upper < p_steady,
                    color="#cc4a4a", alpha=0.30, hatch="//")
    ax.plot(t_rel, p_total, "-", color="#222", lw=1.8, label="Bridge total")
    ax.set_ylabel("Power (W)")
    ax.set_title(
        "R=25 sprint decomposed into steady and KE terms\n"
        f"powerScale = {POWER_SCALE:.2f}"
    )
    ax.legend(loc="upper right", frameon=False)
    ax.grid(True, alpha=0.25)

    ax_cad.plot(t_rel, cad, "-", color="#444", lw=1.6)
    ax_cad.set_xlabel("Time (s)")
    ax_cad.set_ylabel("Cadence (rpm)")
    ax_cad.grid(True, alpha=0.25)
    ax_cad.set_ylim(0, max(100, cad.max() * 1.1))

    fig.tight_layout()
    out = FIG_DIR / "indoor_surge.png"
    fig.savefig(out, dpi=160)
    plt.close(fig)
    print(f"Wrote {out}")


def _load_spindowns():
    """Return list of {R, occ, t, omega} dicts from all_spindowns.csv."""
    if not ALL_SPINDOWNS_CSV.exists():
        return []
    by_id: dict[int, dict] = defaultdict(lambda: {"t": [], "omega": []})
    with ALL_SPINDOWNS_CSV.open() as f:
        for row in csv.DictReader(f):
            sid = int(row["id"])
            s = by_id[sid]
            if "R" not in s:
                s["R"] = int(row["R"])
                s["occ"] = int(row["occ"])
            s["t"].append(float(row["t_s"]))
            s["omega"].append(float(row["omega_rad_s"]))
    out = []
    for s in by_id.values():
        t = np.asarray(s["t"], dtype=float)
        om = np.asarray(s["omega"], dtype=float)
        if len(t) < 4 or (om <= 0).any():
            continue
        order = np.argsort(t)
        s["t"] = t[order] - t[order][0]
        s["omega"] = om[order]
        out.append(s)
    return out


def _tau_model(R, omega):
    h = float(hill(np.array([R]))[0])
    x = KAPPA * h * omega
    tau_eddy = ALPHA * h * 2.0 * x / (1.0 + x * x)
    tau_residual = I_CRANK * BETA * omega
    return tau_eddy + tau_residual


def _integrate(R, t, omega0):
    def rhs(_t, y):
        return [-_tau_model(R, y[0]) / I_CRANK]
    sol = solve_ivp(rhs, (float(t[0]), float(t[-1]) + 1e-6),
                    [omega0], t_eval=t,
                    method="LSODA", rtol=1e-7, atol=1e-9)
    return sol.y[0] if sol.success else None


def plot_spindown_fit():
    """Four panels at representative R, model curve overlaid on every
    coastdown segment at that R. Direct apples-to-apples ω(t)
    comparison — no biased log-linear summary statistic."""
    segments = _load_spindowns()
    if not segments:
        print(f"skipping spindown_fit.png — {ALL_SPINDOWNS_CSV} missing")
        return

    by_R = defaultdict(list)
    for s in segments:
        by_R[s["R"]].append(s)

    # Pick four R buckets spanning the range we have data for.
    available = sorted(by_R.keys())

    def closest(target):
        return min(available, key=lambda r: abs(r - target))

    targets = [closest(t) for t in [11, 31, 60, 90]]
    # de-duplicate while preserving order
    seen = set()
    panels = []
    for r in targets:
        if r in seen:
            continue
        seen.add(r)
        panels.append(r)

    fig, axes = plt.subplots(2, 2, figsize=(9.5, 6.2), sharex=False)
    axes = axes.flatten()
    colors = ["#1f77b4", "#2ca02c", "#ff7f0e", "#d62728"]
    for ax, R, color in zip(axes, panels, colors):
        segs = by_R[R]
        for s in segs:
            t, om = s["t"], s["omega"]
            ax.plot(t, om, "o", color="#333", ms=3, alpha=0.45,
                    markeredgewidth=0)
            om_pred = _integrate(float(R), t, float(om[0]))
            if om_pred is not None:
                ax.plot(t, om_pred, "-", color=color, lw=2.0, alpha=0.95)
        ax.set_title(f"R = {R}   (n = {len(segs)} coastdowns)",
                     fontsize=10)
        ax.set_xlabel("Time (s)")
        ax.set_ylabel("Crank ω (rad/s)")
        ax.grid(True, alpha=0.25)

    handles = [
        plt.Line2D([], [], marker="o", color="#333", linestyle="",
                   ms=5, alpha=0.6, label="Coastdown data"),
        plt.Line2D([], [], color="#222", lw=2.0,
                   label="Wouterse model"),
    ]
    fig.legend(handles=handles, loc="upper center",
               ncol=2, frameon=False, bbox_to_anchor=(0.5, 0.985))
    fig.suptitle(
        "Spin-down model vs data — representative R buckets",
        fontsize=12, weight="bold", y=1.005,
    )
    fig.tight_layout(rect=[0, 0, 1, 0.95])
    out = FIG_DIR / "spindown_fit.png"
    fig.savefig(out, dpi=160, bbox_inches="tight")
    plt.close(fig)
    print(f"Wrote {out}")


if __name__ == "__main__":
    plot_power_curves()
    plot_indoor_surge()
    plot_spindown_fit()
