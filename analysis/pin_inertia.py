"""Pin I_crank (effective rotational inertia at the crank, in kg·m²) from
outdoor power-meter ground truth.

Method:
  1. Pool outdoor "true" power samples from clean efforts (stable / climbing
     / early-mid hard, like fit_restricted.py).
  2. Pool indoor IC8 broadcast samples.
  3. For matched cadence + HR bins, take median outdoor and indoor power.
  4. Back-solve indoor R from the IC8 closed-form  P_b = κ·R^N_R·cad^N_CAD
     (constants below, fit once from the IC8 broadcast itself).
  5. Apply physics:  P_true = λ(R)·I·ω²,  ω = cad·π/30,
     λ(R) = α·R^p + β,  using the shipped spin-down fit.
  6. Solve I per bin and take the median across bins.

Why this is a fair anchor: the IC8's closed-form is just a per-bike calibration
table; back-solving R from the broadcast recovers what knob position the rider
had set. The TRUE power is the outdoor 4iiii reading at the same effort.
"""
from pathlib import Path
import numpy as np
import fitdecode

ROOT = Path(__file__).parent.parent
OUT_PATHS = ["data/outdoor/Lunch_Ride.fit",
             "data/outdoor/Lunch_Ride-2.fit",
             "data/outdoor/Lunch_Ride_still_too_much_snow.fit",
             "data/outdoor/Lunch_Ride_harder_effort.fit",
             "data/outdoor/3_nations_loop.fit"]
IND_PATHS = ["data/IC bike/ROUVY_Güímar_Tenerife.fit",
             "data/IC bike/ROUVY_IRONMAN_70_3_Sunshine_Coast_1st_loop_.fit",
             "data/IC bike/ROUVY_Cumbre_del_Sol_Spain.fit",
             "data/IC bike/MyWhoosh_Capital_Circuit.fit"]

# Spin-down derived power-law fit (analysis/fit_saturating.py — trajectory-
# based; supersedes the per-segment-λ aggregation in fit_lambda_R_v3.py).
LAMBDA_ALPHA = 0.000932  # power-law brake amplitude (1/s · R^-p)
LAMBDA_BETA = 0.0355     # residual drag at R=0 (1/s)
LAMBDA_P = 1.33          # brake exponent (dimensionless)
# IC8 closed-form fit (from earlier calibration analysis):
KAPPA = 0.0148
N_R = 0.79
N_CAD = 1.586


def load_records(path):
    rows = []
    with fitdecode.FitReader(str(path)) as fit:
        for f in fit:
            if not isinstance(f, fitdecode.FitDataMessage) or f.name != "record":
                continue
            d = {x.name: x.value for x in f.fields}
            rows.append({
                "hr": d.get("heart_rate"), "pw": d.get("power"),
                "cd": d.get("cadence"),
                "alt": d.get("enhanced_altitude") or d.get("altitude"),
                "dist": d.get("distance"),
            })
    return rows


def stable_indices(rows, window=240, max_cv=0.15, min_mean=150):
    pw = np.array([r["pw"] if r["pw"] is not None else 0 for r in rows], float)
    n = len(pw)
    keep = np.zeros(n, bool)
    for i in range(n):
        lo, hi = max(0, i - window // 2), min(n, i + window // 2)
        seg = pw[lo:hi]
        if len(seg) < window * 0.8: continue
        m = seg.mean()
        if m < min_mean: continue
        if seg.std() / m < max_cv: keep[i] = True
    return keep


def climb_indices(rows, min_grade=3.0, hr_pctile=70):
    alt = np.array([r["alt"] if r["alt"] is not None else np.nan for r in rows], float)
    dist = np.array([r["dist"] if r["dist"] is not None else np.nan for r in rows], float)
    hr = np.array([r["hr"] if r["hr"] is not None else 0 for r in rows], float)
    grade = np.full(len(rows), np.nan)
    for i in range(len(rows)):
        for back in range(20, 60):
            j = max(0, i - back)
            dd = dist[i] - dist[j] if not np.isnan(dist[i] - dist[j]) else 0
            da = alt[i] - alt[j] if not np.isnan(alt[i] - alt[j]) else 0
            if dd > 50:
                grade[i] = 100 * da / dd; break
    hr_thr = np.nanpercentile(hr[hr > 60], hr_pctile)
    return (grade >= min_grade) & (hr >= hr_thr)


def hard_indices(rows, min_avg_pw=180, win=120, min_dur=120):
    pw = np.array([r["pw"] if r["pw"] is not None else 0 for r in rows], float)
    n = len(pw)
    rolling = np.array([pw[max(0,i-win//2):min(n,i+win//2)].mean()
                        for i in range(n)])
    raw = rolling >= min_avg_pw
    out = np.zeros(n, bool)
    s = None
    for i, m in enumerate(raw):
        if m and s is None: s = i
        elif not m and s is not None:
            if i - s >= min_dur: out[s:i] = True
            s = None
    if s is not None and n - s >= min_dur: out[s:n] = True
    return out


def collect_outdoor():
    pts = []
    for path in OUT_PATHS:
        rows = load_records(ROOT / path)
        n = len(rows)
        keep = stable_indices(rows) | climb_indices(rows) | hard_indices(rows)
        keep[int(0.67 * n):] = False  # drop late-ride fatigue
        for i, r in enumerate(rows):
            if not keep[i]: continue
            if (r["hr"] and r["pw"] is not None and r["cd"]
                    and r["pw"] >= 10 and r["cd"] >= 30):
                pts.append((r["hr"], r["cd"], r["pw"]))
    return np.array(pts, float)


def collect_indoor():
    pts = []
    for path in IND_PATHS:
        rows = load_records(ROOT / path)
        for r in rows:
            if (r["hr"] and r["pw"] is not None and r["cd"]
                    and r["pw"] >= 10 and r["cd"] >= 30):
                pts.append((r["hr"], r["cd"], r["pw"]))
    return np.array(pts, float)


def back_solve_R(P_b, cad):
    """Invert the IC8 closed-form to recover the resistance setting."""
    return (P_b / (KAPPA * cad ** N_CAD)) ** (1.0 / N_R)


def main():
    out = collect_outdoor()
    ind = collect_indoor()
    print(f"outdoor truth pool: {len(out)} samples")
    print(f"indoor IC8 active: {len(ind)} samples")

    print(f"\n{'cad_bin':>10} {'n_o':>4} {'n_i':>4} {'P_out':>6} {'P_in':>6} "
          f"{'R_back':>7} {'P_phys/I':>9} {'I_est':>7}")
    estimates = []
    for c in range(50, 90, 5):
        lo, hi = c, c + 5
        m_o = (out[:, 1] >= lo) & (out[:, 1] < hi)
        if m_o.sum() < 30: continue
        hr_lo = np.percentile(out[m_o, 0], 25)
        hr_hi = np.percentile(out[m_o, 0], 75)
        m_i = ((ind[:, 1] >= lo) & (ind[:, 1] < hi)
               & (ind[:, 0] >= hr_lo) & (ind[:, 0] <= hr_hi))
        if m_i.sum() < 30: continue
        med_o = float(np.median(out[m_o, 2]))
        med_i = float(np.median(ind[m_i, 2]))
        cad_c = c + 2.5
        R = back_solve_R(med_i, cad_c)
        omega = cad_c * np.pi / 30.0
        rp = max(R, 0.0) ** LAMBDA_P if R > 0 else 0.0
        lam_R = LAMBDA_ALPHA * rp + LAMBDA_BETA
        phys_per_I = lam_R * omega ** 2
        I_est = med_o / phys_per_I
        weight = 2.0 / (1.0 / m_o.sum() + 1.0 / m_i.sum())
        estimates.append((cad_c, I_est, weight, R, med_o, med_i))
        print(f"  [{lo:>2},{hi:>2})    {m_o.sum():>4} {m_i.sum():>4} "
              f"{med_o:>6.0f} {med_i:>6.0f} {R:>7.1f} "
              f"{phys_per_I:>9.4f} {I_est:>7.3f}")

    if not estimates:
        print("no usable bins")
        return

    cads = np.array([e[0] for e in estimates])
    Is = np.array([e[1] for e in estimates])
    ws = np.array([e[2] for e in estimates])
    I_med = float(np.median(Is))
    I_wmean = float((Is * ws).sum() / ws.sum())
    print(f"\nI_crank estimates per bin: {Is.round(3).tolist()}")
    print(f"  median:        {I_med:.3f} kg·m²")
    print(f"  weighted mean: {I_wmean:.3f} kg·m²")
    print(f"  across-bin spread (CV): {Is.std() / Is.mean() * 100:.1f}%")

    # Sanity check: physical plausibility
    # Flywheel ~ 18 kg, radius ~ 0.18 m → I_flywheel ≈ 0.29 kg·m².
    # With gear ratio g, I_crank = I_flywheel · g². So g = sqrt(I_crank / 0.29).
    g = np.sqrt(I_wmean / 0.29)
    print(f"\nimplied gear ratio (flywheel:crank), assuming I_flywheel=0.29: {g:.2f}")
    print(f"  (typical IC8 gearing is reported ~6:1 — this should be in that ballpark)")

    print(f"\n=> use I_crank = {I_wmean:.3f} kg·m² as the default in "
          f"bridge/lib/physics/calibration.dart")


if __name__ == "__main__":
    main()
