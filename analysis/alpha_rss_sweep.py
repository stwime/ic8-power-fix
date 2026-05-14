"""Does the spin-down data prefer some α, or is it truly degenerate?

The strict-Wouterse fit (analysis/fit_wouterse.py) holds α fixed at 165
N·m and κ at 0.1585 because the README+script claim the data only
constrains the product α·κ — α and κ slide along a degenerate ridge.
This script tests that claim directly: sweep α across a wide range,
refit {w, R_h1, p1, R_h2, p2, κ, β, τ_c} at each α (κ released — that's
the point of the sweep), and plot RSS vs α.

  flat curve  →  strict degeneracy holds; any α equally good. The 165
                 anchor inherits all its authority from the 1000 W spec.

  curve with a basin  →  the data does prefer some α (presumably from
                 bell-curve curvature in the segments where κ·H·ω
                 approaches 1). 165 may or may not sit in the basin.

Requires data/calibration/all_spindowns.csv. If you don't have it,
run analysis/aggregate_spindowns.py first against your captured spin-down
videos.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis"))

import fit_wouterse as fw

OUT_DIR = ROOT / "analysis_out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _residuals(params, segments, alpha):
    """Residuals at fixed α, with everything else (incl. κ) free.

    params = (w, R_h1, p1, R_h2, p2, κ, β, τ_c).
    """
    w, R_h1, p1, R_h2, p2, kappa, beta, tau_c = params
    out = []
    for s in segments:
        R = float(s["R"])
        t = s["t"]; om = s["omega"]; om0 = float(om[0])

        def rhs(_t, y):
            H = fw.H_two(R, w, R_h1, p1, R_h2, p2)
            x = kappa * H * y[0]
            tau_eddy = alpha * H * 2.0 * x / (1.0 + x * x)
            tau_res = tau_c + fw.I_CRANK * beta * y[0]
            return [-(tau_eddy + tau_res) / fw.I_CRANK]

        sol = solve_ivp(rhs, (float(t[0]), float(t[-1]) + 1e-6), [om0],
                        t_eval=t, method="LSODA", rtol=1e-7, atol=1e-9)
        if not sol.success or sol.y.shape[1] != len(t):
            out.append(np.full(len(t), 1e3))
            continue
        pred = sol.y[0]
        scale = max(om0, 0.1)
        out.append((om - pred) / scale / math.sqrt(len(t)))
    return np.concatenate(out)


def fit_at_alpha(segments, alpha, x0):
    lo = np.array([0.0,  5.0,   0.5,  5.0,   0.5,  1e-4, 0.0,  0.0])
    hi = np.array([1.0,  500.,  10.,  500.,  10.,  5.0,  0.5,  20.0])
    res = least_squares(_residuals, x0, args=(segments, alpha),
                        bounds=(lo, hi), x_scale="jac", max_nfev=400)
    return res


def main():
    if not fw.ALL_SPINDOWNS_CSV.exists():
        sys.exit(f"missing {fw.ALL_SPINDOWNS_CSV} — "
                 f"run aggregate_spindowns.py first")

    segments = fw.collect()
    n_samples = sum(len(s["t"]) for s in segments)
    print(f"collected {len(segments)} segments ({n_samples} samples)")

    pinned_alpha = 165.0
    pinned_kappa = 0.1585  # production calibration

    # Warm-start two-Hill shape from the shipping fit; κ is the param
    # that should slide most with α (since ακH² is what the linear
    # regime constrains).
    base_x0 = np.array([
        0.4467,   # w
        57.616,   # R_h1
        2.297,    # p1
        128.452,  # R_h2
        0.685,    # p2
        pinned_kappa,
        0.0157,   # β
        1.3582,   # τ_c
    ])

    alphas = np.array([50, 75, 100, 130, 165, 200, 250, 330, 500, 750])
    results = []

    for alpha in alphas:
        # Warm-start κ inversely with α to preserve the linear-regime ακ.
        x0 = base_x0.copy()
        x0[5] = pinned_kappa * pinned_alpha / float(alpha)
        res = fit_at_alpha(segments, float(alpha), x0)
        w, R_h1, p1, R_h2, p2, kappa, beta, tau_c = res.x
        rss = 0.5 * float((res.fun ** 2).sum())
        results.append({
            "alpha": float(alpha), "kappa": kappa,
            "w": w, "R_h1": R_h1, "p1": p1, "R_h2": R_h2, "p2": p2,
            "beta": beta, "tau_c": tau_c, "rss": rss,
            "ak": float(alpha) * kappa, "a_over_k": float(alpha) / kappa,
        })
        print(f"α={alpha:>6.1f}  κ={kappa:.4f}  w={w:.3f}  "
              f"R_h1={R_h1:6.2f}  p1={p1:.2f}  R_h2={R_h2:6.2f}  p2={p2:.2f}  "
              f"β={beta:.4f}  τc={tau_c:.3f}  ακ={float(alpha)*kappa:6.3f}  "
              f"α/κ={float(alpha)/kappa:7.1f}  RSS={rss:.6f}")

    rss_arr = np.array([r["rss"] for r in results])
    a_arr = np.array([r["alpha"] for r in results])
    ak_arr = np.array([r["ak"] for r in results])
    aok_arr = np.array([r["a_over_k"] for r in results])

    i_min = int(np.argmin(rss_arr))
    i_pin = int(np.argmin(np.abs(a_arr - 165)))
    rel_gap = (rss_arr[i_pin] - rss_arr[i_min]) / max(rss_arr[i_min], 1e-9)

    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5))

    ax = axes[0]
    ax.plot(a_arr, rss_arr, "o-", color="C0")
    ax.axvline(165, color="k", lw=0.8, ls="--",
               label=f"pinned α=165  (RSS={rss_arr[i_pin]:.5f})")
    ax.axvline(a_arr[i_min], color="C3", lw=0.8, ls=":",
               label=f"min α={a_arr[i_min]:g}  (RSS={rss_arr[i_min]:.5f})")
    ax.set_xscale("log")
    ax.set_xlabel("α  [N·m]")
    ax.set_ylabel("normalized RSS")
    ax.set_title("Fit RSS vs anchored α (two-Hill, κ free)")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=8)

    ax = axes[1]
    ax.plot(a_arr, ak_arr, "o-", color="C2", label="α·κ (linear-regime product)")
    ax.set_xscale("log")
    ax.set_xlabel("α  [N·m]")
    ax.set_ylabel("α·κ")
    ax.set_title("Linear-regime product (should be ~constant)")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=9)

    ax = axes[2]
    ax.plot(a_arr, aok_arr, "o-", color="C4",
            label="α/κ (asymptotic peak power, W)")
    ax.axhline(1000, color="k", lw=0.8, ls=":", label="1000 W spec")
    ax.set_xscale("log")
    ax.set_yscale("log")
    ax.set_xlabel("α  [N·m]")
    ax.set_ylabel("α/κ  [W]")
    ax.set_title("Asymptotic peak power")
    ax.grid(alpha=0.3, which="both")
    ax.legend(fontsize=9)

    fig.suptitle(
        f"Spin-down identifiability of α   "
        f"(min @ α={a_arr[i_min]:g}, pin @ 165, gap = {rel_gap*100:+.2f}%)",
        fontsize=11, weight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.94])
    out_png = OUT_DIR / "alpha_rss_sweep.png"
    fig.savefig(out_png, dpi=130)
    plt.close()
    print(f"\nwrote {out_png}")
    print(f"\nRSS at pinned α=165 :  {rss_arr[i_pin]:.6f}")
    print(f"RSS at min α={a_arr[i_min]:>4g}    :  {rss_arr[i_min]:.6f}")
    print(f"relative gap         :  {rel_gap*100:+.3f}%")

    # Distinguish a true basin (RSS bowl with α* somewhere inside the
    # swept range) from monotone behavior (optimizer wants α→∞ or α→0,
    # i.e. the degeneracy is unbounded over the sweep window).
    monotone = (i_min == 0) or (i_min == len(a_arr) - 1)
    if rel_gap < 0.01:
        print("→ effectively flat: data has no meaningful α preference; "
              "anchor entirely from spec/geometry.")
    elif monotone:
        direction = "lower" if i_min == 0 else "higher"
        print(f"→ monotone descent toward {direction} α — no basin in "
              f"sweep range. The data trades α off against κ and H-shape "
              f"freely; ακ is what's actually constrained. The α=165 "
              f"anchor inherits its authority from the 1000 W spec, not "
              f"the data.")
    elif rel_gap < 0.10:
        print("→ shallow basin: data slightly prefers a different α, but "
              "within fit noise. Anchor still does most of the work.")
    else:
        print("→ real basin: data has measurable α preference. Worth "
              "checking whether the anchor and the data agree.")


if __name__ == "__main__":
    main()
