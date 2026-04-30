"""Refit λ(R) on the cleaned video-derived λ values.

Inputs (built in-process by re-running v2 + v3):
  - For R ≤ 24 use the phase-locked v3 λ (gravity-pendulum subtracted).
  - For R ≥ 33 use the v2 two-term λ (segments too short for phase
    locking, but pendulum bias is small relative to brake torque).
  - Drop the R=0 occ=1 outlier (n_rev=0 — bounds are too tight).
  - For R=0 occ=2 use v3.

Compares several functional forms — to see whether Hill is still the
right choice now that the high-R points are clean:

  1. Linear:        λ(R) = a·R + b
  2. Quadratic:     λ(R) = a·R² + b·R + c
  3. Power:         λ(R) = α·R^p + β
  4. Saturating:    λ(R) = α·(1 − exp(−R/Rc)) + β
  5. Hill:          λ(R) = α · R^p / (R^p + Rc^p) + β
  6. Magnet-gap:    λ(R) = α / (1 − R/Rmax)^p + β   (eddy-brake far-field)

Reports weighted-RSS, R-bucket residuals, and writes a comparison plot.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_nrf_log import parse_log  # noqa: E402
from spindown_fit import find_clean_coastdowns, fit_decay  # noqa: E402
from spindown_fit_video import (LOG, VIDEO_CSV,
                                integrate_to_cumulative,
                                load_video_modpi)  # noqa: E402
from spindown_fit_video_twoterm import (fit_one_term_segment,
                                        fit_two_term_segment)  # noqa: E402
from spindown_fit_video_v3 import phase_lock_resample  # noqa: E402
from video_segment_bounds import detect_segment_bounds  # noqa: E402

OUT_PATH = (Path(__file__).resolve().parent.parent
            / "data/calibration/spindown_plots_v3/lambda_R_models.png")


def collect_lambdas():
    rows = parse_log(LOG)
    segs = find_clean_coastdowns(rows)
    t_v, ang_v = load_video_modpi(VIDEO_CSV)
    cum_all = integrate_to_cumulative(ang_v)

    out = []
    per_R = {}
    for seg, R, term in segs:
        occ = per_R.get(R, 0); per_R[R] = occ + 1
        if R == 0 and occ == 0:
            continue
        b = detect_segment_bounds(seg, t_v, cum_all, R=R)
        if b is None:
            continue
        i0, i1, t_stop, t_floor = b
        if i1 <= i0 + 5:
            continue
        tt = t_v[i0:i1 + 1]
        cum = cum_all[i0:i1 + 1] - cum_all[i0]
        if len(tt) < 12:
            continue
        s = np.sign(cum[-1] - cum[0])
        if s == 0:
            continue
        cum = s * cum

        fcsc = fit_decay(seg)
        lam_c = fcsc[0] if fcsc is not None else float("nan")
        c0 = fcsc[3] if fcsc is not None else 60.0
        omega0_seed = c0 * 2 * math.pi / 60
        lam0 = max(lam_c if not math.isnan(lam_c) else 0.1, 0.01)

        f2_v2 = fit_two_term_segment(tt, cum, lam0=lam0, omega0=omega0_seed)
        if f2_v2 is None:
            continue
        lam2_v2 = abs(f2_v2[0])

        # Try v3 (phase-locked) — only counts if we get >=4 revs.
        t_rev, cum_rev = phase_lock_resample(tt, cum)
        lam2_v3 = None
        n_rev = len(t_rev)
        if n_rev >= 4:
            f2_v3 = fit_two_term_segment(t_rev, cum_rev, lam0=lam0,
                                         omega0=omega0_seed)
            if f2_v3 is not None:
                lam2_v3 = abs(f2_v3[0])

        # Pick which λ to use for the Hill fit.
        # Drop R=0 occ=1 (n_rev=0; bounds too tight).
        if R == 0 and occ == 1:
            continue
        if lam2_v3 is not None:
            lam_use = lam2_v3
            source = "v3"
        else:
            lam_use = lam2_v2
            source = "v2"
        # Weight: same scheme as spindown_fit.py — sqrt(n) × log(c0/c1).
        # Use video-derived n and dynamic range.
        n_pts = (n_rev if source == "v3" else len(tt))
        # Dynamic range from cumulative curve: ω at start vs end.
        # ω_start ≈ (cum[20]-cum[0])/(tt[20]-tt[0]); ω_end ≈ near floor.
        if len(tt) >= 30:
            om_start = (cum[20] - cum[0]) / (tt[20] - tt[0])
            om_end = max(0.5, (cum[-1] - cum[-20]) / (tt[-1] - tt[-20]))
        else:
            om_start = (cum[-1] - cum[0]) / (tt[-1] - tt[0])
            om_end = 0.5
        log_range = math.log(max(om_start / om_end, 1.5))
        w = math.sqrt(n_pts) * log_range
        out.append({"R": R, "occ": occ, "lam": lam_use, "source": source,
                    "lam_v2": lam2_v2, "lam_v3": lam2_v3, "lam_csc": lam_c,
                    "n": n_pts, "n_rev": n_rev, "weight": w})
    return out


def fit_models(R, lam, w):
    W = np.diag(w)

    def wrss(pred):
        return float(np.sum(w**2 * (lam - pred)**2))

    results = {}

    # 1. Linear
    A = np.vstack([R, np.ones_like(R)]).T
    sol, *_ = np.linalg.lstsq(W @ A, W @ lam, rcond=None)
    pred = sol[0] * R + sol[1]
    results["linear"] = {"params": {"a": sol[0], "b": sol[1]},
                        "pred_fn": lambda r, s=sol: s[0] * r + s[1],
                        "wrss": wrss(pred), "pred": pred}

    # 2. Quadratic
    A = np.vstack([R**2, R, np.ones_like(R)]).T
    sol, *_ = np.linalg.lstsq(W @ A, W @ lam, rcond=None)
    pred = sol[0] * R**2 + sol[1] * R + sol[2]
    results["quadratic"] = {"params": {"a": sol[0], "b": sol[1], "c": sol[2]},
                            "pred_fn": lambda r, s=sol: s[0]*r**2 + s[1]*r + s[2],
                            "wrss": wrss(pred), "pred": pred}

    # 3. Power: λ = α·R^p + β  (β = R=0 baseline)
    best = None
    R_safe = np.where(R == 0, 1.0, R)  # mask zeros for R^p
    for p in np.linspace(0.5, 4.0, 351):
        u = np.where(R == 0, 0.0, R_safe**p)
        A = np.vstack([u, np.ones_like(u)]).T
        sol, *_ = np.linalg.lstsq(W @ A, W @ lam, rcond=None)
        pr = sol[0] * u + sol[1]
        rss = wrss(pr)
        if best is None or rss < best[0]:
            best = (rss, p, sol[0], sol[1], pr)
    rss, p, a, b, pr = best
    results["power"] = {"params": {"alpha": a, "p": p, "beta": b},
                        "pred_fn": lambda r, a=a, p=p, b=b: a * np.where(r == 0, 0, r**p) + b,
                        "wrss": rss, "pred": pr}

    # 4. Saturating
    best = None
    for Rc in np.exp(np.linspace(np.log(2.0), np.log(500.0), 400)):
        u = 1 - np.exp(-R / Rc)
        A = np.vstack([u, np.ones_like(u)]).T
        sol, *_ = np.linalg.lstsq(W @ A, W @ lam, rcond=None)
        pr = sol[0] * u + sol[1]
        rss = wrss(pr)
        if best is None or rss < best[0]:
            best = (rss, Rc, sol[0], sol[1], pr)
    rss, Rc, a, b, pr = best
    results["saturating"] = {"params": {"alpha": a, "beta": b, "Rc": Rc},
                             "pred_fn": lambda r, a=a, b=b, Rc=Rc: a * (1 - np.exp(-r / Rc)) + b,
                             "wrss": rss, "pred": pr}

    # 5. Hill
    best = None
    for Rc_h in np.linspace(5.0, 200.0, 196):
        for p_h in np.linspace(1.0, 8.0, 71):
            u = R**p_h / (R**p_h + Rc_h**p_h)
            A = np.vstack([u, np.ones_like(u)]).T
            sol, *_ = np.linalg.lstsq(W @ A, W @ lam, rcond=None)
            pr = sol[0] * u + sol[1]
            rss = wrss(pr)
            if best is None or rss < best[0]:
                best = (rss, Rc_h, p_h, sol[0], sol[1], pr)
    rss, Rc_h, p_h, a, b, pr = best
    results["hill"] = {"params": {"alpha": a, "beta": b, "Rc": Rc_h, "p": p_h},
                       "pred_fn": lambda r, a=a, b=b, Rc=Rc_h, p=p_h: a * r**p / (r**p + Rc**p) + b,
                       "wrss": rss, "pred": pr}

    # 6. Magnet-gap (eddy-brake): λ = α / (1 − R/Rmax)^p + β.
    # As R → Rmax, gap → 0 and brake → ∞.
    best = None
    Rmax_max = float(R.max() * 1.5)
    for Rmax in np.linspace(R.max() + 1, max(Rmax_max, 200), 200):
        for p_g in np.linspace(1.0, 6.0, 51):
            x = 1 - R / Rmax
            if (x <= 0).any():
                continue
            u = 1.0 / x**p_g
            A = np.vstack([u, np.ones_like(u)]).T
            sol, *_ = np.linalg.lstsq(W @ A, W @ lam, rcond=None)
            pr = sol[0] * u + sol[1]
            rss = wrss(pr)
            if best is None or rss < best[0]:
                best = (rss, Rmax, p_g, sol[0], sol[1], pr)
    rss, Rmax, p_g, a, b, pr = best
    results["magnet_gap"] = {"params": {"alpha": a, "beta": b,
                                        "Rmax": Rmax, "p": p_g},
                             "pred_fn": lambda r, a=a, b=b, Rmax=Rmax, p=p_g: a / (1 - r / Rmax)**p + b,
                             "wrss": rss, "pred": pr}

    return results


def main():
    rows = collect_lambdas()
    if not rows:
        sys.exit("no rows")

    rows.sort(key=lambda r: (r["R"], r["occ"]))
    print(f"\n{'R':>3} {'occ':>3} {'src':>3} {'lam':>7} {'lam_csc':>8} "
          f"{'lam_v2':>7} {'lam_v3':>7} {'n_rev':>5} {'w':>5}")
    for r in rows:
        v3str = f"{r['lam_v3']:.3f}" if r['lam_v3'] is not None else "—"
        print(f"{r['R']:>3} {r['occ']:>3} {r['source']:>3} "
              f"{r['lam']:>7.3f} {r['lam_csc']:>8.3f} "
              f"{r['lam_v2']:>7.3f} {v3str:>7} "
              f"{r['n_rev']:>5} {r['weight']:>5.1f}")

    R = np.array([r["R"] for r in rows], dtype=float)
    lam = np.array([r["lam"] for r in rows])
    w = np.array([r["weight"] for r in rows])

    results = fit_models(R, lam, w)

    print(f"\n{'model':>12} {'wRSS':>10} {'wRMS':>9}  params")
    sum_w2 = float(np.sum(w**2))
    for name in ["linear", "quadratic", "power", "saturating", "hill", "magnet_gap"]:
        r = results[name]
        wrms = math.sqrt(r["wrss"] / sum_w2)
        params_str = " ".join(f"{k}={v:.4g}" for k, v in r["params"].items())
        print(f"{name:>12} {r['wrss']:>10.5f} {wrms:>9.5f}  {params_str}")

    # Residuals by R bucket for the best two models.
    sorted_models = sorted(results.items(), key=lambda kv: kv[1]["wrss"])
    print(f"\nbest by wRSS: {sorted_models[0][0]} > {sorted_models[1][0]} > {sorted_models[2][0]}")
    for name, _ in sorted_models[:3]:
        pred = results[name]["pred"]
        resid = lam - pred
        print(f"\n  residuals by R bucket — {name} (mean ± std):")
        print(f"    {'R range':>8} {'n':>3} {'mean λ':>8} {'pred λ':>8} {'mean resid':>12} {'std resid':>10}")
        for lo, hi in [(0, 10), (10, 25), (25, 45), (45, 70), (70, 100)]:
            mask = (R >= lo) & (R < hi)
            if mask.sum() == 0: continue
            print(f"    [{lo:>2},{hi:>3}) {mask.sum():>3} "
                  f"{lam[mask].mean():>8.4f} {pred[mask].mean():>8.4f} "
                  f"{resid[mask].mean():>+12.4f} {resid[mask].std():>10.4f}")

    # Plot.
    fig, (ax_lin, ax_log) = plt.subplots(1, 2, figsize=(14, 5))
    Rg = np.linspace(0.1, R.max() * 1.05, 400)
    colors = {"linear": "C0", "quadratic": "C1", "power": "C5",
              "saturating": "C3", "hill": "C2", "magnet_gap": "C4"}
    for ax, log in [(ax_lin, False), (ax_log, True)]:
        ax.errorbar(R, lam, fmt='ko', ms=5, alpha=0.7, label='data')
        for name, color in colors.items():
            r = results[name]
            try:
                yg = r["pred_fn"](Rg)
            except Exception:
                continue
            wrms = math.sqrt(r["wrss"] / sum_w2)
            ax.plot(Rg, yg, color=color, lw=1.4,
                    label=f"{name} (wRMS={wrms:.4f})")
        ax.set_xlabel('R (dial setting)')
        ax.set_ylabel('λ (1/s)')
        ax.grid(alpha=0.3)
        ax.legend(loc='best', fontsize=9)
        if log:
            ax.set_yscale('log')
            ax.set_title('log-y')
        else:
            ax.set_title('linear')

    plt.suptitle("λ(R) on cleaned data: v3 (phase-locked) for R≤24, "
                 "v2 (full-trace 2-term) for R≥33.", fontsize=11)
    plt.tight_layout(rect=[0, 0, 1, 0.95])
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(OUT_PATH, dpi=120)
    plt.close()
    print(f"\nwrote {OUT_PATH}")


if __name__ == "__main__":
    main()
