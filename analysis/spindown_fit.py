"""Fit the IC8's flywheel coastdown dynamics from BLE-recorded spin-downs.

Model:  I * dω/dt = -(c_brake * R + c_friction) * ω
        =>  ω(t) = ω₀ * exp(-λ(R) * t),  λ(R) = (c_brake*R + c_friction)/I

We measure λ at each R from log-linear fit on a coastdown segment, then
fit λ(R) = a*R + b across coastdowns to separate brake from friction.

Power dissipated at steady state (no rider acceleration):
    P = (c_brake*R + c_friction) * ω² = λ(R) * I * ω²
where ω is in rad/s. So once I is pinned (one outdoor anchor point),
the brake is fully characterized.

Source: data/calibration/spin_downs.csv. We use the CSC-derived cadence
(cadence_rpm_csc), not the FTMS broadcast cadence: the broadcast clips
to 0 below ~40 rpm even while the wheel is still rotating, which silently
truncates high-R coastdowns. CSC reports actual crank-event timestamps
and remains valid all the way down to the rate at which crank events
arrive within the notification window.
"""
from pathlib import Path
import csv
import numpy as np

ROOT = Path(__file__).parent.parent
SOURCE = ROOT / "data/calibration/spin_downs.csv"


def _csc(row):
    v = row.get("cadence_rpm_csc", "")
    if v is None or v == "":
        return None
    return float(v)


def find_clean_coastdowns(rows, min_cad_start=70, min_samples=4,
                          r_jitter_max=1, flat_tol=0.05):
    """Find runs of CSC-cadence decreasing at near-constant R.

    A run begins when CSC cadence is at or above ``min_cad_start`` and the
    rider has stopped pedaling (cadence about to drop). The run extends as
    long as:
      - CSC cadence is available (parser produced a value),
      - CSC cadence is non-increasing (small flat_tol allowed for the
        case where two consecutive notifications report the same average
        rate within rounding),
      - resistance stays within ±r_jitter_max of the run's starting R.

    No FTMS-cap trim — CSC doesn't have that artifact.
    """
    segs = []
    i = 0
    while i < len(rows) - min_samples:
        c0 = _csc(rows[i])
        if c0 is None or c0 < min_cad_start:
            i += 1; continue
        R0 = int(rows[i]["resistance"])
        j = i
        while j + 1 < len(rows):
            c_next = _csc(rows[j+1])
            R_next = int(rows[j+1]["resistance"])
            c_curr = _csc(rows[j])
            if (c_next is not None and c_curr is not None
                    and c_next < c_curr + flat_tol
                    and abs(R_next - R0) <= r_jitter_max):
                j += 1
            else:
                break
        if j - i + 1 >= min_samples:
            seg = rows[i:j+1]
            segs.append((seg, R0))
        i = j + 1 if j > i else i + 1
    return segs


def fit_decay(seg):
    t = np.array([float(r["timestamp_s"]) for r in seg])
    c = np.array([_csc(r) for r in seg], dtype=float)
    y = np.log(c)
    A = np.vstack([t, np.ones_like(t)]).T
    sl, ic = np.linalg.lstsq(A, y, rcond=None)[0]
    lam = -sl
    pred = sl * t + ic
    r2 = 1 - np.sum((y - pred)**2) / max(np.sum((y - y.mean())**2), 1e-12)
    return lam, r2


def main():
    rows = list(csv.DictReader(SOURCE.open()))
    segs = find_clean_coastdowns(rows)
    print(f"clean coastdowns from {SOURCE.name} (CSC-based): {len(segs)}")
    print(f"\n{'R':>3} {'n':>3} {'cad_hi':>6} {'cad_lo':>6} {'dur_s':>6} "
          f"{'λ_per_s':>9} {'r²':>6}")

    rows_out = []
    for seg, R in segs:
        lam, r2 = fit_decay(seg)
        if r2 < 0.95: continue
        c0 = _csc(seg[0])
        c1 = _csc(seg[-1])
        dur = float(seg[-1]["timestamp_s"]) - float(seg[0]["timestamp_s"])
        rows_out.append((R, lam, len(seg), c0, c1, r2))
        print(f"{R:>3} {len(seg):>3} {c0:>6.0f} {c1:>6.0f} {dur:>6.1f} "
              f"{lam:>9.4f} {r2:>6.3f}")

    R = np.array([r[0] for r in rows_out], dtype=float)
    lam = np.array([r[1] for r in rows_out])
    n = np.array([r[2] for r in rows_out])
    W = np.diag(np.sqrt(n))
    A = np.vstack([R, np.ones_like(R)]).T
    (a, b), *_ = np.linalg.lstsq(W @ A, W @ lam, rcond=None)

    pred = a * R + b
    rms = np.sqrt(np.mean((lam - pred)**2))
    print(f"\nlinear fit:  λ(R) = {a:.5f}·R + {b:.4f}  (per second)")
    print(f"  weighted RMS residual: {rms:.4f} 1/s")
    print(f"  friction-only τ (R=0): {1/b:.1f} s")
    print(f"  brake/friction at R=50: {a*50/b:.2f}× friction")

    print(f"\n  per-point residuals:")
    print(f"  {'R':>3} {'λ_meas':>8} {'λ_pred':>8} {'resid':>8}")
    order = np.argsort(R)
    for k in order:
        print(f"  {int(R[k]):>3} {lam[k]:>8.4f} {pred[k]:>8.4f} "
              f"{lam[k]-pred[k]:>+8.4f}")

    print(f"\nphysics implication:")
    print(f"  P_true(R, cad) = ({a:.5f}·R + {b:.4f}) · I · (cad·π/30)² watts")
    print(f"                 = ({a:.5f}·R + {b:.4f}) · I · cad² · 0.01097")


if __name__ == "__main__":
    main()
