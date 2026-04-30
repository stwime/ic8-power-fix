"""Fit the IC8's flywheel coastdown dynamics from BLE-recorded spin-downs.

Model:  I * dω/dt = -(c_brake * R + c_friction) * ω
        =>  ω(t) = ω₀ * exp(-λ(R) * t),  λ(R) = (c_brake*R + c_friction)/I

We measure λ at each R from log-linear fit on a coastdown segment, then
fit λ(R) = f(R) across coastdowns. Three forms are compared:
  * linear        λ(R) = a·R + b
  * saturating    λ(R) = α·(1 − exp(−R/R_c)) + β
  * Hill          λ(R) = α·R^p / (R^p + R_c^p) + β   (physics-derived)

Power dissipated at steady state (no rider acceleration):
    P = (c_brake*R + c_friction) * ω² = λ(R) * I * ω²
where ω is in rad/s. So once I is pinned (one outdoor anchor point),
the brake is fully characterized.

Sources: structured spin-down sessions in data/calibration/. We fit on
per-revolution crank-event timestamps from CSC, not the 1 Hz BLE-row
timestamp_s. Each FTMS row carries the most-recent crank-event time as
reported by the CSC characteristic at 1/1024 s resolution; that is the
actual time the rev happened. timestamp_s is the BLE-notification arrival
time and is misaligned from the rev event by up to half a sample interval
(~0.5 s of jitter). At low R that averages out across 30+ samples and the
log-linear fit is robust; at high R where the whole coastdown lasts ~3 s,
the timing jitter dominates and r² drops to 0.85-0.95. Per-revolution
fitting eliminates this — the time axis is locked to the rev events
themselves.

For each FTMS row in a segment, parse_nrf_log.py reports cumulative
crank_revs and crank_event_time_s. Within a segment we take the unique
strictly-increasing (crank_revs, crank_event_time_s) tuples; for each
consecutive pair we have an inter-rev interval cadence value
    cad_i = 60 · (ΔN_i / Δt_i) rpm
which is the EXACT mean cadence over [t_{i-1}, t_i] and (under exponential
decay) equals the instantaneous cadence at the interval midpoint to
O((λΔt)²/24) — negligible. Log-linear regression of ln(cad_i) vs the
interval midpoint recovers λ.

Two segments are flagged BAD and dropped (rider brushed a pedal mid-coastdown):
  * Apr 29 R=33 (only R=33 segment that session — anomalous λ vs other R≈30s)
  * Apr 30 first R=11 (three R=11 segments; the first is anomalous vs the
    next two)
"""
from pathlib import Path
import csv
import numpy as np

ROOT = Path(__file__).parent.parent
SOURCES = [
    ROOT / "data/calibration/spin_downs_apr29.csv",
    ROOT / "data/calibration/spin_downs_apr30.csv",
    ROOT / "data/calibration/spin_downs_super_high_r.csv",
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


def _wheel_revs(row):
    v = row.get("wheel_revs", "")
    if v is None or v == "":
        return None
    try:
        return int(v)
    except ValueError:
        return None


def _wheel_stopped_after(rows, start_idx, wr_anchor, lookahead=2):
    """Return True if wheel_revs stays at or below ``wr_anchor`` across the next
    ``lookahead`` valid (non-blank) samples starting at ``start_idx``.

    This is the canonical wheel-stop signal: the cumulative rotation counter
    from CSC stops advancing because no full rotation completes in the BLE
    notification window. We require ≥2 consecutive samples of no advance to
    rule out the slow-rotation regime where one rev might span > 1 sample
    interval but the wheel is still turning.
    """
    if wr_anchor is None:
        return False
    seen = 0
    idx = start_idx
    while idx < len(rows) and seen < lookahead:
        wr = _wheel_revs(rows[idx])
        if wr is None:
            idx += 1
            continue
        if wr > wr_anchor:
            return False
        seen += 1
        idx += 1
    return seen >= lookahead


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

    Returns list of (segment_rows, R0, terminator) where terminator is
    one of: "wheel_stop"   — next row has FTMS cad = 0 (flywheel halted
                              between samples; CSC reports nothing
                              because no rotations completed),
            "rider_repedal" — CSC started increasing again,
            "R_changed"    — resistance dial moved beyond jitter,
            "csc_blank"    — CSC went blank but FTMS > 0 (BLE blip or
                              partial sample drop, not a true stop),
            "end_of_data"  — segment ran to the end of the file.
    Wheel_stop is the high-R analogue of "rode it down to the floor": the
    flywheel decelerates so fast at high R that it stops in less than one
    BLE sample interval, so CSC's last live reading is well above the floor
    even though the coastdown is in fact complete.
    """
    segs = []
    i = 0
    while i < len(rows) - min_samples:
        c0 = _csc(rows[i])
        if c0 is None or c0 < min_cad_start:
            i += 1; continue
        R0 = int(rows[i]["resistance"])
        j = i
        terminator = "end_of_data"
        while j + 1 < len(rows):
            c_next = _csc(rows[j+1])
            R_next = int(rows[j+1]["resistance"])
            c_curr = _csc(rows[j])
            if c_next is None or c_curr is None:
                wr_anchor = _wheel_revs(rows[j])
                if _wheel_stopped_after(rows, j+1, wr_anchor):
                    terminator = "wheel_stop"
                else:
                    terminator = "csc_blank"
                break
            if c_next >= c_curr + flat_tol:
                terminator = "rider_repedal"
                break
            if abs(R_next - R0) > r_jitter_max:
                terminator = "R_changed"
                break
            j += 1
        if j - i + 1 >= min_samples:
            seg = rows[i:j+1]
            segs.append((seg, R0, terminator))
        i = j + 1 if j > i else i + 1
    return segs


def _crank_rev_obs(seg):
    """Return the strictly-increasing (crank_revs, crank_event_time_s) pairs
    in ``seg``. Each pair is one revolution event reported by CSC and timed
    to 1/1024 s. Adjacent FTMS rows that repeat the same pair (no new rev
    arrived in that BLE window) are deduplicated."""
    obs = []
    for r in seg:
        nv = r.get("crank_revs", "")
        tv = r.get("crank_event_time_s", "")
        if nv in (None, "") or tv in (None, ""):
            continue
        try:
            n = int(nv); t = float(tv)
        except (ValueError, TypeError):
            continue
        if obs and (n <= obs[-1][0] or t <= obs[-1][1] + 1e-6):
            continue
        obs.append((n, t))
    return obs


def fit_decay(seg):
    """Per-revolution log-linear fit of ω(t) = ω₀ exp(-λ t).

    Returns (lam, r2, n_intervals, cad_hi, cad_lo, dur_s) — all derived from
    the per-revolution observations within the segment, not from the 1 Hz
    cadence_rpm_csc column. Returns None when there are too few rev events
    in the segment to fit (need ≥ 3 distinct rev observations = 2 intervals
    for a slope, 3 intervals for a defined r²).
    """
    obs = _crank_rev_obs(seg)
    if len(obs) < 4:
        return None
    revs = np.array([o[0] for o in obs], dtype=float)
    et = np.array([o[1] for o in obs])
    d_revs = np.diff(revs)
    dt = np.diff(et)
    cad = 60.0 * d_revs / dt
    t_mid = 0.5 * (et[:-1] + et[1:])
    y = np.log(cad)
    A = np.vstack([t_mid, np.ones_like(t_mid)]).T
    sl, ic = np.linalg.lstsq(A, y, rcond=None)[0]
    lam = -sl
    pred = sl * t_mid + ic
    r2 = 1 - np.sum((y - pred)**2) / max(np.sum((y - y.mean())**2), 1e-12)
    return (lam, r2, len(cad),
            float(cad[0]), float(cad[-1]),
            float(et[-1] - et[0]))


def collect_segments():
    """Return list of segment dicts across all sources, including all
    diagnostics needed for weighting and bucket diagnostics in main().
    n / c0 / c1 / dur are derived from per-revolution observations within
    each segment, not from the 1 Hz BLE rows."""
    results = []
    for src in SOURCES:
        label = src.stem.replace("spin_downs_", "")  # "apr29" or "apr30"
        rows = list(csv.DictReader(src.open()))
        segs = find_clean_coastdowns(rows)
        per_R_count = {}
        for seg, R, terminator in segs:
            occ = per_R_count.get(R, 0)
            per_R_count[R] = occ + 1
            fit = fit_decay(seg)
            if fit is None:
                # Not enough rev events to fit — drop silently.
                results.append({
                    "label": label, "R": R, "occ": occ,
                    "lam": float("nan"), "r2": float("nan"),
                    "n": 0, "c0": float("nan"), "c1": float("nan"),
                    "dur": 0.0, "bad": False, "keep": False,
                    "term": terminator, "drop_reason": "few_revs",
                })
                continue
            lam, r2, n, c0, c1, dur = fit
            bad = (label, R, occ) in BAD_SEGMENTS
            # Reached-floor test on the per-rev cadence: the last cadence we
            # measured is the gap between the final two rev events, which is
            # close to the instantaneous floor.
            reached_floor = (c1 < 25) or (terminator == "wheel_stop")
            # We deliberately do NOT gate on r² here: r² of a log-linear fit
            # measures how exponential the decay is, which is the model's own
            # assumption. Filtering on it censors exactly the data that would
            # falsify the linear-viscous model. r² stays in the diagnostic
            # output instead, and per-segment weighting handles fit precision.
            keep = reached_floor and not bad
            results.append({
                "label": label, "R": R, "occ": occ,
                "lam": lam, "r2": r2, "n": n,
                "c0": c0, "c1": c1, "dur": dur,
                "bad": bad, "keep": keep, "term": terminator,
                "drop_reason": "" if keep else
                    ("bad" if bad else "noFloor"),
            })
    return results


def main():
    rows_out = collect_segments()
    print(f"sources: {[s.name for s in SOURCES]}")
    print(f"\nper-segment fits (per-revolution log-linear, "
          f"n = inter-rev intervals; r² is on ln(cad) vs t_mid):")
    print(f"{'sess':>5} {'R':>3} {'occ':>3} {'n':>3} {'cad_hi':>6} {'cad_lo':>6} "
          f"{'dur_s':>6} {'λ_per_s':>9} {'r²':>6} {'term':>14} flag")
    for r in rows_out:
        if r["drop_reason"] == "few_revs":
            print(f"{r['label']:>5} {r['R']:>3} {r['occ']:>3} {'-':>3} "
                  f"{'-':>6} {'-':>6} {'-':>6} "
                  f"{'-':>9} {'-':>6} {r['term']:>14}  fewRevs")
            continue
        if r["bad"]:
            flag = "BAD"
        elif r["keep"]:
            flag = "ok"
        else:
            flag = "noFloor"
        print(f"{r['label']:>5} {r['R']:>3} {r['occ']:>3} {r['n']:>3} "
              f"{r['c0']:>6.0f} {r['c1']:>6.0f} {r['dur']:>6.1f} "
              f"{r['lam']:>9.4f} {r['r2']:>6.3f} {r['term']:>14}  {flag}")

    keep = [r for r in rows_out if r["keep"]]
    R = np.array([r["R"] for r in keep], dtype=float)
    lam = np.array([r["lam"] for r in keep])
    n = np.array([r["n"] for r in keep])
    c0 = np.array([r["c0"] for r in keep])
    c1 = np.array([r["c1"] for r in keep])
    # Weight by sqrt(n) (sample count) and log(c0/c1) (dynamic range of the
    # decay we observed). A 125→10 segment carries more information about λ
    # than a 77→44 one even at the same n; the log-range factor reflects that.
    log_range = np.log(c0 / c1)
    w = np.sqrt(n) * log_range
    W = np.diag(w)
    A = np.vstack([R, np.ones_like(R)]).T
    (a, b), *_ = np.linalg.lstsq(W @ A, W @ lam, rcond=None)
    pred = a * R + b
    resid = lam - pred
    wrms = np.sqrt(np.sum(w**2 * resid**2) / np.sum(w**2))

    print(f"\npooled linear fit ({len(keep)} segments, all wheel_stop-terminated "
          f"or rode-down-to-floor; r² no longer gates):")
    print(f"  λ(R) = {a:.5f}·R + {b:.4f}  (per second)")
    print(f"  weighted RMS residual: {wrms:.4f} 1/s")
    print(f"  friction-only τ (R=0): {1/b:.1f} s")
    print(f"  brake/friction at R=50: {a*50/b:.2f}× friction")

    print(f"\nresiduals by R bucket (linear fit), to expose model misspecification:")
    print(f"  {'R range':>8} {'n':>3} {'mean λ':>8} {'pred λ':>8} {'mean resid':>11}")
    for lo, hi in [(0, 10), (10, 25), (25, 45), (45, 100)]:
        mask = (R >= lo) & (R < hi)
        if mask.sum() == 0: continue
        mean_lam = lam[mask].mean()
        mean_pred = pred[mask].mean()
        mean_resid = resid[mask].mean()
        print(f"  [{lo:>2},{hi:>3}) {mask.sum():>3} {mean_lam:>8.4f} {mean_pred:>8.4f} {mean_resid:>+11.4f}")

    # Saturating-λ(R) alternative: λ(R) = α·(1 - exp(-R/Rc)) + β.
    # At R=0 → β (residual). At R→∞ → α + β (saturation). Grid-search Rc;
    # solve (α, β) by weighted lstsq at each grid point.
    best = None
    for Rc in np.exp(np.linspace(np.log(2.0), np.log(500.0), 200)):
        u = 1 - np.exp(-R / Rc)
        A_sat = np.vstack([u, np.ones_like(u)]).T
        sol, *_ = np.linalg.lstsq(W @ A_sat, W @ lam, rcond=None)
        alpha, beta = sol
        pred_s = alpha * u + beta
        rss = float(np.sum(w**2 * (lam - pred_s)**2))
        if best is None or rss < best[0]:
            best = (rss, Rc, alpha, beta, pred_s)
    rss_s, Rc, alpha, beta, pred_s = best
    wrms_s = np.sqrt(rss_s / np.sum(w**2))
    rss_lin = float(np.sum(w**2 * resid**2))

    print(f"\nsaturating fit  λ(R) = α·(1 − e^(−R/R_c)) + β:")
    print(f"  α = {alpha:.4f}, β = {beta:.4f}, R_c = {Rc:.2f}")
    print(f"  weighted RMS residual: {wrms_s:.4f} 1/s")
    print(f"  saturation λ_∞ = α + β = {alpha+beta:.4f}")
    print(f"\nlinear vs saturating, weighted RSS: {rss_lin:.5f}  vs  {rss_s:.5f}  "
          f"({100*(rss_lin-rss_s)/rss_lin:+.1f}% change)")

    # Same R-bucket residual diagnostic but for the saturating fit. If the
    # mean residual is ~zero in every bucket, the one-knee shape is matched
    # to the data; systematic +/- signs across buckets would indicate the
    # need for a more flexible curve.
    resid_s = lam - pred_s
    print(f"\nresiduals by R bucket (saturating fit), to test for residual misspecification:")
    print(f"  {'R range':>8} {'n':>3} {'mean λ':>8} {'pred λ':>8} {'mean resid':>11} {'std resid':>10}")
    for lo, hi in [(0, 10), (10, 25), (25, 45), (45, 100)]:
        mask = (R >= lo) & (R < hi)
        if mask.sum() == 0: continue
        mean_lam = lam[mask].mean()
        mean_pred = pred_s[mask].mean()
        mean_resid = resid_s[mask].mean()
        std_resid = resid_s[mask].std()
        print(f"  [{lo:>2},{hi:>3}) {mask.sum():>3} {mean_lam:>8.4f} {mean_pred:>8.4f} "
              f"{mean_resid:>+11.4f} {std_resid:>10.4f}")

    # Hill-form alternative: λ(R) = α · R^p / (R^p + R_c^p) + β.
    # Physically motivated: B² ∝ 1/gap^p with p≈3-6 (dipole far-field) and
    # gap ∝ (1 - R/R_c) gives a power-law-in-(1-R/R_c) saturation. A Hill
    # function with the same R_c is a reparameterization that cleanly nests
    # both far-field (R << R_c) and near-field (R >> R_c) limits. p=1 is
    # Michaelis-Menten; physics suggests p>1.
    # Fit: 2D grid over (R_c, p) with linear LSQ for (α, β) at each grid pt.
    best_h = None
    for Rc_h in np.linspace(5.0, 120.0, 80):
        for p in np.linspace(1.0, 8.0, 71):
            u = R**p / (R**p + Rc_h**p)
            A_h = np.vstack([u, np.ones_like(u)]).T
            sol, *_ = np.linalg.lstsq(W @ A_h, W @ lam, rcond=None)
            ah, bh = sol
            pred_h = ah * u + bh
            rss = float(np.sum(w**2 * (lam - pred_h)**2))
            if best_h is None or rss < best_h[0]:
                best_h = (rss, Rc_h, p, ah, bh, pred_h)
    rss_h, Rc_h, p_h, ah, bh, pred_h = best_h
    wrms_h = np.sqrt(rss_h / np.sum(w**2))
    print(f"\nHill fit  λ(R) = α·R^p / (R^p + R_c^p) + β:")
    print(f"  α = {ah:.4f}, β = {bh:.4f}, R_c = {Rc_h:.2f}, p = {p_h:.2f}")
    print(f"  weighted RMS residual: {wrms_h:.4f} 1/s")
    print(f"  saturation λ_∞ = α + β = {ah+bh:.4f}")
    print(f"\nlinear vs sat vs Hill, weighted RSS: "
          f"{rss_lin:.5f}  vs  {rss_s:.5f}  vs  {rss_h:.5f}")
    print(f"  Hill cuts wRSS by {100*(rss_s-rss_h)/rss_s:+.1f}% over saturating, "
          f"{100*(rss_lin-rss_h)/rss_lin:+.1f}% over linear.")

    resid_h = lam - pred_h
    print(f"\nresiduals by R bucket (Hill fit):")
    print(f"  {'R range':>8} {'n':>3} {'mean λ':>8} {'pred λ':>8} {'mean resid':>11} {'std resid':>10}")
    for lo, hi in [(0, 10), (10, 25), (25, 45), (45, 100)]:
        mask = (R >= lo) & (R < hi)
        if mask.sum() == 0: continue
        mean_lam = lam[mask].mean()
        mean_pred = pred_h[mask].mean()
        mean_resid = resid_h[mask].mean()
        std_resid = resid_h[mask].std()
        print(f"  [{lo:>2},{hi:>3}) {mask.sum():>3} {mean_lam:>8.4f} {mean_pred:>8.4f} "
              f"{mean_resid:>+11.4f} {std_resid:>10.4f}")

    # Per-session sanity for the linear fit
    print(f"\nper-session agreement (linear, same weighting):")
    for label in sorted({r["label"] for r in keep}):
        sub = [r for r in keep if r["label"] == label]
        Rs = np.array([r["R"] for r in sub], float)
        ls = np.array([r["lam"] for r in sub])
        ns = np.array([r["n"] for r in sub])
        c0s = np.array([r["c0"] for r in sub])
        c1s = np.array([r["c1"] for r in sub])
        ws = np.sqrt(ns) * np.log(c0s / c1s)
        Ws = np.diag(ws)
        As = np.vstack([Rs, np.ones_like(Rs)]).T
        (ai, bi), *_ = np.linalg.lstsq(Ws @ As, Ws @ ls, rcond=None)
        rmsi = np.sqrt(np.sum(ws**2 * (ls - (ai*Rs+bi))**2) / np.sum(ws**2))
        print(f"  {label}: a={ai:.5f}, b={bi:.4f}, n={len(sub)}, wRMS={rmsi:.4f}")

    print(f"\nphysics implication (linear, for backwards compatibility):")
    print(f"  P_true(R, cad) = ({a:.5f}·R + {b:.4f}) · I · (cad·π/30)² watts")
    print(f"                 = ({a:.5f}·R + {b:.4f}) · I · cad² · 0.01097")


if __name__ == "__main__":
    main()
