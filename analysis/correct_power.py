"""Apply the physics-based power correction to a parsed BLE log.

Input CSV: output of parse_nrf_log.py (FTMS fields + CSC fields per row).
Output CSV: same columns + power_w_steady, power_w_ke, power_w_corrected.

Model:
    P_corrected = λ(R_smoothed) · I_crank · ω²  +  I_crank · ω · dω/dt
                  └─────── steady-state ───────┘  └───── KE term ─────┘
    λ(R) = α · R^p / (R^p + R_c^p) + β

Where ω = cad·π/30 (rad/s). α, R_c, p, β are from the spin-down fit
(Hill form — physics-derived from the eddy-current B²(d) coupling; see
analysis/spindown_fit.py for the comparison vs the linear and saturating
alternatives). I_crank is pinned by an outdoor anchor; default below;
override with --i.

Implementation notes:
  * R is median-filtered (window 5) to kill the ±1 sensor jitter.
  * Cadence ω is taken from CSC (per-revolution timing) when available,
    else from FTMS (0.5 rpm quantized). This matters for the KE term.
  * dω/dt uses central differences over a 3-sample window (~3 s span).
    Edges fall back to one-sided differences.
  * Cadence above 124 rpm is treated as cap-saturated and the row is
    flagged (corrected power not emitted, since cadence input is wrong).
  * R == 100 is the bike's hard cap (crank locked). Those rows get NaN.
"""
import csv
import sys
import argparse
from pathlib import Path

import numpy as np

# Spin-down derived Hill-form fit (analysis/spindown_fit.py).
# Keep in sync with bridge/lib/physics/calibration.dart defaults.
LAMBDA_ALPHA = 0.207     # Hill-form brake amplitude (1/s)
LAMBDA_BETA = 0.034      # residual drag at R=0 (1/s)
LAMBDA_RC = 38.5         # half-max knee on the dial (R-units)
LAMBDA_P = 1.90          # Hill exponent (dimensionless)
# Inertia anchor (analysis/pin_inertia.py).
DEFAULT_I_CRANK = 24.5
# Saturation flags
CAD_CAP = 124.0       # FTMS BLE cap is 125; treat anything ≥124 as suspect
R_CAP = 100           # hard mechanical cap; brake locked, no useful info


def median_filter(x, window):
    n = len(x)
    out = np.empty(n)
    half = window // 2
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        out[i] = np.median(x[lo:hi])
    return out


def central_diff(y, t):
    """Compute dy/dt with central differences (fall back to one-sided at edges)."""
    n = len(y)
    out = np.zeros(n)
    for i in range(n):
        if 0 < i < n - 1 and t[i + 1] > t[i - 1]:
            out[i] = (y[i + 1] - y[i - 1]) / (t[i + 1] - t[i - 1])
        elif i == 0 and n > 1 and t[1] > t[0]:
            out[i] = (y[1] - y[0]) / (t[1] - t[0])
        elif i == n - 1 and n > 1 and t[i] > t[i - 1]:
            out[i] = (y[i] - y[i - 1]) / (t[i] - t[i - 1])
    return out


def correct(rows_in, i_crank, r_smooth_window=5, dt_window=3):
    n = len(rows_in)
    t = np.array([float(r["timestamp_s"]) for r in rows_in])
    cad_ftms = np.array([float(r["cadence_rpm"]) for r in rows_in])
    cad_csc = np.array([float(r["cadence_rpm_csc"]) if r.get("cadence_rpm_csc")
                        else np.nan for r in rows_in])
    R = np.array([int(r["resistance"]) for r in rows_in])
    P_b = np.array([int(r["power_w"]) for r in rows_in])

    # Choose cadence source: CSC where available, else FTMS.
    cad = np.where(np.isnan(cad_csc), cad_ftms, cad_csc)

    # Median-filter R against ±1 sensor jitter.
    R_smooth = median_filter(R, r_smooth_window)

    # Saturation masks
    # FTMS cap is at 125 rpm (uint16 at 0.5 rpm resolution); CSC is uncapped
    # (per-revolution event timing has no encoded ceiling). So mask only if
    # FTMS is at cap AND CSC is unavailable.
    csc_available = ~np.isnan(cad_csc)
    cap_mask = (cad_ftms >= CAD_CAP) & ~csc_available
    rcap_mask = R >= R_CAP
    inactive_mask = cad <= 0

    # Convert to angular velocity (rad/s)
    omega = cad * np.pi / 30.0

    # Numerical derivative of omega
    omega_dot = central_diff(omega, t)
    # Smooth omega_dot lightly (3-sample boxcar) to reduce 1Hz quantization noise
    if dt_window > 1:
        kernel = np.ones(dt_window) / dt_window
        omega_dot = np.convolve(omega_dot, kernel, mode="same")

    # Physics — Hill form: λ(R) = α·R^p / (R^p + R_c^p) + β. Guard R=0
    # explicitly to avoid 0**p evaluating funny at fractional p.
    R_pos = np.maximum(R_smooth, 0.0)
    rp = R_pos ** LAMBDA_P
    lam_R = LAMBDA_ALPHA * rp / (rp + LAMBDA_RC ** LAMBDA_P) + LAMBDA_BETA
    p_steady = lam_R * i_crank * omega ** 2
    p_ke = i_crank * omega * omega_dot
    p_corrected = p_steady + p_ke

    # Apply saturation: zero out anything where input is unreliable
    bad = cap_mask | rcap_mask | inactive_mask
    p_steady_out = np.where(bad, np.nan, p_steady)
    p_ke_out = np.where(bad, np.nan, p_ke)
    p_corr_out = np.where(bad, np.nan, np.maximum(p_corrected, 0.0))

    rows_out = []
    for i, r in enumerate(rows_in):
        new = dict(r)
        new["R_smooth"] = round(float(R_smooth[i]), 2)
        new["omega_rad_s"] = round(float(omega[i]), 4)
        new["omega_dot_rad_s2"] = round(float(omega_dot[i]), 4)
        new["power_w_steady"] = (round(float(p_steady_out[i]), 1)
                                 if not np.isnan(p_steady_out[i]) else "")
        new["power_w_ke"] = (round(float(p_ke_out[i]), 1)
                             if not np.isnan(p_ke_out[i]) else "")
        new["power_w_corrected"] = (round(float(p_corr_out[i]), 1)
                                    if not np.isnan(p_corr_out[i]) else "")
        rows_out.append(new)
    return rows_out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input_csv", help="parsed BLE CSV (from parse_nrf_log.py)")
    ap.add_argument("output_csv", help="where to write the corrected CSV")
    ap.add_argument("--i", type=float, default=DEFAULT_I_CRANK,
                    help=f"I_crank in kg·m² (default {DEFAULT_I_CRANK})")
    args = ap.parse_args()

    rows_in = list(csv.DictReader(open(args.input_csv)))
    rows_out = correct(rows_in, args.i)

    fieldnames = list(rows_in[0].keys()) + [
        "R_smooth", "omega_rad_s", "omega_dot_rad_s2",
        "power_w_steady", "power_w_ke", "power_w_corrected",
    ]
    with open(args.output_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows_out)

    # Quick summary
    pcorr = [float(r["power_w_corrected"]) for r in rows_out
             if r["power_w_corrected"] != ""]
    pbike = [int(r["power_w"]) for r, rc in zip(rows_in, rows_out)
             if rc["power_w_corrected"] != ""]
    if pcorr:
        ratio = np.mean(pcorr) / max(np.mean(pbike), 1.0)
        print(f"wrote {len(rows_out)} rows ({len(pcorr)} with corrected power)")
        print(f"  mean broadcast: {np.mean(pbike):.0f} W")
        print(f"  mean corrected: {np.mean(pcorr):.0f} W  ({ratio:.2f}× broadcast)")
        print(f"  I_crank used:   {args.i} kg·m²")


if __name__ == "__main__":
    main()
