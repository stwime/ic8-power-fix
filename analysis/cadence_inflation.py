"""Within a tight HR window, compare outdoor (4iiii) vs IC8 power as a function
of cadence. If the IC8's inflation is cadence-dependent (as the user's
seat-of-pants suggests), we should see a cadence-trend in the indoor curve
that isn't there in the outdoor curve.
"""
from pathlib import Path
import numpy as np
import fitdecode

ROOT = Path(__file__).parent.parent

OUTDOOR = [
    "data/Lunch_Ride.fit",
    "data/Lunch_Ride-2.fit",
    "data/Lunch_Ride_still_too_much_snow.fit",
]
INDOOR_IC8 = [
    "data/ROUVY_Güímar_Tenerife.fit",
    "data/ROUVY_IRONMAN_70_3_Sunshine_Coast_1st_loop_.fit",
]


def load(paths):
    out = []
    for p in paths:
        with fitdecode.FitReader(str(ROOT / p)) as fit:
            for frame in fit:
                if not isinstance(frame, fitdecode.FitDataMessage):
                    continue
                if frame.name != "record":
                    continue
                d = {f.name: f.value for f in frame.fields}
                hr, pw, cd = d.get("heart_rate"), d.get("power"), d.get("cadence")
                if hr is None or pw is None or cd is None:
                    continue
                if cd < 30 or pw < 10:  # active-pedaling filter
                    continue
                out.append((hr, cd, pw))
    return np.array(out, dtype=float)


def cadence_curve(data, hr_lo, hr_hi, label):
    m = (data[:, 0] >= hr_lo) & (data[:, 0] < hr_hi)
    sub = data[m]
    print(f"\n{label} | HR∈[{hr_lo},{hr_hi}) | n={len(sub)}")
    print(f"  {'cad':>10} {'n':>5} {'P25':>5} {'P50':>5} {'P75':>5}")
    bins = [(40, 55), (55, 65), (65, 75), (75, 85), (85, 95), (95, 110)]
    out = {}
    for lo, hi in bins:
        m2 = (sub[:, 1] >= lo) & (sub[:, 1] < hi)
        if m2.sum() < 15:
            continue
        p = sub[m2, 2]
        med = np.median(p)
        q1, q3 = np.percentile(p, [25, 75])
        out[(lo, hi)] = (m2.sum(), med, q1, q3)
        print(f"  [{lo:>3},{hi:>3}) {m2.sum():>5} "
              f"{q1:>5.0f} {med:>5.0f} {q3:>5.0f}")
    return out


def main():
    out_data = load(OUTDOOR)
    in_data = load(INDOOR_IC8)
    print(f"outdoor samples: {len(out_data)}, indoor samples: {len(in_data)}")
    print(f"outdoor cadence median: {np.median(out_data[:,1]):.0f} rpm")
    print(f"indoor cadence median:  {np.median(in_data[:,1]):.0f} rpm")

    # Tight HR windows where both have data
    for hr_lo, hr_hi in [(130, 145), (145, 160), (155, 170)]:
        print(f"\n{'='*60}")
        print(f"HR window [{hr_lo}, {hr_hi})")
        out_b = cadence_curve(out_data, hr_lo, hr_hi, "OUTDOOR (4iiii)")
        in_b = cadence_curve(in_data, hr_lo, hr_hi, "INDOOR (IC8)")
        if not (out_b and in_b):
            continue
        print(f"\n  inflation by cadence:")
        print(f"  {'cad':>10} {'real':>5} {'IC8':>5} {'ratio':>6}")
        for k in sorted(set(out_b) & set(in_b)):
            _, om, _, _ = out_b[k]
            _, im, _, _ = in_b[k]
            print(f"  [{k[0]:>3},{k[1]:>3}) {om:>5.0f} {im:>5.0f} "
                  f"{im/om:>6.2f}")


if __name__ == "__main__":
    main()
