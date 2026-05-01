"""Build one authoritative CSV of every spindown + a grid plot.

Source of truth
---------------
data/calibration/spindown_bounds.json (curated by hand via curate_spindowns.py).

Each entry is {source, candidate_id, R, occ, keep, t_in, t_out}. We slice
each source's per-frame/per-rev data by [t_in, t_out] and extract per-rev
ω from there. No auto-detection heuristics in this script — the heavy
lifting is done in the curation tool.

Sources
-------
BLE/CSC (CSV):
    data/calibration/spin_downs_apr29.csv
    data/calibration/spin_downs_apr30.csv
    Bounds are in the rebased CSC clock (= crank_event_time_s + rebase,
    where rebase = first row's timestamp_s − crank_event_time_s).

Video (per-frame mod-π angle, with BLE log for R + segment bounds):
    Log 2026-04-30 19_37_28.txt + crank_video.csv             (LAG = -39.6)
    second crank video/Log 2026-05-01 10_15_36.txt + .csv     (LAG = +13.5)
    Bounds are in the video clock (t_video_s).

Output
------
    data/calibration/all_spindowns.csv   columns: id, source, R, occ, method, t_s, omega_rad_s
    data/calibration/all_spindowns.png   one panel per spindown

Per-rev ω
---------
ω is measured as exactly one full crank revolution (Δθ = 2π) in both
BLE and video sources. The gravity-pendulum once-per-rev oscillation
cancels by construction, so the two sources are directly comparable.
For short high-R video spindowns where fewer than two 2π crossings fit
in the user's window, fall back to the smoothed windowed ω trace
(method="windowed").
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_nrf_log import parse_log  # noqa: E402  (re-exported for curate)
from spindown_fit_video import (integrate_to_cumulative,  # noqa: E402
                                load_video_modpi)
from extract_spindowns_from_video import (  # noqa: E402
    OMEGA_WINDOW_S, SMOOTH_S, windowed_omega, edge_safe_mean)

ROOT = Path(__file__).resolve().parent.parent
BOUNDS_JSON = ROOT / "data/calibration/spindown_bounds.json"
OUT_CSV = ROOT / "data/calibration/all_spindowns.csv"
OUT_PNG = ROOT / "data/calibration/all_spindowns.png"

BLE_SOURCES = [
    ("ble_apr29", ROOT / "data/calibration/spin_downs_apr29.csv"),
    ("ble_apr30", ROOT / "data/calibration/spin_downs_apr30.csv"),
]

VIDEO_SOURCES = [
    {
        "name": "video_1",
        "log":  ROOT / "data/calibration/Log 2026-04-30 19_37_28.txt",
        "csv":  ROOT / "data/calibration/crank_video.csv",
        "lag":  -39.6,
    },
    {
        "name": "video_2",
        "log":  ROOT / "data/calibration/second crank video/Log 2026-05-01 10_15_36.txt",
        "csv":  ROOT / "data/calibration/second crank video/crank_video.csv",
        "lag":  +13.5,
    },
]


def _ble_R_lookup(log: Path) -> tuple[np.ndarray, np.ndarray]:
    """(t_log, R) arrays from a BLE log for time-based R lookup. Used by
    curate_spindowns.py and by relabel_video_R below."""
    rows = parse_log(log)
    t: list[float] = []
    R: list[int] = []
    for r in rows:
        ts = r.get("timestamp_s")
        res = r.get("resistance")
        if ts is None or res is None:
            continue
        try:
            t.append(float(ts)); R.append(int(res))
        except (TypeError, ValueError):
            continue
    return np.asarray(t), np.asarray(R, dtype=int)


def relabel_video_R(log_t: np.ndarray, log_R: np.ndarray,
                    t_in_wall: float, t_out_wall: float) -> int:
    """Robust R label for a video spindown: median R in the wall-clock
    decay window [t_in+lag, t_out+lag]. Falls back to the most recent R
    before t_in_wall if no record falls inside the window — handles the
    failure mode where curate's peak-time lookup grabbed an R from the
    rider's pre-stop warmup rather than the actual decay phase."""
    if len(log_t) == 0:
        return -1
    mask = (log_t >= t_in_wall) & (log_t <= t_out_wall)
    if mask.sum() >= 1:
        return int(np.median(log_R[mask]))
    j = int(np.searchsorted(log_t, t_in_wall)) - 1
    j = max(0, min(len(log_t) - 1, j))
    return int(log_R[j])


# ---------------------------------------------------------------------------
# Per-revolution ω from cumulative angle.
# ---------------------------------------------------------------------------

def per_rev_omega(t: np.ndarray, cum: np.ndarray
                  ) -> tuple[np.ndarray, np.ndarray]:
    """Return (t_mid, ω) for every successive 2π advance in cum.

    Each ω is an exact one-revolution average (2π / Δt between two
    times where cum advances by 2π), so the once-per-rev gravity-pendulum
    oscillation cancels by construction. Linear interpolation on cum gives
    sub-frame timing of the crossings.
    """
    if len(t) < 2:
        return np.array([]), np.array([])
    cum = np.asarray(cum, dtype=float)
    t = np.asarray(t, dtype=float)
    if cum[-1] < cum[0]:
        cum = -cum
    if cum[-1] - cum[0] < 2 * math.pi:
        return np.array([]), np.array([])
    target = cum[0] + 2 * math.pi
    cross_t: list[float] = []
    n = len(cum)
    for i in range(1, n):
        while cum[i] >= target:
            denom = cum[i] - cum[i - 1]
            if denom > 1e-12:
                frac = (target - cum[i - 1]) / denom
                cross_t.append(float(t[i - 1] + frac * (t[i] - t[i - 1])))
            else:
                cross_t.append(float(t[i]))
            target += 2 * math.pi
    if len(cross_t) < 2:
        return np.array([]), np.array([])
    cross = np.asarray(cross_t)
    dt = np.diff(cross)
    valid = dt > 1e-6
    omega = (2 * math.pi) / dt[valid]
    t_mid = 0.5 * (cross[:-1] + cross[1:])[valid]
    return t_mid, omega


# ---------------------------------------------------------------------------
# Load each source once into a (t, ω-per-rev) or (t_v, cum_v, abs_om) view.
# ---------------------------------------------------------------------------

def _ble_rebase(rows: list[dict]) -> float | None:
    """The constant offset that maps crank_event_time_s into wall clock."""
    for r in rows:
        ts = r.get("timestamp_s"); evt = r.get("crank_event_time_s")
        if ts in (None, "") or evt in (None, ""):
            continue
        try:
            return float(ts) - float(evt)
        except (TypeError, ValueError):
            continue
    return None


def load_ble(path: Path) -> tuple[np.ndarray, np.ndarray]:
    """Per-rev ω over the entire BLE log, in the rebased CSC clock that
    matches `timestamp_s`. The midpoint time of each pair is what gets
    compared against the user's [t_in, t_out] bounds."""
    rows = list(csv.DictReader(open(path)))
    rebase = _ble_rebase(rows)
    if rebase is None:
        return np.array([]), np.array([])
    obs = []
    last_n = last_evt = None
    for r in rows:
        nv = r.get("crank_revs"); tv = r.get("crank_event_time_s")
        if nv in (None, "") or tv in (None, ""):
            continue
        try:
            n = int(nv); evt = float(tv)
        except (ValueError, TypeError):
            continue
        evt_wall = evt + rebase
        if last_n is not None and (n <= last_n or evt_wall <= last_evt + 1e-6):
            continue
        if last_n is not None:
            d_n = n - last_n
            dt = evt_wall - last_evt
            if dt > 0:
                obs.append((0.5 * (evt_wall + last_evt),
                            2.0 * math.pi * d_n / dt))
        last_n, last_evt = n, evt_wall
    if not obs:
        return np.array([]), np.array([])
    t = np.asarray([o[0] for o in obs], dtype=float)
    om = np.asarray([o[1] for o in obs], dtype=float)
    return t, om


def load_video(csv_path: Path) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return (t_v, cum_v, abs_om) for the full video.

    cum_v is the cumulative angle from the mod-π integration (more wrap-
    robust than per-frame unwrap). abs_om is the smoothed windowed ω
    trace, used as a fallback for very short high-R spindowns where
    per-rev gives <1 sample.
    """
    rows = list(csv.DictReader(csv_path.open()))
    t_v = np.array([float(r["t_video_s"]) for r in rows])
    ang_unw = np.array([float(r["angle_unwrapped_rad"])
                        if r["angle_unwrapped_rad"] else np.nan
                        for r in rows])
    nan = np.isnan(ang_unw)
    if nan.any():
        good = np.where(~nan)[0]
        ang_unw = np.interp(np.arange(len(ang_unw)), good, ang_unw[good])

    _, ang_modpi = load_video_modpi(csv_path)
    cum_v = integrate_to_cumulative(ang_modpi)
    if len(cum_v) != len(t_v):
        cum_v = ang_unw.copy()

    omega = windowed_omega(t_v, ang_unw, OMEGA_WINDOW_S)
    abs_om_raw = np.abs(omega)
    med_dt = float(np.median(np.diff(t_v)))
    k_smooth = max(1, int(round(SMOOTH_S / med_dt)))
    abs_om = edge_safe_mean(abs_om_raw, k_smooth)
    return t_v, cum_v, abs_om


# ---------------------------------------------------------------------------
# Slice + extract per-rev ω over a curated window.
# ---------------------------------------------------------------------------

def extract_ble(t_full: np.ndarray, om_full: np.ndarray,
                t_in: float, t_out: float
                ) -> tuple[np.ndarray, np.ndarray]:
    """BLE per-rev arrays are already in the rebased CSC clock that matches
    user-set bounds, so just mask and rebase t to start at 0."""
    mask = (t_full >= t_in) & (t_full <= t_out)
    if mask.sum() < 2:
        return np.array([]), np.array([])
    t = t_full[mask]
    om = om_full[mask]
    return t - t[0], om


def extract_video(t_v: np.ndarray, cum_v: np.ndarray, abs_om: np.ndarray,
                  t_in: float, t_out: float
                  ) -> tuple[np.ndarray, np.ndarray, str]:
    """Per-rev ω over [t_in, t_out] from the cumulative-angle integration.
    Falls back to windowed ω (smoothed |ω|) when the window contains <2
    full revs — that's the typical situation at R≥85, dur ≈ 2–3 s.
    Returns (t_rel, omega, method)."""
    mask = (t_v >= t_in) & (t_v <= t_out)
    if mask.sum() < 2:
        return np.array([]), np.array([]), "empty"
    tt = t_v[mask]
    cum = cum_v[mask]
    t_rev, om_rev = per_rev_omega(tt, cum)
    if len(t_rev) >= 2:
        return t_rev - t_rev[0], om_rev, "per_rev"
    # Fallback: windowed ω over the same mask. Pendulum wobble is
    # smaller than the fast decay at high R so it's still readable.
    return tt - tt[0], abs_om[mask], "windowed"


# ---------------------------------------------------------------------------
# Output.
# ---------------------------------------------------------------------------

def write_csv(spindowns: list[dict], path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "source", "R", "occ", "method",
                    "t_s", "omega_rad_s"])
        for s in spindowns:
            for ti, wi in zip(s["t"], s["omega"]):
                w.writerow([s.get("id", ""), s["source"], s["R"], s["occ"],
                            s["method"], f"{ti:.6f}", f"{wi:.6f}"])
    print(f"wrote {path} ({sum(len(s['t']) for s in spindowns)} rows, "
          f"{len(spindowns)} spindowns)")


SRC_COLOR = {
    "ble_apr29": "#1f77b4",
    "ble_apr30": "#17becf",
    "video_1":   "#ff7f0e",
    "video_2":   "#d62728",
}


def plot_grid(spindowns: list[dict], path: Path):
    spindowns = sorted(spindowns, key=lambda s: (s["R"], s["source"], s["occ"]))
    n = len(spindowns)
    cols = 6
    rows_ = (n + cols - 1) // cols
    fig, axes = plt.subplots(rows_, cols,
                             figsize=(2.7 * cols, 2.0 * rows_),
                             sharex=False, sharey=False)
    axes = np.atleast_2d(axes)
    for i, s in enumerate(spindowns):
        ax = axes[i // cols, i % cols]
        rpm = s["omega"] * 60.0 / (2 * math.pi)
        ls = "-" if s["method"] == "per_rev" else "--"
        ax.plot(s["t"], rpm, ls,
                color=SRC_COLOR.get(s["source"], "k"), lw=1.0,
                marker="o", ms=2.5)
        ax.set_title(f"#{s['id']}  R={s['R']} occ={s['occ']}  "
                     f"{s['source']}  n={len(s['t'])}  ({s['method']})",
                     fontsize=8)
        ax.set_xlabel("t (s)", fontsize=7)
        ax.set_ylabel("ω (rpm)", fontsize=7)
        ax.tick_params(labelsize=7)
        ax.grid(alpha=0.3)
        ax.set_ylim(bottom=0)
    for k in range(n, rows_ * cols):
        axes[k // cols, k % cols].set_visible(False)
    for src, color in SRC_COLOR.items():
        axes[0, 0].plot([], [], color=color, label=src, marker="o", ms=4)
    axes[0, 0].legend(fontsize=7, loc="upper right")

    fig.suptitle("All spindowns (curated) — per-rev ω(t)",
                 fontsize=12, weight="bold")
    fig.tight_layout(rect=[0, 0, 1, 0.985])
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close()
    print(f"wrote {path}")


# ---------------------------------------------------------------------------
# Main.
# ---------------------------------------------------------------------------

def main():
    if not BOUNDS_JSON.exists():
        sys.exit(f"missing {BOUNDS_JSON} — run curate_spindowns.py first")
    bounds = json.loads(BOUNDS_JSON.read_text())["spindowns"]
    print(f"loaded {len(bounds)} curated entries from {BOUNDS_JSON}")

    # Cache per-source data so we only parse each file once.
    ble_cache: dict[str, tuple[np.ndarray, np.ndarray]] = {}
    for name, path in BLE_SOURCES:
        ble_cache[name] = load_ble(path)
        print(f"  {name}: {len(ble_cache[name][0])} per-rev samples loaded")
    video_cache: dict[str, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
    video_R_lookup: dict[str, tuple[np.ndarray, np.ndarray, float]] = {}
    for v in VIDEO_SOURCES:
        video_cache[v["name"]] = load_video(v["csv"])
        log_t, log_R = _ble_R_lookup(v["log"])
        video_R_lookup[v["name"]] = (log_t, log_R, float(v["lag"]))
        print(f"  {v['name']}: {len(video_cache[v['name']][0])} frames, "
              f"{len(log_t)} R records loaded")

    spindowns: list[dict] = []
    skipped: list[tuple[str, int, str]] = []
    relabels: list[tuple[str, int, int, int]] = []  # (src, cid, R_json, R_fixed)
    for ent in bounds:
        if not ent.get("keep", True):
            continue
        src = ent["source"]
        R = int(ent["R"]); occ = int(ent["occ"])
        t_in, t_out = float(ent["t_in"]), float(ent["t_out"])

        if src in ble_cache:
            t_full, om_full = ble_cache[src]
            t, om = extract_ble(t_full, om_full, t_in, t_out)
            method = "per_rev"
            if len(t) < 2:
                skipped.append((src, ent["candidate_id"], "<2 BLE samples"))
                continue
        elif src in video_cache:
            t_v, cum_v, abs_om = video_cache[src]
            t, om, method = extract_video(t_v, cum_v, abs_om, t_in, t_out)
            if len(t) < 2:
                skipped.append((src, ent["candidate_id"], "<2 video samples"))
                continue
            # Re-derive R from the BLE log over the actual decay window.
            log_t, log_R, lag = video_R_lookup[src]
            R_fixed = relabel_video_R(log_t, log_R,
                                      t_in + lag, t_out + lag)
            if R_fixed != R:
                relabels.append((src, int(ent["candidate_id"]), R, R_fixed))
                R = R_fixed
        else:
            skipped.append((src, ent.get("candidate_id", -1), "unknown source"))
            continue

        spindowns.append({
            "source": src, "R": R, "occ": occ,
            "method": method, "t": t, "omega": om,
            "candidate_id": ent.get("candidate_id"),
        })

    # Re-number occurrences by (R, source) order so labels are sane after
    # relabeling — the JSON's `occ` was pre-relabel.
    by_src_R: dict[tuple[str, int], int] = {}
    spindowns.sort(key=lambda s: (s["source"], s.get("candidate_id", 0)))
    for s in spindowns:
        key = (s["source"], s["R"])
        s["occ"] = by_src_R.get(key, 0)
        by_src_R[key] = s["occ"] + 1

    if relabels:
        print(f"\nrelabeled {len(relabels)} video R values from BLE log:")
        for src, cid, R0, R1 in relabels:
            print(f"  {src} cid={cid}:  R {R0} → {R1}")

    # Stable IDs in (R, source, occ) sort order.
    spindowns = sorted(spindowns, key=lambda s: (s["R"], s["source"], s["occ"]))
    for i, s in enumerate(spindowns, 1):
        s["id"] = i

    # Summary.
    print(f"\ntotal: {len(spindowns)} curated spindowns "
          f"({len(skipped)} skipped)")
    if skipped:
        for src, cid, why in skipped:
            print(f"  skipped {src} cid={cid}: {why}")
    print(f"  {'#':>3} {'R':>3} {'occ':>3} {'src':>10} {'n':>4} "
          f"{'dur(s)':>7} {'ω0':>6} {'ω_end':>6} {'method':>8}")
    for s in spindowns:
        dur = float(s["t"][-1] - s["t"][0])
        print(f"  {s['id']:>3} {s['R']:>3} {s['occ']:>3} {s['source']:>10} "
              f"{len(s['t']):>4} {dur:>7.2f} "
              f"{s['omega'][0]:>6.2f} {s['omega'][-1]:>6.2f} "
              f"{s['method']:>8}")

    write_csv(spindowns, OUT_CSV)
    plot_grid(spindowns, OUT_PNG)


if __name__ == "__main__":
    main()
