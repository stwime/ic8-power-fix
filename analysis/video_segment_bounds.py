"""Per-segment spin-down bounds derived from video, not CSC.

For each CSC-identified spindown segment (rough window): pull the video
|ω| trace over a generous window around it, smooth, and detect:

  1. ``t_stop``: the moment the rider stopped pedaling. Pedaling shows as
     a wobble in |ω| at the cadence frequency — adjacent halves of a
     pedal stroke have unequal torque so |ω| rises and falls each half
     rev. Free-coasting is smooth monotonic decay. We detect t_stop as
     the last local maximum in a heavily-smoothed |ω| trace, beyond
     which |ω| is monotonically non-increasing for at least 0.5 s.

  2. ``t_floor``: the moment the bike came to rest. Cum-angle plateaus
     when |ω| → 0; we detect the first frame after t_stop where the
     50-frame slope of cum-angle drops below 0.3 rad/s and stays there.

These are the *physical* spindown bounds, independent of CSC's
notification jitter. We also report the CSC-derived bounds for the
same segment for direct comparison — if the implied lag drifts across
the recording, that's the alignment-drift smoking gun.

Usage:
    python analysis/video_segment_bounds.py
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

OUT_PATH = (Path(__file__).resolve().parent.parent
            / "data/calibration/spindown_plots/video_segment_bounds.png")
SMOOTH_S = 0.5  # |ω| windowed-slope window for ω(t) estimation
HEAVY_SMOOTH_S = 0.0  # extra running-mean for stop detection (0 = use raw)
LIGHT_SMOOTH_S = 0.3  # light smoothing used for floor crossing
LOOKAHEAD_S = 0.5  # window over which to take max for stop detection
MIN_MONOTONIC_S = 0.5  # require non-increasing for this long after t_stop
FLOOR_OMEGA = 0.1  # rad/s — bike-at-rest threshold (≈1 rpm)
PRE_BUFFER_S = 2.0  # video window starts this many s before CSC start
POST_BUFFER_S = 5.0  # video window extends this many s past CSC end


def windowed_omega(t: np.ndarray, cum: np.ndarray,
                   window_s: float) -> np.ndarray:
    """Centred local linear fit; returns ω at every frame."""
    n = len(t)
    out = np.full(n, np.nan)
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
        out[i] = np.polyfit(ts, cs, 1)[0]
    return out


def edge_safe_mean(x: np.ndarray, k: int) -> np.ndarray:
    """Centred running mean that uses only real samples near the edges
    (no zero-padding). For each i, average over ``x[max(0,i-k//2):
    min(n,i+k//2+1)]``."""
    n = len(x)
    out = np.empty(n, dtype=float)
    half = k // 2
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        out[i] = float(np.mean(x[lo:hi]))
    return out


def detect_stop(t: np.ndarray, abs_omega: np.ndarray,
                min_monotonic_s: float,
                i_floor: int | None = None,
                tol: float = 0.3,
                lookahead_s: float = LOOKAHEAD_S) -> int | None:
    """t_stop = the latest local peak before the floor.

    Walks back from ``i_floor``. At each index ``i`` we compare the
    current value against the MAX of the next ``lookahead_s`` window of
    samples. If ``v[i] >= max_ahead - tol`` the chain extends leftward.
    Otherwise we've just walked past a genuine local maximum (the peak
    at the start of the cleanest decay tail) and return its index.

    Using the max of a short look-ahead window — instead of the
    immediate neighbour — absorbs sample-level noise without walking
    past real pedal-touch peaks. Using the local max — instead of a
    global running_max — lets us pick a small late peak even when an
    earlier struggle peak is higher.
    """
    n = len(t)
    end = i_floor if i_floor is not None else n - 1
    if end < 8:
        return None
    if not np.isfinite(abs_omega[end]):
        return None
    med_dt = float(np.median(np.diff(t)))
    K = max(1, int(round(lookahead_s / med_dt)))
    i_stop = end
    for i in range(end - 1, -1, -1):
        v = abs_omega[i]
        if not np.isfinite(v):
            break
        upper = min(end + 1, i + 1 + K)
        ahead = abs_omega[i + 1:upper]
        if len(ahead) == 0:
            break
        v_ahead = float(np.nanmax(ahead))
        if v >= v_ahead - tol:
            i_stop = i
        else:
            # Dip going forward — i+1..upper contains a local peak.
            offset = int(np.nanargmax(ahead))
            i_stop = i + 1 + offset
            break
    if t[end] - t[i_stop] < min_monotonic_s:
        return None
    return i_stop


def detect_floor(t: np.ndarray, abs_omega: np.ndarray,
                 i_stop: int, floor: float,
                 cum_angle: np.ndarray | None = None,
                 sustain_s: float = 0.5) -> int | None:
    """First index after i_stop where smoothed |ω| stays below ``floor``
    for at least ``sustain_s``. If the signal never crosses (decay still
    ongoing at end of window), returns the last index. The cum_angle arg
    is unused here but kept for caller compatibility.
    """
    del cum_angle
    n = len(t)
    for i in range(i_stop, n):
        if abs_omega[i] < floor:
            run_end = i
            while run_end + 1 < n and abs_omega[run_end + 1] < floor:
                run_end += 1
                if t[run_end] - t[i] >= sustain_s:
                    return i
            if t[run_end] - t[i] >= sustain_s:
                return i
    return n - 1


def find_csc_wheel_stop(rows: list, seg_end_idx: int) -> float | None:
    """Walk forward in ``rows`` past ``seg_end_idx`` until cumulative
    wheel_revs has been seen unchanged for ≥2 valid samples; return the
    timestamp of the LAST distinct wheel-rev sample (= the moment the
    flywheel made its final rotation).
    """
    last_wr = None
    last_ts = None
    for i in range(max(0, seg_end_idx - 5), len(rows)):
        wr = rows[i].get("wheel_revs")
        ts = rows[i].get("timestamp_s")
        if wr in (None, "") or ts is None:
            continue
        try:
            wr = int(wr)
        except (TypeError, ValueError):
            continue
        if last_wr is None or wr > last_wr:
            last_wr = wr
            last_ts = float(ts)
        elif wr == last_wr:
            # If we see ≥2 consecutive same-wr after this, last_ts is the
            # final-rev moment.
            ahead_same = 0
            for j in range(i, min(i + 5, len(rows))):
                wr_j = rows[j].get("wheel_revs")
                if wr_j in (None, ""):
                    continue
                try:
                    if int(wr_j) == last_wr:
                        ahead_same += 1
                except (TypeError, ValueError):
                    pass
            if ahead_same >= 2:
                return last_ts
    return last_ts


def stop_tol_for_R(R: int) -> float:
    """Tolerance (rad/s) for the stop-detection envelope walk.

    At low R the decay is slow and the |ω| trace flutters from the rider
    not having lifted as cleanly, producing many small spurious peaks that
    we don't want the algorithm to catch. At high R the decay is fast and
    the small peaks are genuine pedal-touch artifacts that mark the actual
    lift moment, so we want to land on them.
    """
    return 1.0 if R < 57 else 0.2


def detect_segment_bounds(seg, t_v, ang_v_cum_full, R: int | None = None):
    """Return (i_start, i_end, t_stop_video, t_floor_video) where indices
    are into t_v / ang_v_cum_full. Returns None if detection fails.

    i_start = i_stop (segment starts at the rider-stopped moment).
    i_end   = i_floor if the bike fully stopped, else last in-window frame.
    """
    t_csc_start = seg[0]["timestamp_s"]
    t_csc_end = seg[-1]["timestamp_s"]
    tv0 = (t_csc_start - PRE_BUFFER_S) - LAG
    tv1 = (t_csc_end + POST_BUFFER_S) - LAG
    m = (t_v >= tv0) & (t_v <= tv1)
    if m.sum() < 30:
        return None
    idx = np.where(m)[0]
    tt = t_v[idx]
    cum = ang_v_cum_full[idx] - ang_v_cum_full[idx[0]]
    omega = windowed_omega(tt, cum, SMOOTH_S)
    abs_om = np.abs(omega)
    med_dt = np.median(np.diff(tt))
    k_heavy = max(1, int(round(HEAVY_SMOOTH_S / med_dt)))
    k_light = max(1, int(round(LIGHT_SMOOTH_S / med_dt)))
    abs_om_heavy = edge_safe_mean(abs_om, k_heavy)
    abs_om_light = edge_safe_mean(abs_om, k_light)
    scan_start = int(0.3 * len(tt))
    i_floor_local = detect_floor(tt, abs_om_light, scan_start, FLOOR_OMEGA,
                                 cum_angle=cum)
    tol = stop_tol_for_R(R) if R is not None else 0.3
    i_stop_local = detect_stop(tt, abs_om_heavy, MIN_MONOTONIC_S,
                               i_floor_local, tol=tol)
    if i_stop_local is None:
        return None
    i_start_global = idx[i_stop_local]
    if i_floor_local is not None:
        i_end_global = idx[i_floor_local]
    else:
        i_end_global = idx[-1]
    return (int(i_start_global), int(i_end_global),
            float(t_v[i_start_global]),
            float(t_v[i_end_global]) if i_floor_local is not None else None)


def main():
    rows = parse_log(LOG)
    segs = find_clean_coastdowns(rows)
    t_v, ang_v = load_video_modpi(VIDEO_CSV)
    cum_v_all = integrate_to_cumulative(ang_v)

    panels = []
    per_R = {}
    for seg, R, term in segs:
        occ = per_R.get(R, 0); per_R[R] = occ + 1
        if R == 0 and occ == 0:
            continue
        # CSC bounds for this segment (in wall clock).
        t_csc_start = seg[0]["timestamp_s"]
        t_csc_end = seg[-1]["timestamp_s"]
        # CSC's "second rev event" → its idea of post-stop start.
        E0 = T0 = E1 = None
        for r in seg:
            ts = r["timestamp_s"]; et = r.get("crank_event_time_s")
            if ts is None or et is None:
                continue
            if E0 is None:
                E0, T0 = float(et), float(ts)
            elif float(et) > E0 + 1e-6:
                E1 = float(et); break
        t_csc_post = (E1 + (T0 - E0)) if E1 is not None else t_csc_start

        # Pull a generous window of video frames around the CSC bounds.
        tv0 = (t_csc_start - PRE_BUFFER_S) - LAG
        tv1 = (t_csc_end + POST_BUFFER_S) - LAG
        m = (t_v >= tv0) & (t_v <= tv1)
        if m.sum() < 30:
            continue
        tt = t_v[m]
        cum = cum_v_all[m] - cum_v_all[m][0]
        omega = windowed_omega(tt, cum, SMOOTH_S)
        abs_om_raw = np.abs(omega)
        med_dt = np.median(np.diff(tt))
        k_heavy = max(1, int(round(HEAVY_SMOOTH_S / med_dt)))
        k_light = max(1, int(round(LIGHT_SMOOTH_S / med_dt)))
        abs_om_heavy = edge_safe_mean(abs_om_raw, k_heavy)
        abs_om_light = edge_safe_mean(abs_om_raw, k_light)

        scan_start = int(0.3 * len(tt))
        i_floor = detect_floor(tt, abs_om_light, scan_start, FLOOR_OMEGA,
                               cum_angle=cum)
        i_stop = detect_stop(tt, abs_om_heavy, MIN_MONOTONIC_S, i_floor,
                             tol=stop_tol_for_R(R))
        abs_om_smooth = abs_om_heavy  # for plot
        if i_stop is None:
            continue
        t_stop_video = float(tt[i_stop])
        t_floor_video = float(tt[i_floor]) if i_floor is not None else None

        # Implied lag for THIS segment:
        # if CSC says post-stop starts at t_csc_post, and video says rider
        # stopped at t_stop_video, then lag = t_csc_post - t_stop_video.
        # (matches the original convention: add LAG to t_video → t_log)
        implied_lag_post = t_csc_post - t_stop_video

        panels.append({
            "R": R, "occ": occ, "term": term,
            "tt": tt, "cum": cum, "abs_om": abs_om_raw,
            "abs_om_smooth": abs_om_smooth,
            "t_stop": t_stop_video, "t_floor": t_floor_video,
            "t_csc_start_video": t_csc_start - LAG,
            "t_csc_post_video": t_csc_post - LAG,
            "t_csc_end_video": t_csc_end - LAG,
            "implied_lag_post": implied_lag_post,
        })

    if not panels:
        sys.exit("no panels")

    # Sort by ABSOLUTE video time so we can see drift over the recording.
    panels.sort(key=lambda p: p["t_stop"])
    print(f"\nper-segment alignment vs. global lag = {LAG:+.3f}s "
          f"(t_log = t_video + lag).")
    print(f"  implied lag = t_csc_post − t_stop_video; "
          f"if alignment is correct and CSC's rev-1 = actual stop, this "
          f"matches LAG.")
    print(f"\n{'R':>3} {'occ':>3} {'t_video':>9} {'t_csc_post':>11} "
          f"{'implied_lag':>12} {'t_floor_v':>10} {'term':>14}")
    for p in panels:
        floor = f"{p['t_floor']:>10.2f}" if p['t_floor'] is not None else f"{'-':>10}"
        print(f"{p['R']:>3} {p['occ']:>3} {p['t_stop']:>9.2f} "
              f"{p['t_csc_post_video']:>11.2f} "
              f"{p['implied_lag_post']:>+12.3f} {floor} {p['term']:>14}")

    # Plot |ω| per segment with vertical markers for t_stop, t_csc_post,
    # t_floor.
    n = len(panels)
    cols = 4
    rows_ = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows_, cols,
                             figsize=(4 * cols, 2.6 * rows_),
                             sharex=False, sharey=False)
    axes = np.atleast_2d(axes)
    for i, p in enumerate(panels):
        ax = axes[i // cols, i % cols]
        t_rel = p["tt"] - p["tt"][0]
        rpm_raw = p["abs_om"] * 60.0 / (2 * math.pi)
        rpm_smooth = p["abs_om_smooth"] * 60.0 / (2 * math.pi)
        ax.plot(t_rel, rpm_raw, 'k-', lw=0.5, alpha=0.35, label='|ω| raw')
        ax.plot(t_rel, rpm_smooth, 'C0-', lw=1.0, label='|ω| smooth')
        t_stop_rel = p["t_stop"] - p["tt"][0]
        # Shade the fit window (stop → floor) so it's unambiguous.
        if p['t_floor'] is not None:
            t_floor_rel = p["t_floor"] - p["tt"][0]
            ax.axvspan(t_stop_rel, t_floor_rel, color='C2', alpha=0.12,
                       label='fit window')
            ax.axvline(t_floor_rel, color='C1', ls=':', lw=1.0,
                       label='floor')
        ax.axvline(t_stop_rel, color='C2', ls='-', lw=1.5,
                   label='stop')
        n_in_window = int(((p["tt"] >= p["t_stop"]) &
                           ((p["tt"] <= p["t_floor"])
                            if p["t_floor"] is not None else True)).sum())
        ax.set_title(f"R={p['R']} occ={p['occ']}  n_fit={n_in_window}",
                     fontsize=9)
        ax.set_xlabel('t (s, relative)', fontsize=8)
        ax.set_ylabel('|ω| (rpm)', fontsize=8)
        ax.tick_params(labelsize=8)
        ax.grid(alpha=0.3)
        ax.set_ylim(bottom=0)
        if i == 0:
            ax.legend(loc='upper right', fontsize=7)
    for k in range(n, rows_ * cols):
        axes[k // cols, k % cols].set_visible(False)
    plt.suptitle(
        "Video-detected fit windows for each spindown. "
        "Shaded region is the actual data fed to the v2 fit (stop → floor).",
        fontsize=11)
    plt.tight_layout(rect=[0, 0, 1, 0.96])
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(OUT_PATH, dpi=120)
    plt.close()
    print(f"\nwrote {OUT_PATH} ({n} panels)")


if __name__ == "__main__":
    main()
