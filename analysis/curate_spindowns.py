"""Interactive curation: set fit-window in/out (or drop) for every spindown.

The auto-detector throws candidates away on heuristics (rebound, duration,
peak threshold). This tool generates candidates *permissively* — every
active run with a peak above ~30 rpm, every BLE coastdown of length ≥ 2
samples — and presents them one at a time with a draggable in/out span.
You decide what to keep.

Output: data/calibration/spindown_bounds.json
    {
      "spindowns": [
        {"source": "video_2", "candidate_id": 7,
         "R": 22, "occ": 0,
         "keep": true, "t_in": 282.40, "t_out": 294.48,
         "notes": "..."}
      ]
    }
aggregate_spindowns.py reads this file and uses these hand-set bounds
instead of auto-detection.

Controls (focus must be on the matplotlib window):
    drag           : set in/out span
    SPACE / →      : next candidate (save current span)
    ←              : previous candidate
    K              : mark KEEP
    D              : mark DROP
    R              : reset to default in/out
    S              : save and exit
    Q              : quit without saving
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.widgets import SpanSelector

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_nrf_log import parse_log  # noqa: E402
from spindown_fit import find_clean_coastdowns, _crank_rev_obs  # noqa: E402
from spindown_fit_video import integrate_to_cumulative, load_video_modpi  # noqa: E402  # noqa: F401
from extract_spindowns_from_video import (  # noqa: E402
    OMEGA_WINDOW_S, SMOOTH_S, FLOOR,
    windowed_omega, edge_safe_mean, find_active_runs,
    detect_floor as vid_detect_floor)
from aggregate_spindowns import BLE_SOURCES, VIDEO_SOURCES, _ble_R_lookup  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
BOUNDS_JSON = ROOT / "data/calibration/spindown_bounds.json"

# Permissive: catch what the strict detector drops, then let user decide.
MIN_PEAK_PERMISSIVE = 3.0   # rad/s ≈ 30 rpm  (vs 5.0 in extract_*)
BLE_MIN_CAD_START = 50.0    # rpm (vs 70)
BLE_MIN_SAMPLES = 2         # (vs 4)

# How much context (in source seconds) to show around each candidate so you
# can see the lead-up and the runout, not just the auto-detected window.
VIEW_PAD_BEFORE_S = 8.0
VIEW_PAD_AFTER_S = 8.0


# ---------------------------------------------------------------------------
# Candidate generators.
# ---------------------------------------------------------------------------

def _ble_rebase(rows: list[dict]) -> float | None:
    """The constant offset that maps crank_event_time_s into wall clock.
    Picked from the first row where both timestamp_s and crank_event_time_s
    are present: rebase = timestamp_s - crank_event_time_s. Once set it's
    fixed for the whole log."""
    for r in rows:
        ts = r.get("timestamp_s"); evt = r.get("crank_event_time_s")
        if ts in (None, "") or evt in (None, ""):
            continue
        try:
            return float(ts) - float(evt)
        except (TypeError, ValueError):
            continue
    return None


def _full_ble_per_rev(rows: list[dict], rebase: float
                      ) -> tuple[np.ndarray, np.ndarray]:
    """Per-rev ω over the *entire* BLE row list (not just one segment).

    Used to give each candidate's display window some context — lead-up
    and runout — beyond the auto-detected segment bounds. Times are
    crank_event_time_s + rebase (i.e., wall-clock-compatible).
    """
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


def ble_candidates(name: str, path: Path) -> list[dict]:
    rows = list(csv.DictReader(open(path)))
    rebase = _ble_rebase(rows)
    if rebase is None:
        return []
    full_csc_t, full_csc_om = _full_ble_per_rev(rows, rebase)
    segs = find_clean_coastdowns(
        rows, min_cad_start=BLE_MIN_CAD_START, min_samples=BLE_MIN_SAMPLES)
    out = []
    per_R: dict[int, int] = {}
    for cid, (seg, R, term) in enumerate(segs):
        occ = per_R.get(R, 0); per_R[R] = occ + 1
        obs = _crank_rev_obs(seg)
        if len(obs) < 2:
            continue
        # Segment bounds in the same rebased CSC clock as full_csc_t.
        et = np.array([o[1] for o in obs], dtype=float)
        seg_evt0 = float(et[0]) + rebase
        seg_evt1 = float(et[-1]) + rebase
        # Per-rev midpoints from the segment's own (n, evt) pairs — these
        # are the natural in/out for a per-rev-ω fit window.
        seg_mid = 0.5 * (et[:-1] + et[1:]) + rebase
        view_lo = seg_evt0 - VIEW_PAD_BEFORE_S
        view_hi = seg_evt1 + VIEW_PAD_AFTER_S
        m = (full_csc_t >= view_lo) & (full_csc_t <= view_hi)
        if m.sum() < 2:
            continue
        out.append({
            "source": name, "candidate_id": cid,
            "R": R, "occ": occ, "term": term,
            "t": full_csc_t[m], "omega": full_csc_om[m],
            "t_default_in": float(seg_mid[0]),
            "t_default_out": float(seg_mid[-1]),
        })
    return out


def video_candidates(name: str, log: Path, csv_path: Path,
                     lag: float) -> list[dict]:
    log_t, log_R = _ble_R_lookup(log)
    rows = list(csv.DictReader(csv_path.open()))
    t_v = np.array([float(r["t_video_s"]) for r in rows])
    ang_unw = np.array([float(r["angle_unwrapped_rad"])
                        if r["angle_unwrapped_rad"] else np.nan
                        for r in rows])
    nan = np.isnan(ang_unw)
    if nan.any():
        good = np.where(~nan)[0]
        ang_unw = np.interp(np.arange(len(ang_unw)), good, ang_unw[good])

    omega = windowed_omega(t_v, ang_unw, OMEGA_WINDOW_S)
    abs_om_raw = np.abs(omega)
    med_dt = float(np.median(np.diff(t_v)))
    k_smooth = max(1, int(round(SMOOTH_S / med_dt)))
    abs_om = edge_safe_mean(abs_om_raw, k_smooth)
    runs = find_active_runs(abs_om, t_v, FLOOR, MIN_PEAK_PERMISSIVE)

    out = []
    per_R: dict[int, int] = {}
    for cid, (i_lo, i_hi) in enumerate(runs):
        i_peak = int(np.argmax(abs_om[i_lo:i_hi + 1])) + i_lo
        i_floor = vid_detect_floor(abs_om, t_v, i_peak, FLOOR, sustain_s=0.5)
        if i_floor is None:
            i_floor = i_hi
        i_view_lo = max(0, i_lo - int(round(VIEW_PAD_BEFORE_S / med_dt)))
        i_view_hi = min(len(t_v) - 1,
                        i_floor + int(round(VIEW_PAD_AFTER_S / med_dt)))

        # R from BLE at peak's wall-clock time
        t_log_stop = float(t_v[i_peak]) + lag
        if len(log_t) == 0:
            R = -1
        else:
            j = max(0, min(len(log_t) - 1,
                           int(np.searchsorted(log_t, t_log_stop))))
            R = int(log_R[j])
        occ = per_R.get(R, 0); per_R[R] = occ + 1

        out.append({
            "source": name, "candidate_id": cid,
            "R": R, "occ": occ, "term": "video_run",
            "t": t_v[i_view_lo:i_view_hi + 1],
            "omega": abs_om[i_view_lo:i_view_hi + 1],
            "t_default_in": float(t_v[i_peak]),
            "t_default_out": float(t_v[i_floor]),
        })
    return out


# ---------------------------------------------------------------------------
# JSON state.
# ---------------------------------------------------------------------------

def load_existing(path: Path) -> dict:
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    out = {}
    for entry in data.get("spindowns", []):
        out[(entry["source"], entry["candidate_id"])] = entry
    return out


def save_bounds(state: dict, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Stable order: source, candidate_id
    items = sorted(state.values(),
                   key=lambda e: (e["source"], e["candidate_id"]))
    path.write_text(json.dumps({"spindowns": items}, indent=2))


# ---------------------------------------------------------------------------
# UI.
# ---------------------------------------------------------------------------

def curate(candidates: list[dict], state: dict):
    """One figure, navigate via keys; SpanSelector for in/out."""
    idx = [0]  # mutable for closures

    fig, ax = plt.subplots(figsize=(14, 6))
    plt.subplots_adjust(bottom=0.12)

    def get_entry(c):
        key = (c["source"], c["candidate_id"])
        if key not in state:
            state[key] = {
                "source": c["source"], "candidate_id": c["candidate_id"],
                "R": c["R"], "occ": c["occ"],
                "keep": True,
                "t_in": c["t_default_in"], "t_out": c["t_default_out"],
            }
        return state[key]

    def render():
        ax.clear()
        if idx[0] >= len(candidates):
            ax.text(0.5, 0.5, "Done. Press S to save+exit, Q to quit.",
                    ha='center', va='center',
                    transform=ax.transAxes, fontsize=14)
            fig.canvas.draw_idle()
            return
        c = candidates[idx[0]]
        ent = get_entry(c)
        rpm = c["omega"] * 60.0 / (2 * math.pi)
        ax.plot(c["t"], rpm, "C0-", lw=0.9)
        keep = ent.get("keep", True)
        face = 'C2' if keep else 'C3'
        ax.axvspan(ent["t_in"], ent["t_out"], color=face, alpha=0.18,
                   zorder=-1)
        ax.axvline(ent["t_in"], color='C2', lw=1.5)
        ax.axvline(ent["t_out"], color='C1', lw=1.5)
        keep_tag = "KEEP" if keep else "DROP"
        ax.set_title(
            f"[{idx[0]+1}/{len(candidates)}]  {c['source']}  R={c['R']}  "
            f"occ={c['occ']}  cand_id={c['candidate_id']}  "
            f"in={ent['t_in']:.2f}  out={ent['t_out']:.2f}  "
            f"dur={ent['t_out']-ent['t_in']:.2f}s  [{keep_tag}]\n"
            f"drag=set in/out  SPACE/→ next  ← prev  "
            f"K=keep  D=drop  R=reset  S=save+exit  Q=quit",
            fontsize=10)
        ax.set_xlabel("t (source clock, s)")
        ax.set_ylabel("|ω| (rpm)")
        ax.set_ylim(bottom=0)
        ax.grid(alpha=0.3)
        fig.canvas.draw_idle()

    def on_select(t_lo, t_hi):
        if idx[0] >= len(candidates) or t_hi <= t_lo:
            return
        c = candidates[idx[0]]
        ent = get_entry(c)
        ent["t_in"] = float(t_lo)
        ent["t_out"] = float(t_hi)
        ent["keep"] = True
        render()

    span = SpanSelector(ax, on_select, "horizontal",
                        useblit=True, interactive=True,
                        props=dict(alpha=0.25, facecolor='yellow'))

    def on_key(event):
        if event.key in (' ', 'right'):
            idx[0] = min(idx[0] + 1, len(candidates))
        elif event.key == 'left':
            idx[0] = max(idx[0] - 1, 0)
        elif event.key in ('k', 'K') and idx[0] < len(candidates):
            get_entry(candidates[idx[0]])["keep"] = True
        elif event.key in ('d', 'D') and idx[0] < len(candidates):
            get_entry(candidates[idx[0]])["keep"] = False
        elif event.key in ('r', 'R') and idx[0] < len(candidates):
            c = candidates[idx[0]]
            ent = get_entry(c)
            ent["t_in"] = c["t_default_in"]
            ent["t_out"] = c["t_default_out"]
            ent["keep"] = True
        elif event.key in ('s', 'S'):
            save_bounds(state, BOUNDS_JSON)
            print(f"saved {BOUNDS_JSON} ({len(state)} entries)")
            plt.close(fig)
            return
        elif event.key in ('q', 'Q'):
            print("quit without saving")
            plt.close(fig)
            return
        render()

    fig.canvas.mpl_connect('key_press_event', on_key)
    render()
    plt.show()
    return span  # keep ref alive


def main():
    candidates: list[dict] = []
    for name, path in BLE_SOURCES:
        c = ble_candidates(name, path)
        print(f"{name}: {len(c)} candidates")
        candidates += c
    for v in VIDEO_SOURCES:
        c = video_candidates(v["name"], v["log"], v["csv"], v["lag"])
        print(f"{v['name']}: {len(c)} candidates")
        candidates += c
    print(f"\ntotal: {len(candidates)} candidates")

    state = load_existing(BOUNDS_JSON)
    if state:
        print(f"resuming from {len(state)} previously-saved entries")

    curate(candidates, state)


if __name__ == "__main__":
    main()
