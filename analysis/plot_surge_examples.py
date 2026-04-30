"""Generate figures for README: how the model is built, and how its dynamics
show up in real recordings.

  * spindown_fit.png  — coastdowns at different R values give λ(R), the
    decay rate of the flywheel. A power-law fit λ(R) = α·R^p + β separates
    brake from residual drag and captures the dial's nonlinear bite at
    high R. Same data, same fit as analysis/fit_lambda_R_v3.py.
  * indoor_surge.png  — IC8 BLE log, cadence 8→67 rpm at R=28 (calibration
    grid). The corrected model decomposes power into steady + KE; you can
    see the KE bump while the rider spins the flywheel up, then it collapses
    to ~0 once cadence holds and only steady-state dissipation remains.
  * outdoor_surge.png — 4iiii crank meter on a real road ride. Power shows
    the same bump+settle shape directly during a speed surge, validating
    that the indoor model is reproducing real-world transient physics.
"""
from pathlib import Path
import csv
import numpy as np
import matplotlib.pyplot as plt
import fitdecode

ROOT = Path(__file__).parent.parent
OUT = ROOT / "docs/figures"
OUT.mkdir(parents=True, exist_ok=True)

# Bridge constants (mirrored in bridge/lib/physics/calibration.dart).
LAMBDA_ALPHA = 0.001020
LAMBDA_BETA = 0.0252
LAMBDA_P = 1.646
I_CRANK = 9.3


def lambda_at(R):
    R_pos = np.maximum(R, 0.0)
    rp = np.where(R_pos > 0, R_pos ** LAMBDA_P, 0.0)
    return LAMBDA_ALPHA * rp + LAMBDA_BETA


def _csc(row):
    v = row.get("cadence_rpm_csc", "")
    return float(v) if v not in (None, "") else None


def indoor_surge():
    rows = list(csv.DictReader(
        open(ROOT / "data/calibration/holding_a_few_seconds_at_lots_of_R_values.csv")))
    n = len(rows)
    t = np.array([float(r["timestamp_s"]) for r in rows])
    R = np.array([int(r["resistance"]) for r in rows])
    P_ic8 = np.array([int(r["power_w"]) for r in rows], dtype=float)
    cad_csc = np.array([_csc(r) if _csc(r) is not None else np.nan for r in rows])
    cad_ftms = np.array([float(r["cadence_rpm"]) for r in rows])
    cad = np.where(np.isnan(cad_csc), cad_ftms, cad_csc)

    omega = cad * np.pi / 30
    omegaDot = np.zeros(n)
    for i in range(1, n - 1):
        if t[i + 1] > t[i - 1]:
            omegaDot[i] = (omega[i + 1] - omega[i - 1]) / (t[i + 1] - t[i - 1])
    omegaDot = np.convolve(omegaDot, np.ones(3) / 3, mode="same")

    P_steady = lambda_at(R) * I_CRANK * omega ** 2
    P_ke = I_CRANK * omega * omegaDot
    P_corr = np.maximum(0, P_steady + P_ke)

    # Window: a clean cad 8→67 rpm spin-up at R=28, then ~9 s hold.
    s, e = 83, 100
    tt = t[s:e] - t[s]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6.5), sharex=True,
                                    gridspec_kw={"height_ratios": [1, 1.6]})
    fig.suptitle("Indoor surge-and-hold (IC8 BLE log, R=28)",
                 fontsize=12, weight="bold")

    ax1.plot(tt, cad[s:e], color="#1f77b4", marker="o", lw=2, label="cadence")
    ax1.set_ylabel("cadence (rpm)")
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="lower right")

    ax2.fill_between(tt, 0, P_steady[s:e], color="#aec7e8",
                     label=r"steady $\lambda(R)\cdot I\omega^2$", step="mid")
    ax2.fill_between(tt, P_steady[s:e], P_steady[s:e] + np.maximum(P_ke[s:e], 0),
                     color="#ff9896", label=r"KE $I\omega\dot{\omega}$ (positive)",
                     step="mid", alpha=0.85)
    ax2.plot(tt, P_corr[s:e], color="#d62728", lw=2.2, label="corrected = steady + KE")
    ax2.plot(tt, P_ic8[s:e], color="#555", lw=1.4, ls="--", label="IC8 broadcast")
    ax2.set_xlabel("time (s)")
    ax2.set_ylabel("power (W)")
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc="upper left", framealpha=0.9)

    # Annotate the transition (place labels at the bottom to avoid the legend).
    surge_end = float(np.argmax(cad[s:e] >= 60))
    ax2.axvspan(0, surge_end, alpha=0.07, color="black")
    ymin = ax2.get_ylim()[0]
    yspan = ax2.get_ylim()[1] - ymin
    ax2.text(surge_end / 2, ymin + 0.04 * yspan, "spin-up",
             ha="center", fontsize=9, color="#444", style="italic")
    ax2.text((surge_end + tt[-1]) / 2, ymin + 0.04 * yspan, "hold",
             ha="center", fontsize=9, color="#444", style="italic")

    fig.tight_layout()
    fig.savefig(OUT / "indoor_surge.png", dpi=140)
    print(f"wrote {OUT / 'indoor_surge.png'}")


def outdoor_surge():
    rows = []
    with fitdecode.FitReader(str(ROOT / "data/outdoor/Lunch_Ride_still_too_much_snow.fit")) as fit:
        for f in fit:
            if not isinstance(f, fitdecode.FitDataMessage) or f.name != "record":
                continue
            d = {x.name: x.value for x in f.fields}
            rows.append({
                "pw": d.get("power") or 0,
                "cd": d.get("cadence") or 0,
                "sp": d.get("enhanced_speed") or d.get("speed") or 0,
                "al": d.get("enhanced_altitude") or d.get("altitude") or 0,
            })
    n = len(rows)
    P = np.array([r["pw"] for r in rows], dtype=float)
    sp = np.array([r["sp"] for r in rows], dtype=float)  # m/s
    cd = np.array([r["cd"] for r in rows], dtype=float)

    # Snow surge: t=141..154. Speed climbs 6.2 m/s → 9.4 m/s in ~7 s, then
    # holds at ~9.3 m/s while the rider keeps pedaling (cad ~95-100). Cut the
    # window before the rider finally eases off the pedals around t=155+ so
    # the hold-phase mean doesn't get diluted by a trailing coast.
    s, e = 141, 154
    surge_end = 7  # sample where speed first reaches the hold value
    tt = np.arange(e - s)

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(8, 6.5), sharex=True,
                                    gridspec_kw={"height_ratios": [1, 1.6]})
    fig.suptitle("Outdoor surge-and-hold (4iiii crank meter, snow ride)",
                 fontsize=12, weight="bold")

    ax1.plot(tt, sp[s:e] * 3.6, color="#1f77b4", marker="o", lw=2, label="speed")
    ax1.plot(tt, cd[s:e], color="#9467bd", marker="s", lw=1.4, alpha=0.7, label="cadence")
    ax1.set_ylabel("speed (km/h) / cad (rpm)")
    ax1.grid(True, alpha=0.3)
    ax1.legend(loc="lower right")

    ax2.fill_between(tt, 0, P[s:e], color="#ffbb78", step="mid", alpha=0.7,
                     label="P_4iiii (true rider power)")
    ax2.plot(tt, P[s:e], color="#d62728", lw=2.2)

    hold_avg = np.mean(P[s + surge_end:e])
    ax2.axhline(hold_avg, color="#444", lw=1.2, ls="--",
                label=f"hold-phase mean ≈ {hold_avg:.0f} W")
    ax2.set_xlabel("time (s)")
    ax2.set_ylabel("power (W)")
    ax2.grid(True, alpha=0.3)
    ax2.legend(loc="upper right", framealpha=0.9)

    ax2.axvspan(0, surge_end, alpha=0.07, color="black")
    ymin = ax2.get_ylim()[0]
    yspan = ax2.get_ylim()[1] - ymin
    ax2.text(surge_end / 2, ymin + 0.04 * yspan, "speed-up",
             ha="center", fontsize=9, color="#444", style="italic")
    ax2.text((surge_end + tt[-1]) / 2, ymin + 0.04 * yspan, "hold",
             ha="center", fontsize=9, color="#444", style="italic")

    fig.tight_layout()
    fig.savefig(OUT / "outdoor_surge.png", dpi=140)
    print(f"wrote {OUT / 'outdoor_surge.png'}")


def spindown_fit():
    """Plot per-coastdown λ values against R, with the power-law fit
    overlaid and the linear fit drawn dashed for comparison. Uses the
    canonical fit defined in analysis/fit_lambda_R_v3.py (video-derived
    bounds, phase-locked at low R, two-term elsewhere)."""
    import sys as _sys
    _sys.path.insert(0, str(Path(__file__).parent))
    from fit_lambda_R_v3 import collect_lambdas

    rows = collect_lambdas()
    Rs = np.array([r["R"] for r in rows], dtype=float)
    lams = np.array([r["lam"] for r in rows])
    w = np.array([r["weight"] for r in rows])
    sources = np.array([r["source"] for r in rows])
    W = np.diag(w)

    # Linear comparison line.
    A_lin = np.vstack([Rs, np.ones_like(Rs)]).T
    (a_lin, b_lin), *_ = np.linalg.lstsq(W @ A_lin, W @ lams, rcond=None)

    # Power-law fit by 1D grid over p, profiling (α, β) at each grid point.
    Rs_safe = np.where(Rs == 0, 1.0, Rs)
    best = None
    for p in np.linspace(1.30, 2.20, 901):
        u = np.where(Rs == 0, 0.0, Rs_safe**p)
        A = np.vstack([u, np.ones_like(u)]).T
        sol, *_ = np.linalg.lstsq(W @ A, W @ lams, rcond=None)
        alpha_p, beta_p = sol
        rss = float(np.sum(w**2 * (lams - (alpha_p * u + beta_p))**2))
        if best is None or rss < best[0]:
            best = (rss, p, alpha_p, beta_p)
    _, p_pow, alpha, beta = best

    rline = np.linspace(0, max(Rs.max() + 5, 100), 200)
    pow_curve = np.where(rline > 0, alpha * rline**p_pow, 0.0) + beta

    fig, ax = plt.subplots(figsize=(8, 5))
    color_v3 = "#1f77b4"
    color_v2 = "#ff7f0e"
    for label, mask, color in [("phase-locked (R≤24)", sources == "v3", color_v3),
                                ("two-term (R≥33)", sources == "v2", color_v2)]:
        if not mask.any(): continue
        ax.scatter(Rs[mask], lams[mask], s=w[mask] * 4 + 20, c=color, alpha=0.85,
                   edgecolor="white", linewidth=1,
                   label=f"{label} (n={mask.sum()})")
    ax.plot(rline, a_lin * rline + b_lin, color="#999", lw=1.4, ls="--",
            label=f"linear fit  λ = {a_lin:.4f}·R + {b_lin:.4f}")
    ax.plot(rline, pow_curve,
            color="#d62728", lw=2.2,
            label=f"power-law fit  λ = {alpha:.5f}·R^{p_pow:.3f} + {beta:.4f}")
    ax.axhline(beta, color="#888", ls=":", lw=1)
    ax.text(rline[-1] * 0.02, beta + 0.005,
            f"residual drag β = {beta:.3f}", color="#555", fontsize=9)
    ax.set_xlabel("resistance dial R")
    ax.set_ylabel(r"flywheel decay rate $\lambda$ (1/s)")
    ax.set_title(r"Spin-down calibration: $\lambda(R) = \alpha\,R^p + \beta$",
                 fontsize=12, weight="bold")
    ax.legend(loc="upper left", fontsize=9)
    ax.grid(True, alpha=0.3)
    ax.set_xlim(0, rline[-1])
    fig.tight_layout()
    fig.savefig(OUT / "spindown_fit.png", dpi=140)
    print(f"wrote {OUT / 'spindown_fit.png'}")


def power_curves():
    """At-a-glance comparison of IC8 broadcast vs corrected power across the
    operating envelope. Shows P vs cadence at four common R settings; the gap
    between the dashed (IC8) and solid (corrected) lines is what the bridge
    subtracts at each operating point."""
    cad = np.linspace(40, 120, 200)
    omega = cad * np.pi / 30
    fig, ax = plt.subplots(figsize=(8, 5.5))

    colors = ["#1f77b4", "#2ca02c", "#ff7f0e", "#d62728"]
    for R, color in zip([20, 30, 40, 50], colors):
        P_ic8 = 0.019 * R ** 0.83 * cad ** 1.5
        P_corr = lambda_at(R) * I_CRANK * omega ** 2
        ax.plot(cad, P_ic8, color=color, lw=1.4, ls="--", alpha=0.8)
        ax.plot(cad, P_corr, color=color, lw=2.2,
                label=f"R = {R}")

    # Legend hack: solid = corrected, dashed = IC8 broadcast.
    ax.plot([], [], color="#444", lw=2.2, label="corrected")
    ax.plot([], [], color="#444", lw=1.4, ls="--", label="IC8 broadcast")

    ax.set_xlabel("cadence (rpm)")
    ax.set_ylabel("power (W)")
    ax.set_title("IC8 broadcast vs corrected, by R and cadence",
                 fontsize=12, weight="bold")
    ax.grid(True, alpha=0.3)
    ax.legend(loc="upper left", framealpha=0.9, ncol=2)
    ax.set_xlim(40, 120)
    ax.set_ylim(0, None)
    fig.tight_layout()
    fig.savefig(OUT / "power_curves.png", dpi=140)
    print(f"wrote {OUT / 'power_curves.png'}")


if __name__ == "__main__":
    spindown_fit()
    indoor_surge()
    outdoor_surge()
    power_curves()
