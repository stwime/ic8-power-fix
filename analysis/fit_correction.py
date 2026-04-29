"""Fit a smooth cadence -> inflation-factor curve from all the
outdoor-vs-IC8 comparisons we've built up.

Strategy:
  1. Pool clean ground-truth points: snow-ride climbs (≥3% grade, top-30%
     HR) AND early+mid hard efforts (rolling-2min ≥180W) from all rides.
  2. Pool all IC8 indoor active-pedaling samples.
  3. For each cadence (5-rpm bins), find samples in both pools at matched
     HR and compute the ratio.
  4. Fit a 4-parameter logistic to (cadence, ratio).
"""
from pathlib import Path
import numpy as np
import fitdecode
from scipy.optimize import curve_fit

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
            rows.append({
                "hr": d.get("heart_rate"), "pw": d.get("power"),
                "cd": d.get("cadence"),
                "alt": d.get("enhanced_altitude") or d.get("altitude"),
                "dist": d.get("distance"),
            })
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
            dd = dist[i] - dist[j] if not np.isnan(dist[i] - dist[j]) else 0
            da = alt[i] - alt[j] if not np.isnan(alt[i] - alt[j]) else 0
            if dd > 50:
                grade[i] = 100 * da / dd
                break
    hr_thr = np.nanpercentile(hr[hr > 60], hr_pctile)
    in_climb = (grade >= min_grade) & (hr >= hr_thr)
    return in_climb


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
        # mark anything past 67% of the ride as not hard
        late_start = int(0.67 * n)
        out[late_start:] = False
    return out


def collect_outdoor_truth():
    pts = []
    for path in OUT_PATHS:
        rows = load_records(ROOT / path)
        climb_mask = find_climb_indices(rows)
        hard_mask = find_hard_indices(rows)
        keep = climb_mask | hard_mask
        for r, k in zip(rows, keep):
            if not k: continue
            if (r["hr"] and r["pw"] is not None and r["cd"]
                    and r["pw"] >= 10 and r["cd"] >= 30):
                pts.append((r["hr"], r["cd"], r["pw"]))
    return np.array(pts, dtype=float)


def collect_indoor():
    pts = []
    for path in IND_PATHS:
        rows = load_records(ROOT / path)
        for r in rows:
            if (r["hr"] and r["pw"] is not None and r["cd"]
                    and r["pw"] >= 10 and r["cd"] >= 30):
                pts.append((r["hr"], r["cd"], r["pw"]))
    return np.array(pts, dtype=float)


def build_ratio_points(out_pts, in_pts):
    """For each 5-rpm bin, compute the IC8/outdoor ratio at matched HR."""
    bins = list(range(45, 110, 5))
    cad_centers, ratios, weights = [], [], []
    for c in bins:
        lo, hi = c, c + 5
        m_o = (out_pts[:, 1] >= lo) & (out_pts[:, 1] < hi)
        if m_o.sum() < 20: continue
        # match HR: use IQR of outdoor HR in this cad bin
        hr_lo = np.percentile(out_pts[m_o, 0], 25)
        hr_hi = np.percentile(out_pts[m_o, 0], 75)
        m_i = ((in_pts[:, 1] >= lo) & (in_pts[:, 1] < hi)
               & (in_pts[:, 0] >= hr_lo) & (in_pts[:, 0] <= hr_hi))
        if m_i.sum() < 15: continue
        med_o = float(np.median(out_pts[m_o, 2]))
        med_i = float(np.median(in_pts[m_i, 2]))
        cad_centers.append(c + 2.5)
        ratios.append(med_i / med_o)
        # Weight by harmonic mean of sample counts (lower of the two limits us)
        w = 2.0 / (1.0 / m_o.sum() + 1.0 / m_i.sum())
        weights.append(w)
        print(f"  cad [{lo:>2},{hi:>2})  out n={m_o.sum():>4} med={med_o:>5.0f}W "
              f"HR{hr_lo:.0f}-{hr_hi:.0f}  "
              f"in n={m_i.sum():>4} med={med_i:>5.0f}W  ratio={med_i/med_o:.3f}")
    return np.array(cad_centers), np.array(ratios), np.array(weights)


def logistic(cad, floor, ceiling, midpoint, k):
    return floor + (ceiling - floor) / (1.0 + np.exp(-k * (cad - midpoint)))


def main():
    print("collecting outdoor truth pool (climbs + early/mid hard efforts)...")
    out = collect_outdoor_truth()
    print(f"  {len(out)} samples")
    print("collecting indoor IC8 active samples...")
    ind = collect_indoor()
    print(f"  {len(ind)} samples")

    print("\nratio per cadence bin:")
    cad, ratio, weight = build_ratio_points(out, ind)
    if len(cad) < 4:
        print("not enough bins to fit a curve")
        return

    # Fit logistic
    p0 = (0.80, 1.45, 70.0, 0.15)
    bounds = ([0.5, 1.0, 50, 0.05], [1.0, 2.0, 90, 2.0])
    popt, pcov = curve_fit(logistic, cad, ratio, p0=p0, bounds=bounds,
                           sigma=1.0/np.sqrt(weight), absolute_sigma=False)
    floor, ceiling, midpoint, k = popt
    print(f"\nlogistic fit:")
    print(f"  floor    = {floor:.3f}  (asymptote at low cadence)")
    print(f"  ceiling  = {ceiling:.3f} (asymptote at high cadence)")
    print(f"  midpoint = {midpoint:.1f} rpm  (transition center)")
    print(f"  k        = {k:.4f}  (steepness)")

    # Find the actual ratio=1 crossover from the fit
    # 1 = floor + (ceiling-floor)/(1+exp(-k*(c-mid)))
    # solve: c = mid - log((ceiling-floor)/(1-floor) - 1) / k
    cross = midpoint - np.log((ceiling - floor) / (1.0 - floor) - 1.0) / k
    print(f"  crossover (ratio=1) = {cross:.1f} rpm")

    # Print the curve at integer cadences
    print(f"\nfitted curve, cadence -> inflation factor:")
    print(f"  {'cad':>4} {'factor':>8}")
    for c in range(40, 121, 5):
        print(f"  {c:>4} {logistic(c, *popt):>8.3f}")

    # Residuals
    pred = logistic(cad, *popt)
    print(f"\nfit quality:")
    print(f"  weighted RMS residual (log space): "
          f"{np.sqrt(np.mean((ratio - pred)**2)):.4f}")
    print(f"  per-bin residuals:")
    for c, r, p in zip(cad, ratio, pred):
        print(f"    cad={c:.1f}: observed={r:.3f}, predicted={p:.3f}, "
              f"diff={r-p:+.3f}")


if __name__ == "__main__":
    main()
