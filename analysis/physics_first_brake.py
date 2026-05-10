"""Compute brake torque from measured geometry — no fitting of α or κ.

User-measured / inferred geometry (2026-05-10):
  Disk: aluminum, 23 cm outer radius, 0.5 cm thick (Al conductivity σ_Al).
  Magnets: 4 N42 neodymium cylinders, 2.5 cm diameter, in 2 sandwich pairs
           (one pair attracting through disk → field reinforces inside Al).
  Axial gap: 3 mm magnet-face-to-disk-face, constant across R.
  R control: pivoting carrier swings magnet pairs from "barely outside the
             disk OD" at R=0 to "inside the OD by 1-2 cm" at R=100. The
             front pair sweeps further than the back pair.

What that means for the Wouterse model
--------------------------------------
B inside the disk is constant in R (gap doesn't change). What varies with R
is the overlap area between each magnet's pole face and the spinning disk,
plus the radial centroid of that overlap. The previously empirical Hill
H(R) was fitting

    H(R)²  =  G(R) / G_max,   G(R) = Σᵢ A_overlap_i(R) · d_i²(R)

where i runs over the two magnet pairs and d_i is each pair's distance from
the flywheel axis.

Translating between frames (gear ratio g = ω_fw / ω_crank = 4.5)
  τ_fw_linear(ω_fw) = σ · t · B²_disk · G(R) · ω_fw     (Wouterse-linear)
  τ_crank = g · τ_fw,  ω_fw = g · ω_crank
  ⇒ τ_crank_linear(ω_crank) = g² · σ · t · B²_disk · G(R) · ω_crank

Match to the strict-Wouterse model at the crank:
  τ_crank_linear = 2·α·κ·H(R)² · ω_crank
  ⇒ 2·α·κ (at H=1)  =  g² · σ · t · B²_disk · G_max

Single physical free parameter: B_disk. Everything else is measured.
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
OUT_DIR = ROOT / "data/calibration/wouterse_fit"
OUT_DIR.mkdir(parents=True, exist_ok=True)

# --- Physical constants --------------------------------------------------
SIGMA_AL = 3.5e7        # S/m
MU_0 = 4 * math.pi * 1e-7

# --- Geometry ------------------------------------------------------------
R_DISK = 0.230          # m, flywheel outer radius
T_DISK = 0.005          # m, disk thickness
A_MAG = 0.0125          # m, magnet radius (2.5 cm dia)
GAP_AXIAL = 0.003       # m, magnet face to disk face (constant in R)
GEAR = 4.5              # ω_flywheel / ω_crank
I_CRANK = 4.82          # kg·m², effective inertia at crank

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
# B inside the Al disk for an attracting N42 sandwich pair at 3 mm + 5 mm
# disk + 3 mm = 11 mm pole-to-pole separation. Surface B of an N42 magnet
# is ~0.5 T; through this stack with the reinforcing pair, B at the disk
# midplane is roughly the same as a single magnet's surface B. 0.45 T is
# the central estimate — sensitivity sweep below covers the realistic
# 0.35–0.55 T band.
B_DISK = 0.45           # T


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


def physics_2alpha_kappa(B_disk: float, G_max: float) -> float:
    """2·α·κ at H=1, derived from physics. Units: N·m·s/rad."""
    return GEAR * GEAR * SIGMA_AL * T_DISK * B_disk * B_disk * G_max


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
    segments = collect_segments()
    pts = per_segment_lambda(segments)

    Rg = np.linspace(0.0, 100.0, 401)
    G = np.array([G_of_R(R) for R in Rg])
    G_max = G_of_R(100.0)
    H_geom = np.sqrt(np.divide(G, G_max, out=np.zeros_like(G), where=G_max > 0))

    # β from R=0 data
    r0_lams = [p["lam"] for p in pts if p["R"] == 0]
    beta = float(np.mean(r0_lams)) if r0_lams else 0.04

    # Physics 2·α·κ at H=1 for several B assumptions
    print("=" * 64)
    print("Physics-first brake parameters")
    print("=" * 64)
    print(f"  Disk: R={R_DISK*100:.1f} cm, t={T_DISK*1000:.1f} mm")
    print(f"  Magnet: dia={2*A_MAG*100:.1f} cm, axial gap={GAP_AXIAL*1000:.1f} mm")
    print(f"  Pairs: 2, attracting through disk")
    print()
    print(f"  G(R=100) = {G_max:.4e} m⁴")
    print(f"  G(R=50)  = {G_of_R(50):.4e} m⁴")
    print(f"  G(R=30)  = {G_of_R(30):.4e} m⁴")
    print(f"  G(R=10)  = {G_of_R(10):.4e} m⁴")
    print()
    print(f"  β (from {len(r0_lams)} R=0 spindown(s)) = {beta:.4f} 1/s")
    print()
    print(f"  {'B (T)':>6}  {'2αk':>7}  {'λ(R=100)':>9}  {'λ(R=50)':>8}")
    for B in (0.30, 0.40, 0.45, 0.50, 0.60):
        two_ak = physics_2alpha_kappa(B, G_max)
        H100 = math.sqrt(G_of_R(100) / G_max)
        H50 = math.sqrt(G_of_R(50) / G_max)
        lam100 = beta + two_ak * H100 * H100 / I_CRANK
        lam50 = beta + two_ak * H50 * H50 / I_CRANK
        print(f"  {B:>6.2f}  {two_ak:>7.2f}  {lam100:>9.3f}  {lam50:>8.3f}")
    print()

    # Plot λ(R) data vs physics predictions over B sweep
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))

    ax = axes[0]
    R_pts = np.array([p["R"] for p in pts])
    lam_pts = np.array([p["lam"] for p in pts])
    ax.scatter(R_pts, lam_pts, c="C1", s=24, alpha=0.7,
               label=f"per-segment λ̂ from data (n={len(pts)})")
    for B, ls, alpha_line in [(0.30, ":", 0.6), (0.40, "--", 0.7),
                               (0.45, "-", 1.0), (0.50, "--", 0.7),
                               (0.60, ":", 0.6)]:
        two_ak = physics_2alpha_kappa(B, G_max)
        lam = beta + two_ak * H_geom * H_geom / I_CRANK
        lw = 2.2 if ls == "-" else 1.4
        ax.plot(Rg, lam, ls, lw=lw, alpha=alpha_line,
                label=f"B_disk = {B:.2f} T  (2ακ={two_ak:.1f})")
    ax.set_xlabel("R")
    ax.set_ylabel("λ (1/s)")
    ax.set_title("Physics-first λ(R) = β + 2ακH(R)²/I  vs per-segment λ̂\n"
                 "H(R) from overlap geometry; no fit")
    ax.legend(fontsize=8, loc="upper left")
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
    out_path = OUT_DIR / "physics_first.png"
    fig.savefig(out_path, dpi=130)
    plt.close()
    print(f"wrote {out_path}")


if __name__ == "__main__":
    main()
