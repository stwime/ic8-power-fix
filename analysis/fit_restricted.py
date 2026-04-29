"""Fit cadence correction restricted to the cadence range where outdoor data
is dense (50-85 rpm). Above that, the rider rarely rides outdoor, and what
samples exist are selection-biased (sprints/descents).

Data sources combined:
  - climb segments (≥3% grade, top-30% HR)
  - 3-5 min stable-power segments (CV < 0.15, mean ≥ 150W)
  - early/mid 2-min hard efforts (rolling-2min ≥180W)

All from outdoor rides only. All restricted to first 67% of each ride
to avoid fatigue-induced HR drift.
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
            rows.append({
                "hr": d.get("heart_rate"), "pw": d.get("power"),
                "cd": d.get("cadence"),
                "alt": d.get("enhanced_altitude") or d.get("altitude"),
                "dist": d.get("distance"),
            })
    return rows


def stable_indices(rows, window=240, max_cv=0.15, min_mean=150):
    """Find samples inside 4-min windows of stable power."""
    pw = np.array([r["pw"] if r["pw"] is not None else 0 for r in rows],
                  dtype=float)
    n = len(pw)
    keep = np.zeros(n, dtype=bool)
    for i in range(n):
        lo = max(0, i - window // 2)
        hi = min(n, i + window // 2)
        seg = pw[lo:hi]
        if len(seg) < window * 0.8:
            continue
        m = seg.mean()
        if m < min_mean:
            continue
        # Coefficient of variation
        cv = seg.std() / m if m > 0 else 99
        if cv < max_cv:
            keep[i] = True
    return keep


def climb_indices(rows, min_grade=3.0, hr_pctile=70):
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


def hard_indices(rows, min_avg_pw=180, win=120, min_dur=120):
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
    return out


def collect_outdoor():
    pts, source = [], []
    for path in OUT_PATHS:
        rows = load_records(ROOT / path)
        n = len(rows)
        s_mask = stable_indices(rows)
        c_mask = climb_indices(rows)
        h_mask = hard_indices(rows)
        # Drop late tier (>67% into ride)
        keep = (s_mask | c_mask | h_mask)
        keep[int(0.67 * n):] = False

        per_source = {"stable": 0, "climb": 0, "hard": 0}
        for i, r in enumerate(rows):
            if not keep[i]: continue
            if (r["hr"] and r["pw"] is not None and r["cd"]
                    and r["pw"] >= 10 and r["cd"] >= 30):
                tag = ("stable" if s_mask[i] else
                       "climb" if c_mask[i] else "hard")
                pts.append((r["hr"], r["cd"], r["pw"]))
                source.append(tag)
                per_source[tag] += 1
        print(f"  {Path(path).name}: stable={per_source['stable']} "
              f"climb={per_source['climb']} hard={per_source['hard']}")
    return np.array(pts, dtype=float), np.array(source)


def collect_indoor():
    pts = []
    for path in IND_PATHS:
        rows = load_records(ROOT / path)
        for r in rows:
            if (r["hr"] and r["pw"] is not None and r["cd"]
                    and r["pw"] >= 10 and r["cd"] >= 30):
                pts.append((r["hr"], r["cd"], r["pw"]))
    return np.array(pts, dtype=float)


def main():
    print("collecting outdoor truth (stable + climb + hard, early/mid only)...")
    out, src = collect_outdoor()
    print(f"  total: {len(out)} samples")
    print(f"  source breakdown: {dict(zip(*np.unique(src, return_counts=True)))}")

    ind = collect_indoor()
    print(f"\nindoor IC8 active samples: {len(ind)}")

    # Per-cad-bin ratios, restricted to cad 50-85
    print(f"\nratio per cadence bin (restricted to 50-85 rpm):")
    cads, ratios, weights = [], [], []
    print(f"  {'bin':>10} {'n_out':>6} {'med_out':>7} {'HR_band':>10} "
          f"{'n_in':>6} {'med_in':>7} {'ratio':>6}")
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
        cads.append(c + 2.5)
        ratios.append(med_i / med_o)
        w = 2.0 / (1.0 / m_o.sum() + 1.0 / m_i.sum())
        weights.append(w)
        print(f"  [{lo:>2},{hi:>2})    {m_o.sum():>6} {med_o:>7.0f} "
              f"{hr_lo:>3.0f}-{hr_hi:<3.0f}  {m_i.sum():>6} {med_i:>7.0f} "
              f"{med_i/med_o:>6.2f}")

    cads = np.array(cads); ratios = np.array(ratios); weights = np.array(weights)
    if len(cads) < 4:
        print("not enough bins")
        return

    # Pure power law in cadence: log(ratio) linear in log(cad)
    log_cad = np.log(cads); log_ratio = np.log(ratios)
    W = weights / weights.sum()
    x_mean = (W * log_cad).sum()
    y_mean = (W * log_ratio).sum()
    cov = (W * (log_cad - x_mean) * (log_ratio - y_mean)).sum()
    var = (W * (log_cad - x_mean)**2).sum()
    b = cov / var
    log_a = y_mean - b * x_mean
    a = np.exp(log_a)
    cross = np.exp(-log_a / b)
    pred = a * cads**b
    log_resid = log_ratio - np.log(pred)
    rms = np.sqrt(np.mean(log_resid**2))

    print(f"\n=== power-law fit, cad ∈ [50, 85] ===")
    print(f"  ratio(cad) = (cad / {cross:.1f})^{b:.3f}")
    print(f"  log-RMS residual: {rms:.4f} ({100*rms:.1f}%)")
    print(f"  IC8 cad exponent: 1.586")
    print(f"  ratio cad exponent: {b:.3f}  =>  implied true cad exp: {1.586-b:.3f}")

    print(f"\n  per-bin residuals:")
    for c, r, p in zip(cads, ratios, pred):
        print(f"    cad={c:.1f}: obs={r:.3f} pred={p:.3f} resid={r-p:+.3f}")

    print(f"\nfitted curve at integer cadences:")
    for c in range(50, 121, 5):
        f = a * c**b
        marker = " <- EXTRAPOLATION" if c > 85 else ""
        print(f"  cad={c:>3}: factor={f:.3f}{marker}")


if __name__ == "__main__":
    main()
