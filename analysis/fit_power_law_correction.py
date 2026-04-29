"""Fit a pure power-law cadence correction:
    ratio(cad) = factor_at_60 * (cad / 60)^x

No bounds, no asymptotes. Anchored at cad=60 only because a power law has
to be anchored somewhere; the anchor is just a parameterization choice.

This is the right model if both IC8 and "true" power scale as power laws
in cadence: ratio = (cad)^(n_ic8 - n_true), so log(ratio) is linear in log(cad).
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
                         "cd": d.get("cadence"),
                         "alt": d.get("enhanced_altitude") or d.get("altitude"),
                         "dist": d.get("distance")})
    return rows


def find_climb_indices(rows, min_grade=3.0, hr_pctile=70):
    alt = np.array([r["alt"] if r["alt"] is not None else np.nan
                    for r in rows], dtype=float)
    dist = np.array([r["dist"] if r["dist"] is not None else np.nan
                     for r in rows], dtype=float)
    hr = np.array([r["hr"] if r["hr"] is not None else 0
                   for r in rows], dtype=float)
    grade = np.full(len(rows), np.nan)
    for i in range(len(rows)):
        for back in range(20, 60):
            j = max(0, i - back)
            dd = dist[i] - dist[j] if not np.isnan(dist[i]-dist[j]) else 0
            da = alt[i] - alt[j] if not np.isnan(alt[i]-alt[j]) else 0
            if dd > 50:
                grade[i] = 100 * da / dd
                break
    hr_thr = np.nanpercentile(hr[hr > 60], hr_pctile)
    return (grade >= min_grade) & (hr >= hr_thr)


def find_hard_indices(rows, min_avg_pw=180, win=120, min_dur=120,
                      drop_late=True):
    pw = np.array([r["pw"] if r["pw"] is not None else 0 for r in rows],
                  dtype=float)
    n = len(pw)
    rolling = np.array([pw[max(0,i-win//2):min(n,i+win//2)].mean()
                        for i in range(n)])
    raw = rolling >= min_avg_pw
    out = np.zeros(n, dtype=bool)
    s = None
    for i, m in enumerate(raw):
        if m and s is None: s = i
        elif not m and s is not None:
            if i - s >= min_dur:
                out[s:i] = True
            s = None
    if s is not None and n - s >= min_dur:
        out[s:n] = True
    if drop_late:
        out[int(0.67 * n):] = False
    return out


def collect():
    out = []
    for path in OUT_PATHS:
        rows = load_records(ROOT / path)
        keep = find_climb_indices(rows) | find_hard_indices(rows)
        for r, k in zip(rows, keep):
            if not k: continue
            if (r["hr"] and r["pw"] is not None and r["cd"]
                    and r["pw"] >= 10 and r["cd"] >= 30):
                out.append((r["hr"], r["cd"], r["pw"]))
    ind = []
    for path in IND_PATHS:
        rows = load_records(ROOT / path)
        for r in rows:
            if (r["hr"] and r["pw"] is not None and r["cd"]
                    and r["pw"] >= 10 and r["cd"] >= 30):
                ind.append((r["hr"], r["cd"], r["pw"]))
    return np.array(out, dtype=float), np.array(ind, dtype=float)


def main():
    out, ind = collect()
    print(f"outdoor (hard pool): {len(out)} samples")
    print(f"indoor (IC8): {len(ind)} samples")

    cads, ratios, weights = [], [], []
    print(f"\nratio per cadence bin:")
    for c in range(45, 110, 5):
        lo, hi = c, c + 5
        m_o = (out[:, 1] >= lo) & (out[:, 1] < hi)
        if m_o.sum() < 20: continue
        hr_lo = np.percentile(out[m_o, 0], 25)
        hr_hi = np.percentile(out[m_o, 0], 75)
        m_i = ((ind[:, 1] >= lo) & (ind[:, 1] < hi)
               & (ind[:, 0] >= hr_lo) & (ind[:, 0] <= hr_hi))
        if m_i.sum() < 15: continue
        med_o = float(np.median(out[m_o, 2]))
        med_i = float(np.median(ind[m_i, 2]))
        cads.append(c + 2.5)
        ratios.append(med_i / med_o)
        # weight by harmonic mean of sample counts
        w = 2.0 / (1.0 / m_o.sum() + 1.0 / m_i.sum())
        weights.append(w)
        print(f"  cad [{lo:>2},{hi:>2})  n_out={m_o.sum():>4} n_in={m_i.sum():>4} "
              f"ratio={med_i/med_o:.3f}  weight={w:.0f}")

    cads = np.array(cads); ratios = np.array(ratios); weights = np.array(weights)

    # Power-law fit: log(ratio) = log(a) + b * log(cad)
    log_cad = np.log(cads); log_ratio = np.log(ratios)
    # weighted LS in log space
    W = weights / weights.sum()
    x_mean = (W * log_cad).sum()
    y_mean = (W * log_ratio).sum()
    cov_xy = (W * (log_cad - x_mean) * (log_ratio - y_mean)).sum()
    var_x = (W * (log_cad - x_mean)**2).sum()
    b = cov_xy / var_x
    log_a = y_mean - b * x_mean
    a = np.exp(log_a)
    crossover = np.exp(-log_a / b)  # cad where ratio = 1

    pred_ratio = a * cads**b
    log_resid = log_ratio - np.log(pred_ratio)
    rms = np.sqrt(np.mean(log_resid**2))

    print(f"\n--- pure power-law fit (no bounds, no plateau) ---")
    print(f"  ratio(cad) = {a:.5f} * cad^{b:.4f}")
    print(f"             = (cad / {crossover:.1f})^{b:.4f}")
    print(f"  weighted log-RMS residual: {rms:.4f}")

    # The cadence exponent of the *true* power formula:
    n_ic8 = 1.5863  # from the IC8 closed-form fit
    n_true_implied = n_ic8 - b
    print(f"\n  IC8 cadence exponent (from calibration): {n_ic8:.3f}")
    print(f"  ratio cadence exponent (this fit):        {b:.3f}")
    print(f"  => implied 'true' cadence exponent:        {n_true_implied:.3f}")

    print(f"\nfitted curve, cadence -> inflation factor:")
    print(f"  {'cad':>4} {'factor':>8} {'observed':>8} {'resid':>8}")
    obs_lookup = dict(zip(cads, ratios))
    for c in range(40, 121, 5):
        f = a * c**b
        if c + 2.5 in obs_lookup:
            o = obs_lookup[c + 2.5]
            print(f"  {c:>4} {f:>8.3f} {o:>8.3f} {f-o:>+8.3f}")
        else:
            print(f"  {c:>4} {f:>8.3f}")

    # Compare to logistic from before
    print(f"\nfor reference, the logistic plateaued at 1.245.")
    print(f"the power law extrapolates to:")
    for c in (90, 100, 110, 120):
        print(f"  cad={c}: factor={a*c**b:.3f}")
    print(f"so it predicts continued growth above cad=80 instead of saturation.")


if __name__ == "__main__":
    main()
