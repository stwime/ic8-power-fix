"""Compare HR -> power relationship between real (4iiii) outdoor rides and
IC8-broadcast indoor rides. Outputs binned medians and an inflation estimate.

Filters: cadence >= 50 (actively pedaling), power >= 10W (not coasting).
"""
import sys
from pathlib import Path
from collections import defaultdict
import numpy as np
import fitdecode

OUTDOOR = [
    "data/Lunch_Ride.fit",
    "data/Lunch_Ride-2.fit",
    "data/Lunch_Ride_still_too_much_snow.fit",
]
INDOOR_IC8 = [
    "data/ROUVY_Güímar_Tenerife.fit",
    "data/ROUVY_IRONMAN_70_3_Sunshine_Coast_1st_loop_.fit",
]

ROOT = Path(__file__).parent.parent


def load_records(path: Path):
    rows = []
    with fitdecode.FitReader(str(path)) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            if frame.name != "record":
                continue
            d = {f.name: f.value for f in frame.fields}
            rows.append((d.get("heart_rate"), d.get("power"),
                         d.get("cadence")))
    return rows


def collect(paths, label):
    points = []  # (hr, power)
    for p in paths:
        recs = load_records(ROOT / p)
        for hr, pw, cd in recs:
            if hr is None or pw is None or cd is None:
                continue
            if cd < 50 or pw < 10:
                continue
            points.append((hr, pw))
    points = np.array(points, dtype=float)
    print(f"{label}: {len(points)} actively-pedaling samples")
    return points


def hr_binned_median(points, label, bins):
    print(f"\n{label} HR -> power (median, n)")
    print(f"  {'HR bin':>10} {'n':>5} {'P25':>5} {'P50':>5} {'P75':>5}")
    out = {}
    for lo, hi in bins:
        m = (points[:, 0] >= lo) & (points[:, 0] < hi)
        if m.sum() < 20:
            continue
        p = points[m, 1]
        med = np.median(p)
        q1, q3 = np.percentile(p, [25, 75])
        out[(lo, hi)] = (m.sum(), med, q1, q3)
        print(f"  [{lo:>3},{hi:>3}) {m.sum():>5} "
              f"{q1:>5.0f} {med:>5.0f} {q3:>5.0f}")
    return out


def main():
    out = collect([Path(p) for p in OUTDOOR], "outdoor (4iiii)")
    ic8 = collect([Path(p) for p in INDOOR_IC8], "indoor (IC8)")

    bins = [(100, 110), (110, 120), (120, 130), (130, 140),
            (140, 150), (150, 160), (160, 170), (170, 180)]
    out_b = hr_binned_median(out, "OUTDOOR (4iiii)", bins)
    ic8_b = hr_binned_median(ic8, "INDOOR (IC8)", bins)

    print(f"\n=== inflation factor (IC8 median / outdoor median) ===")
    print(f"  {'HR bin':>10} {'outdoor':>8} {'IC8':>5} {'ratio':>6}")
    ratios = []
    for k in sorted(set(out_b) & set(ic8_b)):
        _, om, _, _ = out_b[k]
        _, im, _, _ = ic8_b[k]
        r = im / om
        ratios.append((k, r))
        print(f"  [{k[0]:>3},{k[1]:>3}) {om:>8.0f} {im:>5.0f} {r:>6.2f}")

    if ratios:
        avg = np.mean([r for _, r in ratios])
        print(f"\n  mean ratio across overlapping HR bins: {avg:.2f}")
        print(f"  (so IC8 broadcasts ~{(avg-1)*100:.0f}% high relative to outdoor "
              f"4iiii at matched HR)")


if __name__ == "__main__":
    main()
