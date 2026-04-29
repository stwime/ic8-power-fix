"""Fit IC8 power model: P = a(R) * cad^2 + b * cad.

a(R) = c0 + c1*R + c2*R^2 + c3*R^3 (cubic in resistance)
b    = single global drag coefficient

Filters:
  - cadence == 125 (BLE broadcast cap, saturated)
  - cadence < 25 or power < 5 (idle/coast)
"""

import csv
import sys
from pathlib import Path

import numpy as np

CSV = Path(__file__).parent.parent / "data" / "calibration" / "calibration.csv"


def load(path: Path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f):
            cad = float(r["cadence_rpm"] or 0)
            pwr = float(r["power_w"] or 0)
            res = int(r["resistance"])
            t = float(r["timestamp_s"])
            rows.append((t, res, cad, pwr))
    return rows


def filter_rows(rows):
    return [(t, R, c, p) for (t, R, c, p) in rows
            if 25 <= c < 125 and p >= 5]


def fit(rows):
    R = np.array([r[1] for r in rows], dtype=float)
    cad = np.array([r[2] for r in rows])
    pwr = np.array([r[3] for r in rows])

    # P = (c0 + c1*R + c2*R^2 + c3*R^3) * cad^2 + b * cad
    # design matrix columns: cad^2, R*cad^2, R^2*cad^2, R^3*cad^2, cad
    cad2 = cad ** 2
    A = np.column_stack([cad2, R * cad2, R ** 2 * cad2, R ** 3 * cad2, cad])
    coef, *_ = np.linalg.lstsq(A, pwr, rcond=None)
    c0, c1, c2, c3, b = coef
    pred = A @ coef
    return coef, pred


def residual_breakdown(rows, pred):
    pwr = np.array([r[3] for r in rows])
    R = np.array([r[1] for r in rows])
    cad = np.array([r[2] for r in rows])
    resid = pwr - pred

    print(f"\noverall: n={len(rows)}, "
          f"rms={np.sqrt(np.mean(resid**2)):.1f}W, "
          f"max|resid|={np.max(np.abs(resid)):.1f}W, "
          f"power range {pwr.min():.0f}-{pwr.max():.0f}W")

    print("\nresiduals by R bin:")
    for lo, hi in [(0, 20), (20, 35), (35, 50), (50, 70), (70, 100)]:
        mask = (R >= lo) & (R < hi)
        if mask.sum() == 0:
            continue
        r = resid[mask]
        print(f"  R∈[{lo:>2},{hi:>2}): n={mask.sum():>3} "
              f"mean={r.mean():+.1f} rms={np.sqrt((r**2).mean()):.1f} "
              f"max|{np.abs(r).max():.1f}|W")

    print("\nresiduals by cadence bin:")
    for lo, hi in [(25, 50), (50, 75), (75, 100), (100, 125)]:
        mask = (cad >= lo) & (cad < hi)
        if mask.sum() == 0:
            continue
        r = resid[mask]
        print(f"  cad∈[{lo:>3},{hi:>3}): n={mask.sum():>3} "
              f"mean={r.mean():+.1f} rms={np.sqrt((r**2).mean()):.1f} "
              f"max|{np.abs(r).max():.1f}|W")


def main():
    raw = load(CSV)
    rows = filter_rows(raw)
    print(f"loaded {len(raw)} rows, {len(rows)} after filter "
          f"(dropped cad==125, cad<25, power<5)")

    coef, pred = fit(rows)
    c0, c1, c2, c3, b = coef
    print(f"\nfit:")
    print(f"  P = ({c0:+.5f} {c1:+.6f}*R {c2:+.7f}*R^2 {c3:+.9f}*R^3) * cad^2"
          f" {b:+.4f}*cad")

    residual_breakdown(rows, pred)

    # Compare to broadcast: how far off would you be if you used this fit
    # vs trusting the bike? Bike's broadcast IS the "current" power. So the
    # fit-vs-bike difference quantifies the correction we'd apply.
    pwr = np.array([r[3] for r in rows])
    diff = pred - pwr
    print(f"\nfit vs bike broadcast:")
    print(f"  abs diff: mean={np.abs(diff).mean():.1f}W, "
          f"max={np.abs(diff).max():.1f}W")
    print(f"  (this is fit residual — bike broadcast IS our calibration target)")


if __name__ == "__main__":
    main()
