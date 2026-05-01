"""Detect spindown segments in a crank-tracking CSV (no CSC log needed).

A spindown looks like: |ω| spikes up to a peak (rider pedaled hard), then
decays smoothly to ~0 (rider stopped, flywheel coasting). Pedaling shows
as fast wobble in |ω|; free-coast is monotonic. We:

    1. Compute smoothed |ω(t)| from a windowed local-linear fit on the
       unwrapped crank angle (robust to single-frame PCA noise).
    2. Slice the full trace into "below-floor" runs separated by
       "above-floor" runs. Each above-floor run that contains a peak
       above MIN_PEAK is a candidate spindown attempt.
    3. Within each candidate, find t_floor (first sustained crossing of
       FLOOR after the peak) and t_stop (last local max before t_floor;
       walk back from t_floor accepting samples whose value is within
       TOL of the max of a small forward look-ahead window).
    4. Drop attempts shorter than MIN_DURATION_S or where ω rebounds
       inside the window above REBOUND_TOL (the rider re-engaged).

Outputs:
    crank_video.spindowns.csv       — same columns as input plus
                                       spindown_id, with everything
                                       outside detected windows dropped.
    spindowns_summary.png           — one panel per detected spindown.
    stdout: a table of segment bounds for sanity-checking.

Usage:
    python analysis/extract_spindowns_from_video.py <crank_video.csv> [--out-dir DIR]
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

OMEGA_WINDOW_S = 0.2      # local-linear fit window for ω(t) from cum angle
SMOOTH_S = 0.1            # extra running-mean smoothing for stop/floor logic
FLOOR = 0.1               # rad/s — bike-at-rest threshold (≈1 rpm)
MIN_PEAK = 5.0            # rad/s — a candidate must peak above this (≈50 rpm)
MIN_DURATION_S = 1.0      # spindown shorter than this is noise/garbage
REBOUND_TOL = 1.5         # rad/s — if |ω| rises above (current local min + this)
                          #          within the window after the peak, drop it
PEAK_MIN_PROMINENCE = 1.0 # rad/s — local-max prominence threshold for t_stop
PRE_BUFFER_S = 0.5        # include this much trace before t_stop in the output


def windowed_omega(t: np.ndarray, cum: np.ndarray, window_s: float) -> np.ndarray:
    """Centred local linear fit of cum(t); returns dω/dt at every frame."""
    n = len(t)
    out = np.full(n, np.nan)
    half = window_s / 2
    for i in range(n):
        lo = i
        while lo > 0 and t[i] - t[lo - 1] < half:
            lo -= 1
        hi = i
        while hi < n - 1 and t[hi + 1] - t[i] < half:
            hi += 1
        if hi - lo < 3:
            continue
        ts = t[lo:hi + 1] - t[i]
        cs = cum[lo:hi + 1]
        out[i] = np.polyfit(ts, cs, 1)[0]
    return out


def edge_safe_mean(x: np.ndarray, k: int) -> np.ndarray:
    n = len(x); out = np.empty(n, dtype=float); half = k // 2
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out[i] = float(np.mean(x[lo:hi]))
    return out


def detect_floor(abs_om: np.ndarray, t: np.ndarray, i_start: int,
                 floor: float, sustain_s: float = 0.5) -> int | None:
    """First index >= i_start where |ω| stays below floor for sustain_s."""
    n = len(t)
    for i in range(i_start, n):
        if abs_om[i] < floor:
            run_end = i
            while run_end + 1 < n and abs_om[run_end + 1] < floor:
                run_end += 1
                if t[run_end] - t[i] >= sustain_s:
                    return i
            if run_end < n - 1 and t[run_end] - t[i] >= sustain_s:
                return i
    return None


def detect_stop(abs_om: np.ndarray, t: np.ndarray,
                i_run_start: int, i_floor: int,
                min_prominence: float = PEAK_MIN_PROMINENCE) -> int | None:
    """Latest local maximum in [i_run_start, i_floor] with prominence
    > min_prominence. Prominence = peak_value − max(left_min, right_min)
    where the mins are taken between this peak and the nearest higher
    peak on either side (or the segment boundary if none).

    Picking the *latest* (rather than the highest) prominence-passing peak
    is the right semantics for spin-down extraction: it's the rider's last
    significant momentum injection before the free coast we want to fit.
    """
    if i_floor <= i_run_start + 1:
        return None
    seg = abs_om[i_run_start:i_floor + 1]
    n = len(seg)
    valid_peaks: list[int] = []
    for i in range(1, n - 1):
        if seg[i] >= seg[i - 1] and seg[i] >= seg[i + 1]:
            # left base: walk back until value > seg[i] or boundary
            left_min = seg[i]
            for j in range(i - 1, -1, -1):
                if seg[j] > seg[i]:
                    break
                if seg[j] < left_min:
                    left_min = seg[j]
            # right base
            right_min = seg[i]
            for j in range(i + 1, n):
                if seg[j] > seg[i]:
                    break
                if seg[j] < right_min:
                    right_min = seg[j]
            prom = seg[i] - max(left_min, right_min)
            if prom >= min_prominence:
                valid_peaks.append(i)
    if not valid_peaks:
        return i_run_start + int(np.argmax(seg))
    return i_run_start + valid_peaks[-1]


def find_active_runs(abs_om: np.ndarray, t: np.ndarray, floor: float,
                     min_peak: float) -> list[tuple[int, int]]:
    """Find runs where |ω| is above floor and contains at least one sample
    above min_peak. Returns list of (start_idx, end_idx) inclusive."""
    above = abs_om >= floor
    runs = []
    i = 0
    n = len(abs_om)
    while i < n:
        if not above[i]:
            i += 1; continue
        j = i
        while j + 1 < n and above[j + 1]:
            j += 1
        if abs_om[i:j + 1].max() >= min_peak:
            runs.append((i, j))
        i = j + 1
    return runs


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("csv", type=Path, help="crank_video.csv")
    ap.add_argument("--out-dir", type=Path, default=None,
                    help="defaults to the CSV's directory")
    args = ap.parse_args()

    out_dir = args.out_dir or args.csv.parent
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load
    rows = list(csv.DictReader(args.csv.open()))
    n = len(rows)
    t = np.array([float(r["t_video_s"]) for r in rows])
    ang_raw = np.array([float(r["angle_unwrapped_rad"]) if r["angle_unwrapped_rad"]
                        else np.nan for r in rows])
    # Linear-interp NaNs so the local-fit ω is defined everywhere
    bad = np.isnan(ang_raw)
    if bad.any():
        good_idx = np.where(~bad)[0]
        ang = np.interp(np.arange(n), good_idx, ang_raw[good_idx])
    else:
        ang = ang_raw

    print(f"loaded {n} frames, {bad.sum()} interpolated "
          f"({100*bad.sum()/n:.1f}%)")

    omega = windowed_omega(t, ang, OMEGA_WINDOW_S)
    abs_om_raw = np.abs(omega)
    med_dt = float(np.median(np.diff(t)))
    k_smooth = max(1, int(round(SMOOTH_S / med_dt)))
    abs_om = edge_safe_mean(abs_om_raw, k_smooth)

    runs = find_active_runs(abs_om, t, FLOOR, MIN_PEAK)
    print(f"found {len(runs)} active runs above floor={FLOOR} containing "
          f"a peak >= {MIN_PEAK} rad/s")

    spindowns = []
    for i_lo, i_hi in runs:
        # peak inside this run
        i_peak_local = int(np.argmax(abs_om[i_lo:i_hi + 1])) + i_lo
        # floor: first sustained sub-floor sample after the peak. Search up
        # to and slightly past the run's hi (might cross right at the edge).
        search_hi = min(len(t) - 1, i_hi + int(round(1.0 / med_dt)))
        i_floor = detect_floor(abs_om, t, i_peak_local, FLOOR,
                               sustain_s=0.5)
        if i_floor is None or i_floor > search_hi:
            continue  # rider didn't fully stop — skip
        i_stop = detect_stop(abs_om, t, i_lo, i_floor)
        if i_stop is None:
            continue
        # Reject if duration too short
        duration = t[i_floor] - t[i_stop]
        if duration < MIN_DURATION_S:
            continue
        # Reject if rebound inside (stop, floor): if there's a local minimum
        # followed by a peak above (min + REBOUND_TOL), the rider re-engaged.
        seg = abs_om[i_stop:i_floor + 1]
        running_min = np.minimum.accumulate(seg)
        max_rebound = float(np.max(seg - running_min))
        if max_rebound > REBOUND_TOL:
            continue
        peak_om = float(abs_om[i_peak_local])
        spindowns.append({
            "i_stop": int(i_stop),
            "i_floor": int(i_floor),
            "t_stop": float(t[i_stop]),
            "t_floor": float(t[i_floor]),
            "peak_omega": peak_om,
            "peak_rpm": peak_om * 60 / (2 * math.pi),
            "duration_s": float(duration),
        })

    print(f"\naccepted {len(spindowns)} spindowns")
    print(f"\n{'#':>3} {'t_stop':>8} {'t_floor':>8} {'dur(s)':>7} "
          f"{'peak_rpm':>9} {'ω₀(rad/s)':>10}")
    for k, s in enumerate(spindowns, 1):
        print(f"{k:>3} {s['t_stop']:>8.2f} {s['t_floor']:>8.2f} "
              f"{s['duration_s']:>7.2f} {s['peak_rpm']:>9.1f} "
              f"{s['peak_omega']:>10.3f}")

    # Write trimmed CSV
    out_csv = out_dir / "crank_video.spindowns.csv"
    fields = list(rows[0].keys()) + ["spindown_id"]
    with out_csv.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for k, s in enumerate(spindowns, 1):
            i0 = max(0, int(np.searchsorted(t, s["t_stop"] - PRE_BUFFER_S)))
            i1 = s["i_floor"]
            for i in range(i0, i1 + 1):
                r = dict(rows[i]); r["spindown_id"] = k
                w.writerow(r)
    print(f"\nwrote {out_csv}")

    # Full-timeline overview: |ω(t)| with detected spindown windows shaded
    if spindowns:
        fig, ax = plt.subplots(figsize=(16, 4))
        ax.plot(t, abs_om_raw * 60 / (2 * math.pi),
                color='0.6', lw=0.4, label='|ω| raw')
        ax.plot(t, abs_om * 60 / (2 * math.pi),
                'C0-', lw=0.8, label='|ω| smoothed')
        for k, s in enumerate(spindowns, 1):
            ax.axvspan(s["t_stop"], s["t_floor"], color='C2', alpha=0.18)
            ax.text(s["t_stop"], s["peak_rpm"] * 1.05, str(k),
                    fontsize=8, ha='left', va='bottom', color='C2')
        ax.set_xlabel('t (s)')
        ax.set_ylabel('|ω| (rpm)')
        ax.set_title(f"Full ω(t) — green spans = detected spindowns "
                     f"({len(spindowns)})")
        ax.grid(alpha=0.3)
        ax.legend(loc='upper right')
        ax.set_ylim(bottom=0)
        plt.tight_layout()
        out_overview = out_dir / "spindowns_overview.png"
        plt.savefig(out_overview, dpi=120)
        plt.close()
        print(f"wrote {out_overview}")

    # Summary plot — one panel per spindown
    if spindowns:
        cols = 4
        rows_ = (len(spindowns) + cols - 1) // cols
        fig, axes = plt.subplots(rows_, cols, figsize=(4 * cols, 2.4 * rows_),
                                 sharex=False, sharey=False)
        axes = np.atleast_2d(axes)
        for k, s in enumerate(spindowns):
            ax = axes[k // cols, k % cols]
            i0 = max(0, s["i_stop"] - int(round(2.0 / med_dt)))
            i1 = min(n - 1, s["i_floor"] + int(round(1.0 / med_dt)))
            tt = t[i0:i1 + 1]
            ax.plot(tt - tt[0], abs_om_raw[i0:i1 + 1] * 60 / (2 * math.pi),
                    'k-', lw=0.5, alpha=0.35, label='|ω| raw')
            ax.plot(tt - tt[0], abs_om[i0:i1 + 1] * 60 / (2 * math.pi),
                    'C0-', lw=1.0, label='|ω| smooth')
            ax.axvspan(t[s["i_stop"]] - tt[0], t[s["i_floor"]] - tt[0],
                       color='C2', alpha=0.15)
            ax.axvline(t[s["i_stop"]] - tt[0], color='C2', lw=1.5)
            ax.axvline(t[s["i_floor"]] - tt[0], color='C1', ls=':', lw=1.0)
            ax.set_title(f"#{k+1}: {s['duration_s']:.1f}s, "
                         f"{s['peak_rpm']:.0f} rpm peak", fontsize=9)
            ax.set_xlabel('t (s, rel)', fontsize=8)
            ax.set_ylabel('|ω| (rpm)', fontsize=8)
            ax.tick_params(labelsize=8)
            ax.grid(alpha=0.3)
            ax.set_ylim(bottom=0)
        for k in range(len(spindowns), rows_ * cols):
            axes[k // cols, k % cols].set_visible(False)
        plt.suptitle(f"{args.csv.parent.name} — {len(spindowns)} spindowns",
                     fontsize=11)
        plt.tight_layout(rect=[0, 0, 1, 0.96])
        out_png = out_dir / "spindowns_summary.png"
        plt.savefig(out_png, dpi=120)
        plt.close()
        print(f"wrote {out_png}")


if __name__ == "__main__":
    main()
