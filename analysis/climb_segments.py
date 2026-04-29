"""Extract climb segments from outdoor FIT files where the rider is
genuinely working hard: sustained positive grade AND HR in upper range.
Use these as a cleaner ground-truth pool to compare against IC8 indoor.
"""
from pathlib import Path
import numpy as np
import fitdecode

ROOT = Path(__file__).parent.parent


def load_records(path: Path):
    rows = []
    with fitdecode.FitReader(str(path)) as fit:
        for f in fit:
            if not isinstance(f, fitdecode.FitDataMessage) or f.name != "record":
                continue
            d = {x.name: x.value for x in f.fields}
            rows.append({
                "t": d.get("timestamp"),
                "hr": d.get("heart_rate"),
                "pw": d.get("power"),
                "cd": d.get("cadence"),
                "alt": d.get("enhanced_altitude") or d.get("altitude"),
                "spd": d.get("enhanced_speed") or d.get("speed"),
                "dist": d.get("distance"),
            })
    return rows


def extract_climbs(rows, min_grade_pct=3.0, min_duration_s=30,
                   hr_percentile=70):
    """Find segments with sustained positive grade and high HR."""
    # Smooth altitude over 10s to denoise
    alt = np.array([r["alt"] if r["alt"] is not None else np.nan
                    for r in rows], dtype=float)
    dist = np.array([r["dist"] if r["dist"] is not None else np.nan
                     for r in rows], dtype=float)
    hr = np.array([r["hr"] if r["hr"] is not None else 0
                   for r in rows], dtype=float)

    # Compute grade over 20m sliding window (more stable than per-second)
    grade = np.full(len(rows), np.nan)
    for i in range(len(rows)):
        # find the sample ~30 seconds back
        for back in range(20, 60):
            j = max(0, i - back)
            dd = dist[i] - dist[j]
            da = alt[i] - alt[j]
            if dd is not None and dd > 50:  # need ≥50m horizontal
                grade[i] = 100 * da / dd
                break

    hr_threshold = np.nanpercentile(hr[hr > 60], hr_percentile)
    print(f"  HR threshold ({hr_percentile}th pctile): {hr_threshold:.0f} bpm")

    # Segment: contiguous indices where grade ≥ threshold and HR ≥ threshold
    in_climb = (grade >= min_grade_pct) & (hr >= hr_threshold)
    segments = []
    start = None
    for i, ic in enumerate(in_climb):
        if ic and start is None:
            start = i
        elif not ic and start is not None:
            if i - start >= min_duration_s:
                segments.append((start, i))
            start = None
    if start is not None and len(rows) - start >= min_duration_s:
        segments.append((start, len(rows)))
    return segments, grade, hr_threshold


def report_climb(rows, segments, label):
    print(f"\n=== {label} ===")
    print(f"  total records: {len(rows)}")
    print(f"  climb segments found: {len(segments)}")

    all_pts = []  # (hr, cad, pw)
    for s, e in segments:
        seg = rows[s:e]
        if not seg:
            continue
        hrs = [r["hr"] for r in seg if r["hr"]]
        pws = [r["pw"] for r in seg if r["pw"] is not None and r["pw"] >= 10]
        cds = [r["cd"] for r in seg if r["cd"] and r["cd"] >= 30]
        if not hrs or not pws:
            continue
        for r in seg:
            if (r["hr"] and r["pw"] is not None and r["cd"]
                    and r["pw"] >= 10 and r["cd"] >= 30):
                all_pts.append((r["hr"], r["cd"], r["pw"]))
        print(f"  seg {s:>5}-{e:>5} ({e-s:>3}s): HR med={np.median(hrs):.0f}, "
              f"P med={np.median(pws):.0f}W, cad med={np.median(cds):.0f}rpm")

    if all_pts:
        arr = np.array(all_pts, dtype=float)
        print(f"\n  all climb samples: n={len(arr)}")
        print(f"  HR  : med={np.median(arr[:,0]):.0f} 25-75% "
              f"{np.percentile(arr[:,0],25):.0f}-{np.percentile(arr[:,0],75):.0f}")
        print(f"  cad : med={np.median(arr[:,1]):.0f} 25-75% "
              f"{np.percentile(arr[:,1],25):.0f}-{np.percentile(arr[:,1],75):.0f}")
        print(f"  P   : med={np.median(arr[:,2]):.0f} 25-75% "
              f"{np.percentile(arr[:,2],25):.0f}-{np.percentile(arr[:,2],75):.0f}")
        return arr
    return None


def compare_with_ic8(climb_pts, label):
    """Pull IC8 indoor data at matched HR+cadence."""
    print(f"\n=== compare {label} climb to IC8 at matched HR+cad ===")
    ic8 = []
    for ride in ["data/ROUVY_Güímar_Tenerife.fit",
                 "data/ROUVY_IRONMAN_70_3_Sunshine_Coast_1st_loop_.fit"]:
        rows = load_records(ROOT / ride)
        for r in rows:
            if r["hr"] and r["pw"] is not None and r["cd"]:
                if r["pw"] >= 10 and r["cd"] >= 30:
                    ic8.append((r["hr"], r["cd"], r["pw"]))
    ic8 = np.array(ic8, dtype=float)

    print(f"  cad bin   |  outdoor-climb (4iiii)  |    indoor (IC8)    | ratio")
    for c_lo, c_hi in [(50, 60), (60, 70), (70, 80), (80, 90), (90, 100)]:
        # match HR window from climb data
        out_m = (climb_pts[:, 1] >= c_lo) & (climb_pts[:, 1] < c_hi)
        if out_m.sum() < 15:
            continue
        out_p = climb_pts[out_m, 2]
        out_hr_lo = np.percentile(climb_pts[out_m, 0], 25)
        out_hr_hi = np.percentile(climb_pts[out_m, 0], 75)
        # Use matching HR window for indoor
        in_m = ((ic8[:, 1] >= c_lo) & (ic8[:, 1] < c_hi)
                & (ic8[:, 0] >= out_hr_lo) & (ic8[:, 0] <= out_hr_hi))
        if in_m.sum() < 15:
            continue
        in_p = ic8[in_m, 2]
        ratio = np.median(in_p) / np.median(out_p)
        print(f"  cad [{c_lo:>2},{c_hi:>2})  | n={out_m.sum():>3} HR{out_hr_lo:.0f}-{out_hr_hi:.0f} "
              f"med={np.median(out_p):>5.0f}W  | n={in_m.sum():>3} med={np.median(in_p):>5.0f}W "
              f"| {ratio:.2f}")


if __name__ == "__main__":
    snow = load_records(ROOT / "data/Lunch_Ride_still_too_much_snow.fit")
    segs, grades, hr_thr = extract_climbs(snow,
                                          min_grade_pct=3.0,
                                          min_duration_s=30,
                                          hr_percentile=70)
    pts = report_climb(snow, segs, "snow ride climbs (≥3% grade, ≥30s, top-30% HR)")
    if pts is not None:
        compare_with_ic8(pts, "snow ride")
