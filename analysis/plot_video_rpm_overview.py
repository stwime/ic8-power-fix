"""All video spindowns as RPM(t) on a single overview figure.

For each clean coastdown segment in the 19:37 log:
  1. Map segment time bounds into video time via LAG.
  2. Pull the per-frame angle_mod_pi over that window.
  3. Integrate to cumulative angle (shortest signed mod-π deltas).
  4. Differentiate via centred-window linear fit (window ~0.5 s) to get ω(t),
     convert to rpm.
  5. Plot all segments on a grid, one panel per (R, occ).

The point is to see whether decay shape stays exponential across all R, or
whether high-R segments show a non-exponential knee at low rpm — which would
be the magnetic-brake non-linearity hypothesis.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_nrf_log import parse_log  # noqa: E402
from spindown_fit import find_clean_coastdowns  # noqa: E402
from spindown_fit_video import (LAG, LOG, VIDEO_CSV,
                                integrate_to_cumulative,
                                load_video_modpi)  # noqa: E402
from spindown_fit_video_twoterm import (fit_two_term_segment,
                                        segment_video_window)  # noqa: E402

OUT_PATH_LIN = (Path(__file__).resolve().parent.parent
                / "data/calibration/spindown_plots/all_video_rpm.png")
OUT_PATH_LOG = (Path(__file__).resolve().parent.parent
                / "data/calibration/spindown_plots/all_video_rpm_log.png")
SMOOTH_WINDOW_S = 0.5  # centred linear-fit window for ω(t) estimation


def windowed_omega(t: np.ndarray, cum: np.ndarray,
                   window_s: float) -> np.ndarray:
    """Centred local linear fit of cumulative angle; slope is ω."""
    n = len(t)
    out = np.full(n, np.nan)
    if n < 3:
        return out
    for i in range(n):
        lo = i
        while lo > 0 and t[i] - t[lo - 1] < window_s / 2:
            lo -= 1
        hi = i
        while hi < n - 1 and t[hi + 1] - t[i] < window_s / 2:
            hi += 1
        if hi - lo < 3:
            continue
        ts = t[lo:hi + 1] - t[i]
        cs = cum[lo:hi + 1]
        # Slope of cs vs ts — that's ω at the centre.
        sl = np.polyfit(ts, cs, 1)[0]
        out[i] = sl
    return out


def segment_window(seg) -> tuple[float, float]:
    """Wall-clock [start, end] of the segment, starting at the SECOND CSC
    rev event (matches spindown_fit_video.py — guarantees post-pedaling)."""
    E0 = T0 = E1 = None
    for r in seg:
        ts = r["timestamp_s"]; et = r.get("crank_event_time_s")
        if ts is None or et is None:
            continue
        if E0 is None:
            E0, T0 = float(et), float(ts)
        elif float(et) > E0 + 1e-6:
            E1 = float(et)
            break
    if E1 is None:
        t0 = seg[0]["timestamp_s"]
    else:
        t0 = E1 + (T0 - E0)
    t1 = seg[-1]["timestamp_s"]
    return float(t0), float(t1)


def csc_lambda(seg) -> float | None:
    """Per-rev log-linear fit on this segment alone — same as
    spindown_fit.fit_decay but only returns λ."""
    obs = []
    for r in seg:
        nv = r.get("crank_revs"); tv = r.get("crank_event_time_s")
        if nv is None or tv is None:
            continue
        if obs and (nv <= obs[-1][0] or tv <= obs[-1][1] + 1e-6):
            continue
        obs.append((nv, tv))
    if len(obs) < 4:
        return None
    revs = np.array([o[0] for o in obs], float)
    et = np.array([o[1] for o in obs])
    cad = 60.0 * np.diff(revs) / np.diff(et)
    t_mid = 0.5 * (et[:-1] + et[1:])
    sl, _ic = np.polyfit(t_mid, np.log(cad), 1)
    return float(-sl)


def csc_rpm_series(seg) -> tuple[np.ndarray, np.ndarray]:
    """Per-rev cadence pairs from CSC, time-aligned to wall clock.
    Returns (t_mid, rpm) — same convention as spindown_fit.fit_decay."""
    obs = []
    rebase = None  # crank_event_time_s -> timestamp_s offset
    for r in seg:
        nv = r.get("crank_revs")
        tv = r.get("crank_event_time_s")
        ts = r.get("timestamp_s")
        if nv is None or tv is None or ts is None:
            continue
        if rebase is None:
            rebase = ts - tv
        if obs and (nv <= obs[-1][0] or tv <= obs[-1][1] + 1e-6):
            continue
        obs.append((nv, tv))
    if len(obs) < 2:
        return np.array([]), np.array([])
    revs = np.array([o[0] for o in obs], float)
    et = np.array([o[1] for o in obs])
    et_wall = et + rebase
    d_revs = np.diff(revs)
    dt = np.diff(et_wall)
    rpm = 60.0 * d_revs / dt
    t_mid = 0.5 * (et_wall[:-1] + et_wall[1:])
    return t_mid, rpm


def main():
    rows = parse_log(LOG)
    segs = find_clean_coastdowns(rows)
    t_v, ang_v = load_video_modpi(VIDEO_CSV)

    panels = []
    per_R = {}
    for seg, R, term in segs:
        occ = per_R.get(R, 0); per_R[R] = occ + 1
        if R == 0 and occ == 0:
            continue  # R_changed terminator
        t0, t1 = segment_window(seg)
        tv0, tv1 = t0 - LAG, t1 - LAG
        m = (t_v >= tv0) & (t_v <= tv1)
        if m.sum() < 6:
            continue
        tt = t_v[m]
        aa = ang_v[m]
        cum = integrate_to_cumulative(aa)
        # |ω| in rpm via windowed slope.
        omega = windowed_omega(tt, cum, SMOOTH_WINDOW_S)
        rpm_video = np.abs(omega) * 60.0 / (2 * math.pi)

        # CSC overlay: rebase t_mid to seconds since segment start.
        t_csc, rpm_csc = csc_rpm_series(seg)
        t_csc_rel = t_csc - t0  # seconds since post-stop start

        lam_c = csc_lambda(seg)
        # Two-term fit on cumulative angle: θ = θ₀ + A(1−e^(−λt)) − B·t.
        # ω(t) = A·λ·e^(−λt) − B; rpm = |ω|·60/(2π).
        omega0_seed = (60.0 if (lam_c is None or np.isnan(lam_c))
                       else max(rpm_video[~np.isnan(rpm_video)][:5].mean()
                                * 2 * math.pi / 60, 1.0))
        lam_seed = lam_c if (lam_c is not None and lam_c > 0) else 0.1
        f2 = fit_two_term_segment(tt, cum, lam0=lam_seed, omega0=omega0_seed)
        panels.append({
            "R": R, "occ": occ,
            "t_rel": tt - tt[0],
            "rpm_video": rpm_video,
            "t_csc_rel": t_csc_rel,
            "rpm_csc": rpm_csc,
            "lam_csc": lam_c,
            "term": term,
            "n_vid": len(tt),
            "fit2": f2,
        })

    if not panels:
        sys.exit("no panels to plot")

    # Sort by R for a readable grid.
    panels.sort(key=lambda p: (p["R"], p["occ"]))
    n = len(panels)
    cols = 4
    rows_ = (n + cols - 1) // cols

    for log_y, out_path in ((False, OUT_PATH_LIN), (True, OUT_PATH_LOG)):
        fig, axes = plt.subplots(rows_, cols,
                                 figsize=(4 * cols, 2.6 * rows_),
                                 sharex=False, sharey=False)
        axes = np.atleast_2d(axes)

        for idx, pan in enumerate(panels):
            ax = axes[idx // cols, idx % cols]
            ax.plot(pan["t_rel"], pan["rpm_video"], 'k-', lw=0.8, alpha=0.8,
                    label='video')
            if len(pan["t_csc_rel"]):
                ax.plot(pan["t_csc_rel"], pan["rpm_csc"], 'C3o', ms=4,
                        alpha=0.8, label='CSC')
            # Pure-exponential reference: anchor at first non-NaN video rpm,
            # slope = -λ_csc. Straight line on log-y; bowed line on linear-y.
            valid = ~np.isnan(pan["rpm_video"])
            if valid.any() and pan["lam_csc"] is not None:
                i0 = int(np.argmax(valid))
                t_ref = pan["t_rel"]
                rpm_ref = (pan["rpm_video"][i0]
                           * np.exp(-pan["lam_csc"] * (t_ref - t_ref[i0])))
                ax.plot(t_ref, rpm_ref, 'C0--', lw=0.8, alpha=0.7,
                        label=f'pure exp λ={pan["lam_csc"]:.2f}')
            # Two-term model fit: ω(t) = A·λ·e^(−λ(t−t₀)) − B
            if pan["fit2"] is not None:
                lam2, A2, B2, _off2, _ = pan["fit2"]
                t_ref = pan["t_rel"]
                omega_pred = abs(A2 * lam2 * np.exp(-lam2 * t_ref)
                                 - np.sign(A2) * B2)
                rpm_pred = omega_pred * 60.0 / (2 * math.pi)
                tau0 = B2 * lam2
                ax.plot(t_ref, rpm_pred, 'C2-', lw=1.0, alpha=0.85,
                        label=f'2-term λ={abs(lam2):.2f} τ₀={tau0:+.2f}')
            ax.set_title(f"R={pan['R']} occ={pan['occ']}  "
                         f"({pan['term']}, n_vid={pan['n_vid']})",
                         fontsize=9)
            ax.set_xlabel('t (s)', fontsize=8)
            ax.set_ylabel('rpm', fontsize=8)
            ax.tick_params(labelsize=8)
            ax.grid(alpha=0.3, which='both')
            if log_y:
                ax.set_yscale('log')
                ax.set_ylim(2, 200)
            else:
                ax.set_ylim(bottom=0)
            if idx == 0:
                ax.legend(loc='upper right', fontsize=8)

        for k in range(n, rows_ * cols):
            axes[k // cols, k % cols].set_visible(False)

        suptitle = (
            f"All video spindowns: RPM(t)"
            f"{' [log scale]' if log_y else ''}.  "
            f"smoothing window {SMOOTH_WINDOW_S}s, "
            f"video=black, CSC=red dots. "
            f"{'Pure exp → straight line.' if log_y else ''}")
        plt.suptitle(suptitle, fontsize=11)
        plt.tight_layout(rect=[0, 0, 1, 0.97])
        out_path.parent.mkdir(parents=True, exist_ok=True)
        plt.savefig(out_path, dpi=120)
        plt.close()
        print(f"wrote {out_path} ({n} panels)")


if __name__ == "__main__":
    main()
