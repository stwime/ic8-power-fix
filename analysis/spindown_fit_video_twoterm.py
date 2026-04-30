"""Two-term spin-down fit: ω̇ = -λω - τ₀  (linear viscous + Coulomb).

Why:
  Single-exponential ω(t) = ω₀·exp(-λt) fits low/mid R video spindowns
  perfectly. At high R the video shows a clear concave-down bend on a
  log-y rpm plot — decay rate increases as ω shrinks. That's the
  signature of a constant-torque term added to the viscous brake.

Model:
  ω̇ = -λ·ω - τ₀
  ω(t) = (ω₀ + τ₀/λ)·exp(-λt) - τ₀/λ
  θ(t) - θ(0) = (ω₀/λ + τ₀/λ²)·(1 - exp(-λt)) - (τ₀/λ)·t

Reparameterised for fitting on cumulative angle:
  θ(t) = θ₀ + A·(1 - e^(-λt)) - B·t
  with  A = ω₀/λ + τ₀/λ²,  B = τ₀/λ
  recovered: τ₀ = B·λ,  ω₀ = (A - B/λ)·λ = A·λ - B

Strategy:
  1. Per-segment NLLS in (λ, A, B, θ₀). Fit on the same cumulative
     angle stream the single-exp fit uses. A free τ₀ per segment lets
     us check across-R consistency.
  2. If τ₀ = B·λ is roughly constant across segments → it's a
     bike-wide property; refit globally with shared τ₀ and per-segment
     (λ, ω₀, θ₀).
  3. Print both per-segment τ₀ values and the global fit.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from scipy.optimize import least_squares

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_nrf_log import parse_log  # noqa: E402
from spindown_fit import find_clean_coastdowns, fit_decay  # noqa: E402
from spindown_fit_video import (LAG, LOG, VIDEO_CSV,
                                integrate_to_cumulative,
                                load_video_modpi)  # noqa: E402

HUBER_DELTA = 0.15  # rad


def segment_video_window(seg, t_v, ang_v):
    """Wall-clock window starting at second CSC rev event → video frames."""
    E0 = T0 = E1 = None
    for r in seg:
        ts = r["timestamp_s"]; et = r.get("crank_event_time_s")
        if ts is None or et is None:
            continue
        if E0 is None:
            E0, T0 = float(et), float(ts)
        elif float(et) > E0 + 1e-6:
            E1 = float(et); break
    t0_log = (E1 + (T0 - E0)) if E1 is not None else seg[0]["timestamp_s"]
    t1_log = seg[-1]["timestamp_s"]
    tv0 = t0_log - LAG
    tv1 = t1_log - LAG
    m = (t_v >= tv0) & (t_v <= tv1)
    return t_v[m], ang_v[m]


def fit_two_term_segment(t, cum, lam0, omega0):
    """Per-segment fit of θ(t) = θ₀ + A(1 - e^(-λt)) - B·t.
    Returns (lam, A, B, theta0, cost) or None on fit failure.
    The sign of the rotation can be either way — fit both signs and keep
    the better cost. Bounds on λ keep us out of the no-decay degeneracy.
    """
    if len(t) < 8:
        return None
    tt = t - t[0]

    def predicted(p):
        lam, A, B, off = p
        return off + A * (1 - np.exp(-lam * tt)) - B * tt

    def residuals(p):
        return predicted(p) - cum

    best = None
    for sign in (+1, -1):
        # Seed: ω₀ ≈ sign·|omega0|, τ₀ ≈ 0 → A ≈ ω₀/λ, B ≈ 0.
        A0 = sign * abs(omega0) / max(lam0, 1e-3)
        x0 = np.array([lam0, A0, 0.0, float(cum[0])])
        try:
            res = least_squares(
                residuals, x0, loss="huber", f_scale=HUBER_DELTA,
                max_nfev=600,
                bounds=([1e-3, -500.0, -50.0, -1e6],
                        [3.0,   500.0,  50.0,  1e6]),
            )
        except Exception:
            continue
        cost = float(np.sum(res.fun**2))
        if best is None or cost < best[-1]:
            best = (res.x[0], res.x[1], res.x[2], res.x[3], cost)
    return best


def fit_one_term_segment(t, cum, lam0, omega0):
    """Same as fit_segment_video but inlined here for direct comparison
    cost vs the two-term fit on the same cumulative-angle vector."""
    if len(t) < 8:
        return None
    tt = t - t[0]

    def predicted(p):
        lam, w0, off = p
        return off + (w0 / lam) * (1 - np.exp(-lam * tt))

    def residuals(p):
        return predicted(p) - cum

    best = None
    for sign in (+1, -1):
        x0 = np.array([lam0, sign * abs(omega0), float(cum[0])])
        try:
            res = least_squares(
                residuals, x0, loss="huber", f_scale=HUBER_DELTA,
                max_nfev=400,
                bounds=([1e-3, -50, -1e6], [3.0, 50, 1e6]),
            )
        except Exception:
            continue
        cost = float(np.sum(res.fun**2))
        if best is None or cost < best[-1]:
            best = (res.x[0], res.x[1], res.x[2], cost)
    return best


def main():
    rows = parse_log(LOG)
    segs = find_clean_coastdowns(rows)
    t_v, ang_v = load_video_modpi(VIDEO_CSV)

    print(f"{'R':>3} {'occ':>3} {'lam_c':>7} {'lam1':>7} {'lam2':>7} "
          f"{'tau0':>8} {'cost1':>9} {'cost2':>9} "
          f"{'Δrss%':>7} {'n':>5}")
    rows_out = []
    per_R = {}
    for seg, R, term in segs:
        occ = per_R.get(R, 0); per_R[R] = occ + 1
        if R == 0 and occ == 0:
            continue  # R-changed terminator
        fcsc = fit_decay(seg)
        if fcsc is None:
            continue
        lam_c, _r2_c, _n_csc, c0, _c1, _dur = fcsc
        tt, aa = segment_video_window(seg, t_v, ang_v)
        if len(tt) < 12:
            continue
        cum = integrate_to_cumulative(aa)
        omega0_seed = c0 * 2 * math.pi / 60

        f1 = fit_one_term_segment(tt, cum, lam0=max(lam_c, 0.01),
                                  omega0=omega0_seed)
        f2 = fit_two_term_segment(tt, cum, lam0=max(lam_c, 0.01),
                                  omega0=omega0_seed)
        if f1 is None or f2 is None:
            continue
        lam1, w0_1, off1, c1_cost = f1
        lam2, A2, B2, off2, c2_cost = f2
        tau0 = B2 * lam2  # the Coulomb term in rad/s² (= τ_C/I)
        improvement = (c1_cost - c2_cost) / max(c1_cost, 1e-12) * 100
        print(f"{R:>3} {occ:>3} {lam_c:>7.3f} {abs(lam1):>7.3f} "
              f"{abs(lam2):>7.3f} {tau0:>+8.3f} {c1_cost:>9.4f} "
              f"{c2_cost:>9.4f} {improvement:>+7.1f} {len(tt):>5}")
        rows_out.append({
            "R": R, "occ": occ, "lam_c": lam_c,
            "lam1": abs(lam1), "lam2": abs(lam2), "tau0": tau0,
            "cost1": c1_cost, "cost2": c2_cost, "n": len(tt),
            "tt": tt, "cum": cum, "off2": off2, "A2": A2, "B2": B2,
        })

    if not rows_out:
        sys.exit("no segments fit")

    # τ₀ summary by R bucket. If τ₀ is bike-wide (no R dependence), the
    # weighted mean across all segments is the answer.
    print("\nτ₀ (B·λ in rad/s²) by R bucket:")
    print(f"  {'R range':>10} {'n':>3} {'mean τ₀':>10} {'median':>10} "
          f"{'std':>8}")
    R_arr = np.array([r["R"] for r in rows_out])
    tau_arr = np.array([r["tau0"] for r in rows_out])
    for lo, hi in [(0, 10), (10, 25), (25, 45), (45, 70), (70, 100)]:
        m = (R_arr >= lo) & (R_arr < hi)
        if m.sum() == 0:
            continue
        print(f"  [{lo:>2},{hi:>3}) {m.sum():>3} "
              f"{tau_arr[m].mean():>+10.4f} "
              f"{np.median(tau_arr[m]):>+10.4f} "
              f"{tau_arr[m].std():>8.4f}")
    print(f"  {'all':>10} {len(tau_arr):>3} "
          f"{tau_arr.mean():>+10.4f} {np.median(tau_arr):>+10.4f} "
          f"{tau_arr.std():>8.4f}")

    # Global fit: shared τ₀, per-segment (λ_i, A_i, off_i).
    # θ_i(t) = off_i + A_i·(1 - e^(-λ_i·t)) - (τ₀/λ_i)·t
    n_seg = len(rows_out)
    # Pack: [τ₀, λ_1, A_1, off_1, λ_2, A_2, off_2, ...]
    x0 = np.zeros(1 + 3 * n_seg)
    x0[0] = float(np.median(tau_arr[tau_arr > 0])) if (tau_arr > 0).any() else 0.0
    if x0[0] <= 0:
        x0[0] = 0.05  # small positive seed
    for i, r in enumerate(rows_out):
        # Seed each segment with its single-exp fit.
        sign = np.sign(r["A2"]) if r["A2"] != 0 else 1.0
        x0[1 + 3 * i + 0] = r["lam1"]
        x0[1 + 3 * i + 1] = sign * abs(r["A2"])
        x0[1 + 3 * i + 2] = r["off2"]

    # Pre-compute per-segment vectors for vectorized residuals.
    cums = [r["cum"] for r in rows_out]
    tts = [r["tt"] - r["tt"][0] for r in rows_out]

    def all_residuals(p):
        tau0 = p[0]
        out = []
        for i, (tt, cum) in enumerate(zip(tts, cums)):
            lam = p[1 + 3 * i + 0]
            A = p[1 + 3 * i + 1]
            off = p[1 + 3 * i + 2]
            B = tau0 / max(lam, 1e-6)
            sign = np.sign(A) if A != 0 else 1.0
            pred = off + A * (1 - np.exp(-lam * tt)) - sign * B * tt
            out.append(pred - cum)
        return np.concatenate(out)

    lo = [0.0]; hi = [5.0]
    for _ in range(n_seg):
        lo += [1e-3, -500.0, -1e6]
        hi += [3.0,   500.0,  1e6]
    try:
        glob = least_squares(all_residuals, x0, loss="huber",
                             f_scale=HUBER_DELTA, max_nfev=2000,
                             bounds=(lo, hi))
    except Exception as e:
        print(f"global fit failed: {e}")
        return

    tau0_glob = float(glob.x[0])
    print(f"\nglobal fit (shared τ₀ across all {n_seg} segments):")
    print(f"  τ₀ = {tau0_glob:.4f} rad/s²")
    print(f"  cost (Σ residual²): {float(np.sum(glob.fun**2)):.4f}")
    print(f"  per-segment λ from global fit:")
    print(f"    {'R':>3} {'occ':>3} {'lam_global':>10} "
          f"{'lam_csc':>9} {'lam_video1':>11}")
    for i, r in enumerate(rows_out):
        lam_i = abs(float(glob.x[1 + 3 * i + 0]))
        print(f"    {r['R']:>3} {r['occ']:>3} {lam_i:>10.4f} "
              f"{r['lam_c']:>9.4f} {r['lam1']:>11.4f}")

    # Compare global fit's per-segment cost vs single-exp cost vs 4-param-per-seg.
    # Reconstruct single-exp cost per segment from f1 above.
    # Already printed; now show global per-segment residual.
    print(f"\nper-segment residual cost: single-exp vs free-τ₀ vs shared-τ₀:")
    print(f"  {'R':>3} {'occ':>3} {'cost1':>9} {'cost2_free':>11} "
          f"{'cost2_glob':>11}")
    # Recompute cost per segment in the global fit.
    seg_lengths = [len(c) for c in cums]
    bounds = np.cumsum([0] + seg_lengths)
    glob_resid = glob.fun
    for i, r in enumerate(rows_out):
        rg = glob_resid[bounds[i]:bounds[i + 1]]
        cost_g = float(np.sum(rg**2))
        print(f"  {r['R']:>3} {r['occ']:>3} {r['cost1']:>9.4f} "
              f"{r['cost2']:>11.4f} {cost_g:>11.4f}")


if __name__ == "__main__":
    main()
