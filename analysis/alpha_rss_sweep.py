"""Does the spin-down data prefer some α, or is it truly degenerate?

The strict-Wouterse fit (analysis/fit_wouterse.py) holds α fixed at 165
N·m because the README+script claim the data only constrains the product
α·κ — α and κ slide along a degenerate ridge. This script tests that
claim directly: sweep α across a wide range, refit {κ, R_h, p, β} at
each α, and plot RSS vs α.

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

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "analysis"))

import fit_wouterse as fw

OUT_DIR = ROOT / "analysis_out"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main():
    if not fw.ALL_SPINDOWNS_CSV.exists():
        sys.exit(f"missing {fw.ALL_SPINDOWNS_CSV} — "
                 f"run aggregate_spindowns.py first")

    segments = fw.collect()
    n_samples = sum(len(s["t"]) for s in segments)
    print(f"collected {len(segments)} segments ({n_samples} samples)")

    pinned_alpha = fw.ALPHA_PINNED  # 165
    pinned_kappa = 0.160             # README baseline, used for warm-start

    alphas = np.array([50, 75, 100, 130, 165, 200, 250, 330, 500, 750])
    results = []

    for alpha in alphas:
        fw.ALPHA_PINNED = float(alpha)
        # Warm-start κ inversely with α to preserve the linear-regime ακ.
        x0 = np.array([
            pinned_kappa * pinned_alpha / float(alpha),
            70.0,   # R_h
            3.0,    # p
            0.04,   # β
        ])
        res = fw.run_fit(segments, x0=x0)
        kappa, R_h, p, beta = res.x
        rss = 0.5 * float((res.fun ** 2).sum())
        results.append({
            "alpha": float(alpha), "kappa": kappa, "R_h": R_h,
            "p": p, "beta": beta, "rss": rss,
            "ak": float(alpha) * kappa, "a_over_k": float(alpha) / kappa,
        })
        print(f"α={alpha:>6.1f}  κ={kappa:.4f}  R_h={R_h:6.2f}  p={p:.3f}  "
              f"β={beta:.4f}  ακ={float(alpha)*kappa:6.3f}  "
              f"α/κ={float(alpha)/kappa:7.1f}  RSS={rss:.6f}")

    fw.ALPHA_PINNED = pinned_alpha  # restore

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
    ax.set_title("Fit RSS vs anchored α")
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
    if rel_gap < 0.01:
        print("→ effectively flat: data has no meaningful α preference; "
              "anchor entirely from spec/geometry.")
    elif rel_gap < 0.10:
        print("→ shallow basin: data slightly prefers a different α, but "
              "within fit noise. Anchor still does most of the work.")
    else:
        print("→ real basin: data has measurable α preference. Worth "
              "checking whether the anchor and the data agree.")


if __name__ == "__main__":
    main()
