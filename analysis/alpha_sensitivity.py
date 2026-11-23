"""How much does the bridge's predicted brake power depend on the α anchor?

Background
----------
Strict-Wouterse spin-down fits constrain the *product* α·κ in the linear
regime (decay rate λ = β + 2·α·κ·H(R)²/I) and the Hill shape {R_h, p}.
α and κ individually slide along a degenerate ridge — only their product
is observed by spin-downs. κ was pinned with α/κ near the manufacturer's
"1000 W max output" spec, which is more marketing claim than measurement.

This script asks: if that anchor is off by a factor c, how wrong is the
bridge's broadcast power across the riding regime?

Method
------
For each α' = c·α, set κ' = κ/c so the data-constrained product α·κ is
preserved (i.e., the new (α', κ') pair would refit the spin-down data
identically in the linear regime). The Hill shape, β, τ_c and I are all
unchanged. Recompute brake power P(R, cad) = τ(R, ω)·ω with

    τ(R, ω) = α'·H(R) · 2x'/(1 + x'²) + τ_c + I·β·ω,    x' = κ'·H(R)·ω

The baseline is the shipped model (analysis/bridge_model.py). Scaling
α, τ_c and I together scales every row by the same factor, so the %
deviations do not depend on the inertia.

and compare to the baseline c=1 across a (R, cad) grid that brackets
typical riding conditions and the high-R corner the README flags.

Outputs
-------
analysis_out/alpha_sensitivity.png  — P(R) curves at three cadences
                                       for several α multipliers, plus
                                       % deviation from baseline.
Console table of % deviation at representative riding points.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from analysis.bridge_model import (  # noqa: E402
    ALPHA, BETA, I_CRANK, KAPPA, TAU_C, hill,
)

OUT_DIR = ROOT / "analysis_out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def brake_power(R, cad_rpm, alpha, kappa):
    """P_brake = τ·ω with α and κ swapped in. R, cad_rpm broadcastable."""
    omega = np.asarray(cad_rpm, dtype=float) * 2 * math.pi / 60
    H = hill(R)
    x = kappa * H * omega
    tau_eddy = alpha * H * 2.0 * x / (1.0 + x * x)
    tau_resid = TAU_C + I_CRANK * BETA * omega
    return (tau_eddy + tau_resid) * omega


def sweep(multipliers):
    """Return dict[c] = {'alpha': cα, 'kappa': κ/c}. ακ preserved."""
    return {c: {"alpha": c * ALPHA, "kappa": KAPPA / c} for c in multipliers}


# ---------------------------------------------------------------------------
# Plot.
# ---------------------------------------------------------------------------

def plot_curves(out_path):
    R_grid = np.linspace(1, 100, 400)
    cadences = [60, 80, 100]
    multipliers = [0.5, 0.7, 1.0, 1.3, 2.0]
    cases = sweep(multipliers)

    fig, axes = plt.subplots(2, 3, figsize=(15, 8), sharex=True)
    colors = plt.cm.coolwarm(np.linspace(0.1, 0.9, len(multipliers)))

    for col, cad in enumerate(cadences):
        ax_p = axes[0, col]
        ax_d = axes[1, col]
        baseline = brake_power(R_grid, cad, ALPHA, KAPPA)
        for c, color in zip(multipliers, colors):
            P = brake_power(R_grid, cad, cases[c]["alpha"], cases[c]["kappa"])
            ax_p.plot(R_grid, P, lw=2, color=color,
                      label=f"α×{c:g}  (κ×{1/c:.2f})")
            with np.errstate(divide="ignore", invalid="ignore"):
                pct = 100 * (P - baseline) / np.where(baseline > 1, baseline, np.nan)
            ax_d.plot(R_grid, pct, lw=2, color=color)

        ax_p.set_title(f"cadence = {cad} rpm")
        ax_p.set_ylabel("predicted brake power [W]")
        ax_p.grid(alpha=0.3)
        ax_p.set_ylim(bottom=0)
        if col == 2:
            ax_p.legend(fontsize=8, loc="upper left")

        ax_d.set_xlabel("R")
        ax_d.set_ylabel("% deviation from baseline")
        ax_d.axhline(0, color="k", lw=0.5)
        ax_d.axhline(10, color="k", lw=0.5, ls=":")
        ax_d.axhline(-10, color="k", lw=0.5, ls=":")
        ax_d.grid(alpha=0.3)
        ax_d.set_ylim(-50, 50)

    fig.suptitle(
        "Sensitivity of bridge power to α anchor, with α·κ pinned by spin-down data\n"
        f"baseline α={ALPHA:g} N·m (shipped),  κ={KAPPA:g} s/rad",
        fontsize=11, weight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    fig.savefig(out_path, dpi=130)
    plt.close()
    print(f"wrote {out_path}")


# ---------------------------------------------------------------------------
# Console table.
# ---------------------------------------------------------------------------

def report_table():
    points = [
        # (R, cad)  — bracket typical riding + high-R corner
        (10, 80), (20, 80), (30, 90), (40, 90),
        (50, 80), (60, 80), (70, 75), (80, 70),
        (90, 65), (95, 60),
    ]
    multipliers = [0.5, 0.7, 1.0, 1.3, 2.0]
    cases = sweep(multipliers)

    header = f"{'R':>4} {'cad':>4} {'ω':>5} {'x':>6}  " + "  ".join(
        f"α×{c:>3g}" for c in multipliers)
    print()
    print("Predicted brake power [W] across α multipliers (α·κ preserved)")
    print(header)
    print("-" * len(header))
    for R, cad in points:
        omega = cad * 2 * math.pi / 60
        x = KAPPA * hill(R) * omega
        row = [f"{R:>4} {cad:>4} {omega:>5.2f} {x:>6.3f} "]
        for c in multipliers:
            P = float(brake_power(R, cad,
                                  cases[c]["alpha"], cases[c]["kappa"]))
            row.append(f"{P:>6.1f}")
        print(" ".join(row))

    print()
    print(f"Same points, % deviation from baseline (α={ALPHA:g})")
    print(header)
    print("-" * len(header))
    for R, cad in points:
        omega = cad * 2 * math.pi / 60
        x = KAPPA * hill(R) * omega
        baseline = float(brake_power(R, cad, ALPHA, KAPPA))
        row = [f"{R:>4} {cad:>4} {omega:>5.2f} {x:>6.3f} "]
        for c in multipliers:
            P = float(brake_power(R, cad,
                                  cases[c]["alpha"], cases[c]["kappa"]))
            pct = 100 * (P - baseline) / max(baseline, 1e-6)
            row.append(f"{pct:>+6.1f}")
        print(" ".join(row))


def main():
    plot_curves(OUT_DIR / "alpha_sensitivity.png")
    report_table()


if __name__ == "__main__":
    main()
