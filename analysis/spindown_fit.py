"""Fit the IC8's flywheel coastdown dynamics from BLE-recorded spin-downs.

Model:  I * dω/dt = -(c_brake * R + c_friction) * ω
        =>  ω(t) = ω₀ * exp(-λ(R) * t),  λ(R) = (c_brake*R + c_friction)/I

We measure λ at each R from log-linear fit on a coastdown segment, then
fit λ(R) = a*R + b across coastdowns to separate brake from friction.

Power dissipated at steady state (no rider acceleration):
    P = (c_brake*R + c_friction) * ω² = λ(R) * I * ω²
where ω is in rad/s. So once I is pinned (one outdoor anchor point),
the brake is fully characterized.

Sources used: the explicit "spin downs.txt" file (rider lifts feet off),
which gives clean exponential decays. Other files have decays that are
contaminated by residual rider torque or saturated cadence readings.
"""
from pathlib import Path
import csv
import numpy as np

ROOT = Path(__file__).parent.parent
SOURCE = ROOT / "data/calibration/spin_downs.csv"


def find_clean_coastdowns(rows, min_cad_start=80, min_samples=6,
                          r_jitter_max=1, cad_cap=125):
    """Find monotone-decreasing cadence runs with ~constant R.

    Drops samples pinned at the cadence cap (BLE caps at 125 rpm), so the
    decay fit only sees the post-saturation portion.
    """
    segs = []
    i = 0
    while i < len(rows) - min_samples:
        if float(rows[i]["cadence_rpm"]) < min_cad_start:
            i += 1; continue
        R0 = int(rows[i]["resistance"])
        j = i
        while j + 1 < len(rows):
            c1 = float(rows[j]["cadence_rpm"])
            c2 = float(rows[j+1]["cadence_rpm"])
            R2 = int(rows[j+1]["resistance"])
            if c2 < c1 - 0.5 and abs(R2 - R0) <= r_jitter_max and c2 > 0.5:
                j += 1
            else:
                break
        if j - i >= min_samples:
            seg = rows[i:j+1]
            # Trim leading samples pinned at cap (the cadence was actually
            # higher; the cap distorts the early decay slope).
            while seg and float(seg[0]["cadence_rpm"]) >= cad_cap:
                seg = seg[1:]
            if len(seg) >= min_samples:
                segs.append((seg, R0))
        i = j + 1 if j > i else i + 1
    return segs


def fit_decay(seg):
    t = np.array([float(r["timestamp_s"]) for r in seg])
    c = np.array([float(r["cadence_rpm"]) for r in seg])
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
    print(f"clean coastdowns from {SOURCE.name}: {len(segs)}")
    print(f"\n{'R':>3} {'n':>3} {'cad_hi':>6} {'cad_lo':>6} {'dur_s':>6} "
          f"{'λ_per_s':>9} {'r²':>6}")

    rows_out = []
    for seg, R in segs:
        lam, r2 = fit_decay(seg)
        if r2 < 0.95: continue
        c0 = float(seg[0]["cadence_rpm"])
        c1 = float(seg[-1]["cadence_rpm"])
        dur = float(seg[-1]["timestamp_s"]) - float(seg[0]["timestamp_s"])
        rows_out.append((R, lam, len(seg), c0, c1, r2))
        print(f"{R:>3} {len(seg):>3} {c0:>6.0f} {c1:>6.0f} {dur:>6.1f} "
              f"{lam:>9.4f} {r2:>6.3f}")

    R = np.array([r[0] for r in rows_out])
    lam = np.array([r[1] for r in rows_out])
    n = np.array([r[2] for r in rows_out])
    W = np.diag(np.sqrt(n))
    A = np.vstack([R, np.ones_like(R, dtype=float)]).T
    (a, b), *_ = np.linalg.lstsq(W @ A, W @ lam, rcond=None)

    pred = a * R + b
    rms = np.sqrt(np.mean((lam - pred)**2))
    print(f"\nlinear fit:  λ(R) = {a:.5f}·R + {b:.4f}  (per second)")
    print(f"  weighted RMS residual: {rms:.4f} 1/s")
    print(f"  friction-only τ (R=0): {1/b:.1f} s")
    print(f"  brake/friction at R=50: {a*50/b:.2f}× friction")

    print(f"\nphysics implication:")
    print(f"  P_true(R, cad) = ({a:.5f}·R + {b:.4f}) · I · (cad·π/30)² watts")
    print(f"                 = ({a:.5f}·R + {b:.4f}) · I · cad² · 0.01097")
    print(f"  Cadence exponent in true power = 2.000 (vs IC8 broadcast: 1.586)")
    print(f"  => bike's wrong cadence exponent is the source of cad-dependent")
    print(f"     inflation we measured against outdoor data.")
    print(f"\n  To pin I_crank in kg·m², need one outdoor-truth anchor:")
    print(f"    e.g. at cad=70, R=R*, P_outdoor=P*, then")
    print(f"    I_crank = P* / [({a:.5f}·R* + {b:.4f}) · (70·π/30)²]")


if __name__ == "__main__":
    main()
