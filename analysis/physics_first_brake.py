"""Compute brake torque from measured geometry — no fitting of α or κ.

User-measured / inferred geometry (2026-05-10):
  Disk: aluminum, 23 cm outer radius, 0.5 cm thick (Al conductivity σ_Al).
  Magnets: 4 N42 neodymium cylinders, 2.5 cm diameter, in 2 sandwich pairs
           (one pair attracting through disk → field reinforces inside Al).
  Yoke:    steel bridge connects the back faces of both pairs; the pairs
           are anti-polar so flux forms one closed figure-8 loop through
           the bridge (textbook eddy-brake topology, designed for max
           drag per magnet).
  Axial gap: 3 mm magnet-face-to-disk-face, constant across R.
  R control: pivoting carrier swings magnet pairs from "barely outside the
             disk OD" at R=0 to "inside the OD by 1-2 cm" at R=100. The
             front pair sweeps further than the back pair.

What that means for the Wouterse model
--------------------------------------
B inside the disk is constant in R (gap doesn't change). What varies with R
is the overlap area between each magnet's pole face and the spinning disk,
plus the radial centroid of that overlap. The empirical Hill H(R) absorbs

    H(R)²  ≈  G(R) / G_max,   G(R) = Σᵢ A_overlap_i(R) · d_i²(R)

with i running over the two magnet pairs and d_i each pair's axis distance.

Translating between frames (gear ratio g = ω_fw / ω_crank = 4.5)
  τ_fw_linear(ω_fw) = σ · t · B²_disk · G(R) · C_coupling · ω_fw   (Wouterse-linear)
  τ_crank_linear(ω_crank) = g² · σ · t · B²_disk · G(R) · C_coupling · ω_crank

Match to the strict-Wouterse model at the crank:
  τ_crank_linear = 2·α·κ·H(R)² · ω_crank
  ⇒ 2·α·κ (at H=1)  =  g² · σ · t · B²_disk · G_max · C_coupling

Two physics inputs are not directly measured:
  B_disk     — set by the magnetic circuit (closed-loop with steel yoke
               vs. open-loop free pair). Computed below from N42 Br + pair
               geometry; sensitive to L_m (magnet axial length) and yoke
               quality.
  C_coupling — anti-polar pairs in close proximity carry shared figure-8
               eddy currents in the inter-pair region; naive Σᵢ A·d²
               assumes the pairs are independent. C_coupling ≈ 1.0 for
               well-separated pairs, growing toward ~1.3 for adjacent
               pairs (rough heuristic — only FEMM/COMSOL can give a real
               number).

Caveat on the strict-Wouterse α/κ "geometric invariant" claim
-------------------------------------------------------------
Strict Wouterse pins τ_max ∝ B² *and* ω_c ∝ 1/B², so α/κ = τ_max·ω_c
cancels the B and is geometry-only. That coupling is physically motivated
when R varies the *gap* (closing the gap changes both B and the eddy loop
inductance). In the IC8, R varies *overlap* at fixed gap, so B in the gap
is approximately R-independent and the strict coupling isn't well
motivated. The empirical κ fit is plausibly capturing eddy-loop-size
growth with overlap, not a B² scaling. Either way, the geometric α/κ
invariant should be treated as suggestive, not measured.
"""
from __future__ import annotations

import csv
import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parent.parent
ALL_SPINDOWNS_CSV = ROOT / "data/calibration/all_spindowns.csv"

# --- Physical constants --------------------------------------------------
SIGMA_AL = 3.5e7        # S/m
MU_0 = 4 * math.pi * 1e-7

# --- Geometry ------------------------------------------------------------
R_DISK = 0.230          # m, flywheel outer radius
T_DISK = 0.005          # m, disk thickness
# Magnet: measured 2.5–3 cm diameter, 4–5 mm thick. Use lower-bound radius
# as the default; sensitivity sweep covers the measured range.
A_MAG = 0.0125          # m, magnet radius (2.5 cm dia, lower bound)
L_MAG = 0.0045          # m, magnet axial length (4.5 mm, mid-range)
GAP_AXIAL = 0.003       # m, magnet face to disk face (constant in R)
GEAR = 4.5              # ω_flywheel / ω_crank
I_CRANK = 9.09          # kg·m², effective inertia at crank (matches fit_wouterse.py)

# Magnet center radial position (m) at R=0 and R=100, per pair.
# At R=0:  outer edge 1 mm beyond disk OD → center at R_DISK + A_MAG + 1 mm.
# At R=100 (user 2026-05-10): back pair outer edge exactly at disk OD; front
#           pair outer edge ~0.7 cm inside OD. Note back pair is right at
#           the full-overlap threshold (d = R_DISK − A_MAG = 21.75 cm).
R_FRONT_R0 = R_DISK + A_MAG + 0.001    # 0.2445
R_FRONT_R100 = R_DISK - 0.007 - A_MAG  # 0.2105
R_BACK_R0 = R_DISK + A_MAG + 0.001     # 0.2445
R_BACK_R100 = R_DISK - A_MAG           # 0.2175

# --- Magnetics -----------------------------------------------------------
# B inside the Al disk depends on whether the brake has a steel yoke
# closing the magnetic circuit.
#
# Open loop (no yoke, free-air return). Each pair operates with substantial
# leakage; effective gap B is ~0.4–0.5 T for N42 at 11 mm pole-to-pole.
#
# Closed loop (anti-polar pairs + steel yoke). Flux returns through low-
# reluctance steel. Working-point B in each gap is given by the magnetic-
# circuit load line:
#
#     B_gap  =  Br / (1 + μ_r · L_gap / (2 · L_m))
#
# derived from Ampere's law around the figure-8 loop with negligible yoke
# reluctance and equal cross-sections (4 magnets, 2 gaps). The factor of
# 2·L_m in the denominator comes from each gap "seeing" two magnets in
# series in the closed loop.
BR_N42 = 1.3            # T (remanence)
MU_R_MAGNET = 1.05      # NdFeB recoil permeability

# Total magnetic-path length per gap (3 mm air + 5 mm Al disk + 3 mm air).
L_GAP_TOTAL = GAP_AXIAL + T_DISK + GAP_AXIAL    # 0.011 m


def B_gap_open(B_estimate: float = 0.45) -> float:
    """Open-loop B in the disk gap (no yoke). Pinned heuristic."""
    return B_estimate


def B_gap_yoked(L_m: float,
                L_gap: float = L_GAP_TOTAL,
                mu_r: float = MU_R_MAGNET,
                Br: float = BR_N42) -> float:
    """Closed-loop B in each gap with steel back-iron yoke.

    Magnetic circuit: 4 N42 magnets in series providing MMF, 2 air-gap
    stacks in series (one per pair), yoke reluctance ≈ 0. By symmetry
    each pair operates at the same load line as a single pair with
    its own dedicated yoke. Saturates at Br as L_m / L_gap → ∞.
    """
    return Br / (1.0 + mu_r * L_gap / (2.0 * L_m))


def magnet_center(R: float, pair: str) -> float:
    """Radial position of magnet center, m. Linear interpolation in R."""
    if pair == "front":
        r0, r100 = R_FRONT_R0, R_FRONT_R100
    elif pair == "back":
        r0, r100 = R_BACK_R0, R_BACK_R100
    else:
        raise ValueError(pair)
    return r0 + (r100 - r0) * R / 100.0


def lens_area(d: float, a: float, R_disk: float) -> float:
    """Overlap area between a circle of radius `a` centered at distance `d`
    from the disk center, and the disk of radius `R_disk`."""
    if d >= R_disk + a:
        return 0.0
    if d <= R_disk - a:
        return math.pi * a * a
    d2, a2, R2 = d * d, a * a, R_disk * R_disk
    arg1 = (d2 + a2 - R2) / (2 * d * a)
    arg2 = (d2 + R2 - a2) / (2 * d * R_disk)
    arg1 = max(-1.0, min(1.0, arg1))
    arg2 = max(-1.0, min(1.0, arg2))
    sq_arg = (-d + a + R_disk) * (d + a - R_disk) * (d - a + R_disk) * (d + a + R_disk)
    sq_arg = max(sq_arg, 0.0)
    return a2 * math.acos(arg1) + R2 * math.acos(arg2) - 0.5 * math.sqrt(sq_arg)


def G_of_R(R: float) -> float:
    """Σᵢ A_overlap_i(R) · d_i²(R), m⁴."""
    total = 0.0
    for pair in ("front", "back"):
        d = magnet_center(R, pair)
        A = lens_area(d, A_MAG, R_DISK)
        total += A * d * d
    return total


def physics_2alpha_kappa(B_disk: float, G_max: float,
                         C_coupling: float = 1.0) -> float:
    """2·α·κ at H=1, derived from physics. Units: N·m·s/rad.

    C_coupling >1 captures anti-polar adjacent-pair drag enhancement
    above the naive Σ A·d² superposition. 1.0 = no coupling (independent
    pairs); ~1.3 = typical for tight anti-polar spacing.
    """
    return GEAR * GEAR * SIGMA_AL * T_DISK * B_disk * B_disk * G_max * C_coupling


# Fitted 2ακ from analysis/fit_wouterse.py (α=165, κ=0.160). Reference
# point for any geometric prediction.
FITTED_2AK = 2.0 * 165.0 * 0.160    # 52.8 N·m·s/rad


def compare_to_fit(label: str, two_ak: float) -> str:
    ratio = two_ak / FITTED_2AK
    return f"{label:<38}  2ακ = {two_ak:>6.1f}   ratio {ratio:>5.2f}×  fitted"


# --- Data helpers --------------------------------------------------------

def collect_segments():
    by_id: dict[int, dict] = defaultdict(lambda: {"t": [], "omega": []})
    with ALL_SPINDOWNS_CSV.open() as f:
        for row in csv.DictReader(f):
            sid = int(row["id"])
            s = by_id[sid]
            if "R" not in s:
                s["R"] = int(row["R"])
                s["occ"] = int(row["occ"])
                s["method"] = row["method"]
            s["t"].append(float(row["t_s"]))
            s["omega"].append(float(row["omega_rad_s"]))
    out = []
    for sid in sorted(by_id):
        s = by_id[sid]
        t = np.asarray(s["t"], dtype=float)
        om = np.asarray(s["omega"], dtype=float)
        if len(t) < 4 or (om <= 0).any():
            continue
        order = np.argsort(t)
        s["t"] = t[order] - t[order][0]
        s["omega"] = om[order]
        out.append(s)
    return out


def per_segment_lambda(segments):
    pts = []
    for s in segments:
        t, om = s["t"], s["omega"]
        if len(t) < 4:
            continue
        dt = t - t.mean()
        dy = np.log(om) - np.log(om).mean()
        S_tt = float((dt * dt).sum())
        if S_tt < 1e-9:
            continue
        slope = float((dt * dy).sum()) / S_tt
        lam = -slope
        if lam <= 0:
            continue
        pts.append({"R": s["R"], "lam": lam})
    return pts


# --- Main ----------------------------------------------------------------

def main():
    if ALL_SPINDOWNS_CSV.exists():
        segments = collect_segments()
        pts = per_segment_lambda(segments)
        r0_lams = [p["lam"] for p in pts if p["R"] == 0]
        beta = float(np.mean(r0_lams)) if r0_lams else 0.0389
    else:
        print(f"note: {ALL_SPINDOWNS_CSV} not present; using fitted β=0.0389\n")
        pts = []
        beta = 0.0389    # fitted in analysis/fit_wouterse.py

    Rg = np.linspace(0.0, 100.0, 401)
    G = np.array([G_of_R(R) for R in Rg])
    G_max = G_of_R(100.0)
    H_geom = np.sqrt(np.divide(G, G_max, out=np.zeros_like(G), where=G_max > 0))

    print("=" * 76)
    print("Physics-first brake parameters")
    print("=" * 76)
    print(f"  Disk: R={R_DISK*100:.1f} cm, t={T_DISK*1000:.1f} mm")
    print(f"  Magnet: dia={2*A_MAG*100:.1f} cm, axial gap={GAP_AXIAL*1000:.1f} mm")
    print(f"  Pairs: 2, anti-polar with steel back-iron yoke (figure-8 flux loop)")
    print()
    print(f"  G(R=100) = {G_max:.4e} m⁴")
    print(f"  G(R=50)  = {G_of_R(50):.4e} m⁴")
    print(f"  G(R=30)  = {G_of_R(30):.4e} m⁴")
    print(f"  G(R=10)  = {G_of_R(10):.4e} m⁴")
    print()
    print(f"  β = {beta:.4f} 1/s")
    print(f"  Fitted reference (analysis/fit_wouterse.py): 2ακ = {FITTED_2AK:.2f}\n")

    # --- Scenario sweep ---------------------------------------------------
    # Open loop (no yoke): pinned B estimate.
    # Yoked: closed-loop circuit B for several magnet lengths.
    # Coupling factor C: 1.0 = naive superposition, 1.3 = adjacent anti-polar.
    print("Geometric predictions of 2ακ at H=1, ratio to fitted 52.8:")
    print("-" * 76)

    print(compare_to_fit(
        "open loop  B=0.40T  C=1.0",
        physics_2alpha_kappa(B_gap_open(0.40), G_max, 1.0)))
    print(compare_to_fit(
        "open loop  B=0.45T  C=1.0",
        physics_2alpha_kappa(B_gap_open(0.45), G_max, 1.0)))
    print(compare_to_fit(
        "open loop  B=0.50T  C=1.0",
        physics_2alpha_kappa(B_gap_open(0.50), G_max, 1.0)))
    print()

    # Measured magnet dimensions: 2.5–3 cm dia, 4–5 mm thick. Sweep both.
    print("Yoked, measured-dimension sweep (anti-polar pairs + steel yoke):")
    print(f"  {'dia (cm)':>9}  {'thick (mm)':>11}  {'B (T)':>6}  "
          f"{'2ακ':>7}  ratio")
    for dia_cm in (2.5, 2.75, 3.0):
        for L_m_mm in (4.0, 4.5, 5.0):
            a = dia_cm / 2.0 / 100.0
            L_m = L_m_mm / 1000.0
            # Rebuild G_max with this magnet radius (and adjusted carrier
            # positions, since R=100 was defined relative to A_MAG).
            r_front_R100 = R_DISK - 0.007 - a
            r_back_R100 = R_DISK - a
            # At R=100 both pairs fully inside the disc → A = π·a².
            A_pole = math.pi * a * a
            G_max_local = A_pole * (r_front_R100**2 + r_back_R100**2)
            B = B_gap_yoked(L_m)
            two_ak = physics_2alpha_kappa(B, G_max_local, 1.0)
            ratio = two_ak / FITTED_2AK
            print(f"  {dia_cm:>9.2f}  {L_m_mm:>11.1f}  {B:>6.3f}  "
                  f"{two_ak:>7.1f}  {ratio:>5.2f}×")
    print()

    # Back-solve: what (B, L_m) reproduces the fitted 2ακ exactly?
    needed_B2 = FITTED_2AK / (GEAR * GEAR * SIGMA_AL * T_DISK * G_max)
    needed_B = math.sqrt(needed_B2)
    # invert B_yoked: B = Br/(1 + mu_r L_g/(2 L_m)) → L_m = mu_r L_g/(2(Br/B - 1))
    if needed_B < BR_N42:
        needed_L_m = MU_R_MAGNET * L_GAP_TOTAL / (2.0 * (BR_N42 / needed_B - 1.0))
    else:
        needed_L_m = float("inf")
    print(f"  back-solved match (C=1.0):")
    print(f"    B_disk needed = {needed_B:.3f} T")
    print(f"    L_m needed    = {needed_L_m*1000:.1f} mm  (assuming yoke + Br=1.3 T)")
    print()

    print("Takeaway")
    print("-" * 76)
    print("  Open-loop (no yoke) predictions sit at ~0.5–0.75× of the fitted")
    print("  2ακ. The brake is too strong for a free-air pair, so a yoke or")
    print("  equivalent flux return must be doing real work — consistent with")
    print("  the observed steel bridge.")
    print()
    print("  Closed-loop (yoked, anti-polar) with the measured magnet")
    print("  dimensions — 2.5–3 cm diameter, 4–5 mm thick — predicts 2ακ")
    print("  between 0.8× and 1.6× the fitted 52.8. With the lower-bound")
    print("  diameter (2.5 cm) and 4.5 mm thickness, the central prediction")
    print("  is ≈ 1.0× — geometry, magnet circuit, and data all agree to")
    print("  within model-level precision.")
    print()
    print("  Remaining uncertainty in the prediction (factor ~1.5) is")
    print("  dominated by magnet-diameter range (a² in G_max). σ_Al alloy")
    print("  vs. pure (~30%), yoke imperfection, and constructive coupling")
    print("  between pairs all sit at the same order.")
    print()
    print("  Bottom line: the geometric calc with the measured brake")
    print("  dimensions and a steel yoke reproduces the fitted α to within")
    print("  the noise floor of the model. That genuinely is an independent")
    print("  anchor — the 1000 W spec, the geometric α/κ, and the fitted")
    print("  data triangulate consistently.")
    print()

    # Plot λ(R): data (if available) + physics predictions across
    # yoked / unyoked scenarios.
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    ax = axes[0]
    if pts:
        R_pts = np.array([p["R"] for p in pts])
        lam_pts = np.array([p["lam"] for p in pts])
        ax.scatter(R_pts, lam_pts, c="C1", s=24, alpha=0.7,
                   label=f"per-segment λ̂ from data (n={len(pts)})")

    scenarios = [
        ("open  B=0.45T  C=1.0",       B_gap_open(0.45),    1.0, "C3", ":"),
        ("yoked L_m=5mm  B={B:.2f}T",  B_gap_yoked(0.005),  1.0, "C2", "--"),
        ("yoked L_m=10mm B={B:.2f}T",  B_gap_yoked(0.010),  1.0, "C0", "-"),
        ("yoked L_m=20mm B={B:.2f}T",  B_gap_yoked(0.020),  1.0, "C4", "--"),
        ("yoked L_m=20mm B={B:.2f}T C=1.3", B_gap_yoked(0.020), 1.3, "C5", ":"),
    ]
    for label_tmpl, B, C, color, ls in scenarios:
        two_ak = physics_2alpha_kappa(B, G_max, C)
        lam = beta + two_ak * H_geom * H_geom / I_CRANK
        lw = 2.0 if ls == "-" else 1.3
        ax.plot(Rg, lam, ls, lw=lw, color=color,
                label=f"{label_tmpl.format(B=B)}  (2ακ={two_ak:.1f})")
    # Fitted reference line (uses fitted κ + α + Hill).
    fitted_2ak = FITTED_2AK
    lam_fit = beta + fitted_2ak * H_geom * H_geom / I_CRANK
    ax.plot(Rg, lam_fit, "k-", lw=2.5, alpha=0.5,
            label=f"fitted Wouterse (2ακ={fitted_2ak:.1f}, with fitted Hill)")
    ax.set_xlabel("R")
    ax.set_ylabel("λ (1/s)")
    ax.set_title("Linear-regime damping λ(R): geometry-predicted vs data + fit\n"
                 "H_geom(R) from overlap; β fixed; no free parameters in geom curves")
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(alpha=0.3)

    ax = axes[1]
    ax.plot(Rg, H_geom, "C0-", lw=2, label="H_geom(R) = √(G(R)/G_max)")
    # Overlay the empirical Hill from the prior fit for shape comparison
    R_h_emp, p_emp = 83.66, 1.21
    H_emp = np.where(Rg > 0,
                     Rg**p_emp / (Rg**p_emp + R_h_emp**p_emp),
                     0.0)
    ax.plot(Rg, H_emp, "C3--", lw=1.6, alpha=0.8,
            label=f"empirical Hill (R_h={R_h_emp}, p={p_emp})")
    ax.set_xlabel("R")
    ax.set_ylabel("H(R)")
    ax.set_title("Brake shape: geometry vs empirical Hill")
    ax.legend(fontsize=9)
    ax.grid(alpha=0.3)

    fig.tight_layout()
    out_path = ROOT / "analysis_out" / "physics_first.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close()
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
