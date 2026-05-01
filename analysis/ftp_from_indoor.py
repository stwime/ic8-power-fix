"""Estimate FTP from indoor IC8 .fit files at I=9.3 vs I=11.0.

Pipeline:
  1. Load (timestamp, cadence, broadcast_power) from each IC8 indoor session.
  2. Back-solve resistance R from broadcast power using the closed-form
     P_b = κ·R^N_R·cad^N_CAD (constants from earlier IC8 fit).
  3. Compute corrected power: P = λ(R)·I·ω² + I·ω·dω/dt with the
     saturating Hill form λ(R) = β + α·R^p / (R^p + R_c^p).
  4. Best rolling N-min averages → FTP estimate (0.95 × best-20-min, or
     best-60-min directly when sessions are long enough).

Reports per-file and pooled FTP at both I_crank candidates.
"""
from pathlib import Path
import math
import numpy as np
import fitdecode

ROOT = Path(__file__).parent.parent
IND_PATHS = [
    "data/IC bike/ROUVY_Güímar_Tenerife.fit",
    "data/IC bike/ROUVY_IRONMAN_70_3_Sunshine_Coast_1st_loop_.fit",
    "data/IC bike/ROUVY_Cumbre_del_Sol_Spain.fit",
    "data/IC bike/MyWhoosh_Capital_Circuit.fit",
]

# Hill λ(R) — must match calibration.dart and correct_power.py.
LAMBDA_ALPHA = 2.3623
LAMBDA_BETA = 0.0396
LAMBDA_RC = 54.58
LAMBDA_P = 3.41
# IC8 closed-form back-solve: P_b = κ·R^N_R·cad^N_CAD
KAPPA = 0.0148
N_R = 0.79
N_CAD = 1.586


def load_records(path):
    out = []
    with fitdecode.FitReader(str(path)) as fit:
        for f in fit:
            if not isinstance(f, fitdecode.FitDataMessage) or f.name != "record":
                continue
            d = {x.name: x.value for x in f.fields}
            ts = d.get("timestamp")
            pw = d.get("power")
            cd = d.get("cadence")
            if ts is None or pw is None or cd is None:
                continue
            out.append({"t": ts.timestamp(), "pw": float(pw), "cd": float(cd)})
    return out


def best_window_avg(p, t, window_s):
    """Best (max) rolling average of p over a window of `window_s` seconds.
    Time is uneven 1Hz; use a left-pointer sliding window."""
    if len(p) < 2:
        return float("nan"), 0
    n = len(p)
    best = 0.0
    best_start = 0
    j = 0
    s = 0.0
    cnt = 0
    for i in range(n):
        s += p[i]
        cnt += 1
        # shrink from left until window fits
        while cnt > 1 and t[i] - t[j] > window_s:
            s -= p[j]
            j += 1
            cnt -= 1
        if t[i] - t[j] >= window_s * 0.95 and cnt >= window_s * 0.5:
            avg = s / cnt
            if avg > best:
                best = avg
                best_start = j
    return best, best_start


def correct(rows, i_crank):
    t = np.array([r["t"] for r in rows])
    cad = np.array([r["cd"] for r in rows])
    pb = np.array([r["pw"] for r in rows])

    # Back-solve R from broadcast: R = (P_b / (κ · cad^N_CAD))^(1/N_R)
    cad_safe = np.where(cad > 0, cad, np.nan)
    R = np.where(
        (pb > 0) & (cad_safe > 0),
        (pb / (KAPPA * cad_safe**N_CAD)) ** (1.0 / N_R),
        0.0,
    )
    R = np.clip(R, 0, 100)

    omega = cad * math.pi / 30.0
    # dω/dt via central differences
    om_dot = np.zeros_like(omega)
    for i in range(1, len(omega) - 1):
        if t[i + 1] > t[i - 1]:
            om_dot[i] = (omega[i + 1] - omega[i - 1]) / (t[i + 1] - t[i - 1])

    R_pos = np.maximum(R, 0.0)
    rp = np.where(R_pos > 0, R_pos**LAMBDA_P, 0.0)
    rcp = LAMBDA_RC**LAMBDA_P
    u = np.where(R_pos > 0, rp / (rp + rcp), 0.0)
    lam = LAMBDA_BETA + LAMBDA_ALPHA * u
    p_steady = lam * i_crank * omega**2
    p_ke = i_crank * omega * om_dot
    p_corr = np.maximum(p_steady + p_ke, 0.0)
    # Mask invalid (cad=0 or pb=0) rows
    valid = (cad > 0) & (pb > 0)
    return t[valid], p_corr[valid]


def main():
    print(f"{'file':<55} {'dur':>5} {'P20 (9.3)':>10} {'P20 (11)':>9} "
          f"{'FTP (9.3)':>10} {'FTP (11)':>9}")
    pooled = {9.3: [], 11.0: []}
    for rel in IND_PATHS:
        path = ROOT / rel
        rows = load_records(path)
        if not rows:
            continue
        t = np.array([r["t"] for r in rows])
        dur_min = (t[-1] - t[0]) / 60.0
        line_pieces = [path.name[:55].ljust(55), f"{dur_min:>5.1f}"]
        ftps = {}
        for I in (9.3, 11.0):
            tt, pc = correct(rows, I)
            best20, _ = best_window_avg(pc, tt, 20 * 60)
            best60, _ = best_window_avg(pc, tt, 60 * 60)
            ftp_est = 0.95 * best20 if best60 == 0 else max(0.95 * best20, best60)
            line_pieces.append(f"{best20:>10.0f}")
            ftps[I] = (best20, best60, ftp_est)
            pooled[I].append((best20, best60, dur_min))
        line_pieces.append(f"{ftps[9.3][2]:>10.0f}")
        line_pieces.append(f"{ftps[11.0][2]:>9.0f}")
        print(" ".join(line_pieces))

    print()
    for I in (9.3, 11.0):
        # Pooled best-20-min across all sessions
        best20s = [b for b, _, _ in pooled[I]]
        best60s = [b for _, b, _ in pooled[I]]
        # Use highest single-session 20-min as the FTP estimate.
        max_b20 = max(best20s) if best20s else 0
        max_b60 = max(best60s) if best60s else 0
        # Standard FTP estimator: 0.95 × best-20-min if no good 60-min,
        # else best-60-min directly.
        if max_b60 > 0:
            ftp = max(0.95 * max_b20, max_b60)
            label = f"max(0.95×best-20={0.95*max_b20:.0f}, best-60={max_b60:.0f})"
        else:
            ftp = 0.95 * max_b20
            label = f"0.95×best-20={0.95*max_b20:.0f}"
        print(f"I_crank={I:>4.1f}: best-20-min across sessions = {max_b20:.0f} W   "
              f"best-60-min = {max_b60:.0f} W   FTP ≈ {ftp:.0f} W   ({label})")


if __name__ == "__main__":
    main()
