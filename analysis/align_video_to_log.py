"""Align a crank-tracked video against an nRF Connect BLE log.

Inputs:
    - nRF Connect log (.txt) with FTMS+CSC notifications. We compute
      ω_csc(t_log) from cumulative crank_revs / crank_event_time_s.
    - crank_video.csv from track_crank.py with ω_video(t_video).

Both signals are resampled to a uniform grid, then we cross-correlate to
find the constant lag that best aligns them. The video covers a subset of
the log (extra log padding at both ends), so the search is over the lag
that maps t_video to t_log.

Output: a merged CSV with ω_csc, ω_video, and the implied wall-clock
offset, plus a printed summary of the lag and correlation peak.

Usage:
    python analysis/align_video_to_log.py \
        "data/calibration/Log 2026-04-30 19_37_28.txt" \
        crank_video.csv \
        --output aligned.csv
"""

from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_nrf_log import parse_log  # noqa: E402


def log_activity(rows: list[dict], min_rpm: float = 5.0) -> tuple[np.ndarray, np.ndarray]:
    """Per-FTMS-row pedaling indicator. Active iff CSC cadence >= min_rpm."""
    t, a = [], []
    for r in rows:
        ts = r["timestamp_s"]
        cad = r["cadence_rpm_csc"]
        if ts is None:
            continue
        t.append(ts)
        a.append(1.0 if (cad is not None and cad >= min_rpm) else 0.0)
    return np.asarray(t), np.asarray(a)


def csc_omega_series(rows: list[dict]) -> tuple[np.ndarray, np.ndarray]:
    """Per-revolution ω from CSC: between successive distinct crank events.

    Each event-time pair gives an exact average ω over that interval. We
    stamp the value at the midpoint of the two event times. The log's
    timestamp_s is the BLE-arrival time (jittery by up to ~0.5 s), so we use
    crank_event_time_s instead, but rebased so it lives on the same wall
    clock as timestamp_s. The rebasing offset is fixed per log: pick the
    first sample where both are present and align them there.
    """
    t = []
    w = []
    rebase = None  # crank_event_time_s -> timestamp_s offset
    last_revs = None
    last_evt = None
    for r in rows:
        revs = r["crank_revs"]
        evt = r["crank_event_time_s"]
        ts = r["timestamp_s"]
        if revs is None or evt is None or ts is None:
            continue
        if rebase is None:
            rebase = ts - evt
        evt_wall = evt + rebase
        if last_revs is not None and revs > last_revs and evt_wall > last_evt + 1e-6:
            d_revs = revs - last_revs
            d_t = evt_wall - last_evt
            omega = 2 * math.pi * d_revs / d_t
            t.append(0.5 * (evt_wall + last_evt))
            w.append(omega)
        if last_evt is None or evt_wall > last_evt + 1e-6:
            last_revs = revs
            last_evt = evt_wall
    return np.asarray(t), np.asarray(w)


def load_video_omega(path: Path) -> tuple[np.ndarray, np.ndarray]:
    t, w = [], []
    with path.open() as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            if not row["omega_rad_s"]:
                continue
            t.append(float(row["t_video_s"]))
            w.append(float(row["omega_rad_s"]))
    return np.asarray(t), np.asarray(w)


def resample(t: np.ndarray, y: np.ndarray, t_grid: np.ndarray) -> np.ndarray:
    """Linear interpolation onto t_grid, with NaN outside [t.min, t.max]."""
    out = np.interp(t_grid, t, y, left=np.nan, right=np.nan)
    return out


def find_lag(t_log: np.ndarray, log_act: np.ndarray,
             t_vid: np.ndarray, w_vid: np.ndarray,
             dt: float, max_abs_lag: float,
             video_omega_cap: float = 17.0,
             smooth_window_s: float = 1.0,
             active_thresh: float = 0.5) -> tuple[float, float]:
    """Activity-based cross-correlation. Returns (lag_s, peak_corr).

    'lag' is what to add to video time to get log time:
        t_log = t_video + lag

    Why activity rather than raw |ω|: video |ω| during pedaling is heavily
    contaminated by motion blur and shoe occlusion (PCA flips on a
    half-masked frame), and CSC |ω| during spin-down rest is just empty.
    Both signals are clean as a binary "is the rider pedaling now?" — that
    feature gives a sharp, unambiguous correlation peak.

    Pipeline:
      1. cap video |ω| at video_omega_cap (drop blur/occlusion spikes),
      2. smooth |ω| with a moving average of length smooth_window_s,
      3. threshold both signals (video via active_thresh on smoothed |ω|,
         CSC via inherent zero/non-zero), giving binary activity,
      4. cross-correlate at integer-second lags then refine to dt.
    """
    # Build binary activity on a 1 Hz grid for both signals.
    grid_dt = 1.0
    log_grid = np.arange(0, t_log.max() + 1, grid_dt)
    log_act_grid = (np.interp(log_grid, t_log, log_act) > 0.5).astype(float)

    vid_grid = np.arange(0, t_vid.max() + 1, grid_dt)
    w_capped = np.where(np.abs(w_vid) > video_omega_cap, 0.0, np.abs(w_vid))
    # Smooth in raw video sample domain, then resample.
    if len(t_vid) > 2:
        # Approximate sample rate from median dt (robust to gaps).
        med_dt = float(np.median(np.diff(t_vid)))
        k = max(1, int(round(smooth_window_s / med_dt)))
        kernel = np.ones(k) / k
        w_smooth = np.convolve(w_capped, kernel, mode="same")
    else:
        w_smooth = w_capped
    vid_smooth_on_grid = np.interp(vid_grid, t_vid, w_smooth)
    vid_act = (vid_smooth_on_grid > active_thresh).astype(float)

    def corr_at(L: float) -> tuple[float, int]:
        # Look up video activity at (log_grid - L), where log_grid is in t_log.
        v = np.interp(log_grid - L, vid_grid, vid_act, left=-1, right=-1)
        ok = v >= 0
        if ok.sum() < 30:
            return -1.0, 0
        a = log_act_grid[ok] - log_act_grid[ok].mean()
        b = v[ok] - v[ok].mean()
        if np.std(a) < 1e-6 or np.std(b) < 1e-6:
            return 0.0, int(ok.sum())
        return (float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-12)),
                int(ok.sum()))

    # Coarse 1 s lag scan.
    best_c = (-np.inf, 0.0, 0)
    for L in np.arange(-max_abs_lag, max_abs_lag + 1, 1.0):
        c, n = corr_at(L)
        if c > best_c[0]:
            best_c = (c, L, n)
    coarse_lag = best_c[1]

    # Fine refinement at dt around the coarse peak.
    best_f = (-np.inf, coarse_lag)
    for L in np.arange(coarse_lag - 1.0, coarse_lag + 1.0 + dt, dt):
        c, _ = corr_at(L)
        if c > best_f[0]:
            best_f = (c, L)
    return best_f[1], best_f[0]


def run(args):
    rows = parse_log(Path(args.log))
    if not rows:
        sys.exit("no notifications in log")
    t_csc, w_csc = csc_omega_series(rows)
    t_log_act, log_act = log_activity(rows)
    print(f"log: {len(rows)} FTMS rows, {len(t_csc)} CSC ω samples "
          f"({t_csc.min():.1f}..{t_csc.max():.1f} s), "
          f"active fraction {log_act.mean():.2f}")

    t_vid, w_vid = load_video_omega(Path(args.video_csv))
    print(f"video: {len(t_vid)} ω samples "
          f"({t_vid.min():.1f}..{t_vid.max():.1f} s)")

    lag, peak = find_lag(t_log_act, log_act, t_vid, w_vid,
                         dt=args.fine_dt, max_abs_lag=args.max_lag_s,
                         video_omega_cap=args.omega_cap,
                         smooth_window_s=args.smooth_s,
                         active_thresh=args.active_thresh)
    print(f"lag (add to t_video to get t_log) = {lag:+.3f} s  "
          f"(activity correlation {peak:.3f})")

    if args.output:
        out = Path(args.output)
        # Resample both onto a 30 Hz grid spanning the video.
        dt_out = 1 / 30.0
        grid = np.arange(t_vid.min(), t_vid.max(), dt_out)
        log_grid = grid + lag
        wlog_g = resample(t_csc, w_csc, log_grid)
        wvid_g = resample(t_vid, w_vid, grid)
        with out.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t_video_s", "t_log_s", "omega_csc_rad_s",
                        "omega_video_rad_s"])
            for tv, tl, wl, wv in zip(grid, log_grid, wlog_g, wvid_g):
                w.writerow([f"{tv:.4f}", f"{tl:.4f}",
                            "" if math.isnan(wl) else f"{wl:.4f}",
                            "" if math.isnan(wv) else f"{wv:.4f}"])
        print(f"wrote {out}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("log", help="nRF Connect .txt log")
    p.add_argument("video_csv", help="crank_video.csv from track_crank.py")
    p.add_argument("--output", "-o", default=None,
                   help="Merged CSV on a 30 Hz video-time grid.")
    p.add_argument("--max-lag-s", type=float, default=3700.0,
                   help="Max |lag| to search. Default ~1 hr handles "
                        "any UTC-vs-local mismatch.")
    p.add_argument("--fine-dt", type=float, default=0.05,
                   help="Sub-second lag refinement step.")
    p.add_argument("--omega-cap", type=float, default=17.0,
                   help="Drop video |ω| above this (rad/s) as blur/occlusion.")
    p.add_argument("--smooth-s", type=float, default=1.0,
                   help="Moving-average window for video |ω| before "
                        "thresholding into binary activity.")
    p.add_argument("--active-thresh", type=float, default=0.5,
                   help="Smoothed video |ω| (rad/s) above which a sample "
                        "counts as 'pedaling'.")
    args = p.parse_args()
    run(args)


if __name__ == "__main__":
    main()
