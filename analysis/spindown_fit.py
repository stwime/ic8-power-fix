"""Fit the IC8's flywheel coastdown dynamics from BLE-recorded spin-downs.

Model:  I * dω/dt = -(c_brake * R + c_friction) * ω
        =>  ω(t) = ω₀ * exp(-λ(R) * t),  λ(R) = (c_brake*R + c_friction)/I

We measure λ at each R from log-linear fit on a coastdown segment, then
fit λ(R) = a*R + b across coastdowns to separate brake from friction.

Power dissipated at steady state (no rider acceleration):
    P = (c_brake*R + c_friction) * ω² = λ(R) * I * ω²
where ω is in rad/s. So once I is pinned (one outdoor anchor point),
the brake is fully characterized.

Sources: two structured spin-down sessions (data/calibration/spin_downs_apr29.csv
and spin_downs_apr30.csv). We use the CSC-derived cadence (cadence_rpm_csc),
not the FTMS broadcast cadence: the broadcast clips to 0 below ~40 rpm even
while the wheel is still rotating, which silently truncates high-R coastdowns.
CSC reports actual crank-event timestamps and remains valid all the way down
to the rate at which crank events arrive within the notification window.

Two segments are flagged BAD and dropped (rider brushed a pedal mid-coastdown):
  * Apr 29 R=33 (only R=33 segment that session — 0.282/s vs ~0.234/s expected)
  * Apr 30 first R=11 (three R=11 segments; the first reads 0.0928 vs the next
    two at 0.0878 and 0.0899)
The other 20 segments survive an r²≥0.95 cutoff and end at sensor-floor cadence.
"""
from pathlib import Path
import csv
import numpy as np

ROOT = Path(__file__).parent.parent
SOURCES = [
    ROOT / "data/calibration/spin_downs_apr29.csv",
    ROOT / "data/calibration/spin_downs_apr30.csv",
]
# (session_label, R, occurrence_index) — segments to drop.
BAD_SEGMENTS = {
    ("apr29", 33, 0),  # outlier; rider likely brushed pedal
    ("apr30", 11, 0),  # user-flagged: rider brushed pedal on the first R=11
}


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


def collect_segments():
    """Return list of (session_label, R, occurrence_idx, lam, r2, len, c0, c1, dur)
    across both sources, post-filter (r²≥0.95, ends at sensor floor, not flagged bad)."""
    results = []
    for src in SOURCES:
        label = src.stem.replace("spin_downs_", "")  # "apr29" or "apr30"
        rows = list(csv.DictReader(src.open()))
        segs = find_clean_coastdowns(rows)
        per_R_count = {}
        for seg, R in segs:
            occ = per_R_count.get(R, 0)
            per_R_count[R] = occ + 1
            lam, r2 = fit_decay(seg)
            c0 = _csc(seg[0]); c1 = _csc(seg[-1])
            dur = float(seg[-1]["timestamp_s"]) - float(seg[0]["timestamp_s"])
            bad = (label, R, occ) in BAD_SEGMENTS
            keep = (r2 >= 0.95) and (c1 < 25) and not bad
            results.append({
                "label": label, "R": R, "occ": occ,
                "lam": lam, "r2": r2, "n": len(seg),
                "c0": c0, "c1": c1, "dur": dur,
                "bad": bad, "keep": keep,
            })
    return results


def main():
    rows_out = collect_segments()
    print(f"sources: {[s.name for s in SOURCES]}")
    print(f"\n{'sess':>5} {'R':>3} {'occ':>3} {'n':>3} {'cad_hi':>6} {'cad_lo':>6} "
          f"{'dur_s':>6} {'λ_per_s':>9} {'r²':>6} flag")
    for r in rows_out:
        flag = "BAD" if r["bad"] else ("ok" if r["keep"] else "lowR²" if r["r2"] < 0.95 else "noFloor")
        print(f"{r['label']:>5} {r['R']:>3} {r['occ']:>3} {r['n']:>3} "
              f"{r['c0']:>6.0f} {r['c1']:>6.0f} {r['dur']:>6.1f} "
              f"{r['lam']:>9.4f} {r['r2']:>6.3f}  {flag}")

    keep = [r for r in rows_out if r["keep"]]
    R = np.array([r["R"] for r in keep], dtype=float)
    lam = np.array([r["lam"] for r in keep])
    n = np.array([r["n"] for r in keep])
    W = np.diag(np.sqrt(n))
    A = np.vstack([R, np.ones_like(R)]).T
    (a, b), *_ = np.linalg.lstsq(W @ A, W @ lam, rcond=None)
    pred = a * R + b
    rms = np.sqrt(np.mean((lam - pred)**2))

    print(f"\npooled fit ({len(keep)} clean spindowns from {len(SOURCES)} sessions):")
    print(f"  λ(R) = {a:.5f}·R + {b:.4f}  (per second)")
    print(f"  weighted RMS residual: {rms:.4f} 1/s")
    print(f"  friction-only τ (R=0): {1/b:.1f} s")
    print(f"  brake/friction at R=50: {a*50/b:.2f}× friction")

    # Per-session sanity: how much does each session disagree with the pooled line?
    print(f"\nper-session agreement with pooled fit:")
    for label in sorted({r["label"] for r in keep}):
        sub = [r for r in keep if r["label"] == label]
        Rs = np.array([r["R"] for r in sub], float)
        ls = np.array([r["lam"] for r in sub])
        ns = np.array([r["n"] for r in sub])
        Ws = np.diag(np.sqrt(ns))
        As = np.vstack([Rs, np.ones_like(Rs)]).T
        (ai, bi), *_ = np.linalg.lstsq(Ws @ As, Ws @ ls, rcond=None)
        rmsi = np.sqrt(np.mean((ls - (ai*Rs+bi))**2))
        print(f"  {label}: a={ai:.5f}, b={bi:.4f}, n={len(sub)}, RMS={rmsi:.4f}")

    print(f"\nphysics implication:")
    print(f"  P_true(R, cad) = ({a:.5f}·R + {b:.4f}) · I · (cad·π/30)² watts")
    print(f"                 = ({a:.5f}·R + {b:.4f}) · I · cad² · 0.01097")


if __name__ == "__main__":
    main()
