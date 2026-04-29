"""Find sustained hard efforts in all outdoor rides, tag by time-into-ride
to expose the fatigue confound, and compare to IC8 indoor at matched
HR + cadence.

A 'hard effort' = a window where rolling 2-min mean power exceeds a
threshold AND the segment lasts ≥120s.
"""
from pathlib import Path
import numpy as np
import fitdecode

ROOT = Path(__file__).parent.parent

OUT_PATHS = ["data/Lunch_Ride.fit", "data/Lunch_Ride-2.fit",
             "data/Lunch_Ride_still_too_much_snow.fit"]
IND_PATHS = ["data/ROUVY_Güímar_Tenerife.fit",
             "data/ROUVY_IRONMAN_70_3_Sunshine_Coast_1st_loop_.fit"]


def load_records(path):
    rows = []
    with fitdecode.FitReader(str(path)) as fit:
        for f in fit:
            if not isinstance(f, fitdecode.FitDataMessage) or f.name != "record":
                continue
            d = {x.name: x.value for x in f.fields}
            rows.append({"hr": d.get("heart_rate"), "pw": d.get("power"),
                         "cd": d.get("cadence")})
    return rows


def find_hard_segments(rows, min_avg_pw=180, win=120, min_dur=120):
    """Indices where rolling-{win}s mean power >= min_avg_pw."""
    pw = np.array([r["pw"] if r["pw"] is not None else 0 for r in rows],
                  dtype=float)
    n = len(pw)
    rolling = np.zeros(n)
    for i in range(n):
        lo = max(0, i - win // 2)
        hi = min(n, i + win // 2)
        rolling[i] = pw[lo:hi].mean()
    mask = rolling >= min_avg_pw

    # Connected segments
    segments = []
    s = None
    for i, m in enumerate(mask):
        if m and s is None:
            s = i
        elif not m and s is not None:
            if i - s >= min_dur:
                segments.append((s, i))
            s = None
    if s is not None and n - s >= min_dur:
        segments.append((s, n))
    return segments


def collect_pts(rows, segments, total_len, file_label):
    """For each segment, record samples + time-tier (early/mid/late)."""
    pts = []  # (hr, cd, pw, tier)
    for s, e in segments:
        # tier based on segment midpoint
        mid = (s + e) / 2
        frac = mid / total_len
        tier = "early" if frac < 0.33 else "mid" if frac < 0.67 else "late"
        for r in rows[s:e]:
            if (r["hr"] and r["pw"] is not None and r["cd"]
                    and r["pw"] >= 10 and r["cd"] >= 30):
                pts.append((r["hr"], r["cd"], r["pw"], tier))
    return pts


def summarize(label, segs, rows, total_len):
    print(f"\n--- {label}: {len(segs)} hard segments ({len(rows)} records) ---")
    for s, e in segs:
        seg = rows[s:e]
        hrs = [r["hr"] for r in seg if r["hr"]]
        pws = [r["pw"] for r in seg if r["pw"] is not None]
        cds = [r["cd"] for r in seg if r["cd"]]
        if not pws:
            continue
        frac = (s + e) / 2 / total_len
        tier = "EARLY" if frac < 0.33 else "MID  " if frac < 0.67 else "LATE "
        print(f"  [{tier}] {s:>5}-{e:>5} ({e-s:>3}s, "
              f"{100*frac:.0f}% into ride): "
              f"HR med={np.median(hrs) if hrs else 0:.0f} "
              f"P med={np.median(pws):.0f}W "
              f"cad med={np.median(cds) if cds else 0:.0f}rpm")


def main():
    all_pts = []
    for path in OUT_PATHS:
        rows = load_records(ROOT / path)
        segs = find_hard_segments(rows, min_avg_pw=180, win=120, min_dur=120)
        summarize(Path(path).name, segs, rows, len(rows))
        all_pts += collect_pts(rows, segs, len(rows), Path(path).stem)

    if not all_pts:
        print("\nno hard segments found")
        return

    arr_all = np.array([(p[0], p[1], p[2]) for p in all_pts], dtype=float)
    tiers = np.array([p[3] for p in all_pts])
    print(f"\ntotal hard-effort samples: {len(arr_all)}")
    for t in ("early", "mid", "late"):
        m = tiers == t
        if m.sum() == 0:
            continue
        sub = arr_all[m]
        print(f"  {t:>5}: n={m.sum():>4} HR med={np.median(sub[:,0]):.0f} "
              f"cad med={np.median(sub[:,1]):.0f} P med={np.median(sub[:,2]):.0f}W")

    # Fatigue check: same HR bin, compare early vs late power
    print(f"\n--- fatigue check: power at matched HR, early vs late ---")
    print(f"  {'HR bin':>10} {'early':>10} {'mid':>10} {'late':>10}")
    for lo, hi in [(140, 150), (150, 160), (160, 170), (170, 180)]:
        line = f"  [{lo:>3},{hi:>3})"
        m_hr = (arr_all[:, 0] >= lo) & (arr_all[:, 0] < hi)
        for t in ("early", "mid", "late"):
            m = m_hr & (tiers == t)
            if m.sum() < 20:
                line += f" {'-':>10}"
            else:
                line += f" {np.median(arr_all[m,2]):>4.0f}W (n={m.sum():>3})"
        print(line)

    # Compare hard-effort outdoor (early+mid only — drop late) vs IC8
    print(f"\n--- compare hard outdoor (early+mid) vs IC8 by cadence ---")
    keep = (tiers == "early") | (tiers == "mid")
    out_pts = arr_all[keep]
    print(f"  outdoor hard pool (early+mid): n={len(out_pts)}")

    ic8 = []
    for path in IND_PATHS:
        rs = load_records(ROOT / path)
        for r in rs:
            if (r["hr"] and r["pw"] is not None and r["cd"]
                    and r["pw"] >= 10 and r["cd"] >= 30):
                ic8.append((r["hr"], r["cd"], r["pw"]))
    ic8 = np.array(ic8, dtype=float)

    print(f"  {'cad':>10} {'n_out':>5} {'med_out':>7} {'HR_band':>10} "
          f"{'n_in':>5} {'med_in':>6} {'ratio':>6}")
    for c_lo, c_hi in [(50, 60), (60, 70), (70, 80), (80, 90), (90, 100)]:
        m = (out_pts[:, 1] >= c_lo) & (out_pts[:, 1] < c_hi)
        if m.sum() < 20:
            continue
        out_p = out_pts[m, 2]
        # use IQR HR window from outdoor sample
        hr_lo = np.percentile(out_pts[m, 0], 25)
        hr_hi = np.percentile(out_pts[m, 0], 75)
        m_in = ((ic8[:, 1] >= c_lo) & (ic8[:, 1] < c_hi)
                & (ic8[:, 0] >= hr_lo) & (ic8[:, 0] <= hr_hi))
        if m_in.sum() < 10:
            continue
        in_p = ic8[m_in, 2]
        print(f"  [{c_lo:>2},{c_hi:>2})  {m.sum():>5} {np.median(out_p):>7.0f} "
              f"{hr_lo:>3.0f}-{hr_hi:<3.0f}  {m_in.sum():>5} "
              f"{np.median(in_p):>6.0f} {np.median(in_p)/np.median(out_p):>6.2f}")


if __name__ == "__main__":
    main()
