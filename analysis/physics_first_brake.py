"""The brake from the measured magnet geometry, no fitting, against the
spin-down fit (analysis/bridge_model.py).

Geometry (measured 2026-05-10)
------------------------------
Disc:    aluminium, 23 cm outer radius, 5 mm thick.
Magnets: 4 N42 cylinders, 2.5–3 cm diameter, 4–5 mm thick, in two pairs
         that straddle the disc at a fixed 3 mm gap, anti-polar, with a
         steel yoke closing the flux loop.
Dial:    a pivoting carrier slides the pairs from just off the rim (R=0)
         onto the disc. At R=100 the back pair's outer edge sits at the
         rim and the front pair's 7 mm inside it. Dial to carrier is taken
         as linear.

Low-speed brake
---------------
A pole moving through a thin sheet drives J = σ(v×B − ∇φ): the EMF inside
the pole's footprint minus the field of the charge that piles up at the
footprint's edge and at the disc rim. For a rotating disc v×B = ωB·r
(radial), and the torque on the flywheel is

    τ_fw = σ t B² G ω_fw,   G = ∫ (r − ∇ψ)·r dA over the footprint

with ψ = φ/(ωB) from ∇·J = 0 and J·n = 0 at the rim. At the crank
(ω_fw = g·ω) that is linear damping c(R) = g² σ t B² G(R), to compare with
the fit's c·H(R)². Without the charge term G would be A·r²; in an open
sheet it is half that (a uniform EMF in a circular patch), and at the rim
about 0.4·A·r². pole_G solves for ψ by finite volumes.

B in the gap comes from the yoked magnetic-circuit load line, which
ignores leakage and so is an upper bound. σ is 3.5e7 S/m for pure
aluminium and 2.5e7 for 6061; the disc alloy is unknown.

Critical speed
--------------
Above v ≈ w = 2/(μ0 σ t) (thin-sheet characteristic speed) the induced
field cancels the source and the brake fades. At the magnets' radius that
is a crank speed ω_c = w/(g·r). The spin-downs show no fade up to the
speeds they reach (fit_spindowns.py --fade-profile).
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import scipy.sparse as sp
import scipy.sparse.linalg as spl

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from analysis.bridge_model import C_EDDY, I_CRANK, hill  # noqa: E402

MU_0 = 4 * math.pi * 1e-7
SIGMAS = (3.5e7, 2.5e7)     # S/m: pure aluminium, 6061
R_DISK = 0.230              # m, flywheel outer radius
T_DISK = 0.005              # m, disc thickness
GAP_AXIAL = 0.003           # m, magnet face to disc face (constant in R)
GEAR = 4.5                  # ω_flywheel / ω_crank
A_MAG = 0.0125              # m, magnet radius (2.5 cm diameter)
BR_N42 = 1.3                # T, remanence
MU_R_MAGNET = 1.05          # NdFeB recoil permeability
L_GAP_TOTAL = GAP_AXIAL + T_DISK + GAP_AXIAL   # magnetic gap per pair


def B_gap_yoked(L_m: float) -> float:
    """Gap field with the steel yoke: B = Br/(1 + μ_r·L_gap/(2·L_m)), two
    magnets in series per gap, yoke reluctance and leakage ignored."""
    return BR_N42 / (1.0 + MU_R_MAGNET * L_GAP_TOTAL / (2.0 * L_m))


def magnet_center(R: float, pair: str, a: float = A_MAG) -> float:
    """Radial position of a pair's centre, m, linear in R. At R=0 the outer
    edge is 1 mm past the rim; at R=100 see the module docstring."""
    r0 = R_DISK + a + 0.001
    r100 = R_DISK - a - (0.007 if pair == "front" else 0.0)
    return r0 + (r100 - r0) * R / 100.0


def pole_G(center_r: float, a: float, h: float = 0.0004,
           box: float = 0.07) -> float:
    """G (m⁴) for one circular pole of radius a centred at center_r on the
    disc. Finite volumes on an h grid over a box around the pole, clipped to
    the disc; J·n = 0 at the rim and at the box edge (currents 5 pole radii
    away carry no measurable power)."""
    xs = np.arange(center_r - box, center_r + box, h) + h / 2
    ys = np.arange(-box, box, h) + h / 2
    X, Y = np.meshgrid(xs, ys, indexing="ij")
    inside = X**2 + Y**2 <= R_DISK**2
    pole = ((X - center_r)**2 + Y**2 <= a * a) & inside
    if not pole.any():
        return 0.0
    idx = -np.ones(X.shape, int)
    idx[inside] = np.arange(inside.sum())
    n = int(inside.sum())
    rows, cols, vals, faces = [], [], [], []
    b = np.zeros(n)
    nx, ny = X.shape
    for axis, (lo, hi) in enumerate((((slice(0, nx - 1), slice(None)),
                                      (slice(1, nx), slice(None))),
                                     ((slice(None), slice(0, ny - 1)),
                                      (slice(None), slice(1, ny))))):
        ok = inside[lo] & inside[hi]
        i, j = idx[lo][ok], idx[hi][ok]
        coord = (X if axis == 0 else Y)
        # EMF component across the face (unit ωB): r along this axis,
        # inside the footprint (half on a footprint edge).
        e = 0.5 * (coord[lo] + coord[hi])[ok] * \
            0.5 * (pole[lo].astype(float) + pole[hi].astype(float))[ok]
        one = np.ones_like(e)
        rows += [i, i, j, j]
        cols += [i, j, j, i]
        vals += [one, -one, one, -one]
        # Zero net outflow per cell: Σ (e·h − (ψ_j − ψ_i)) = 0.
        np.add.at(b, i, -e * h)
        np.add.at(b, j, e * h)
        faces.append((i, j, e))
    L = sp.csr_matrix((np.concatenate(vals),
                       (np.concatenate(rows), np.concatenate(cols))),
                      shape=(n, n)) + sp.identity(n) * 1e-12
    psi = spl.spsolve(L.tocsc(), b)
    return float(sum(np.sum((e - (psi[j] - psi[i]) / h) * e) * h * h
                     for i, j, e in faces))


def G_of_R(R: float, a: float = A_MAG) -> float:
    """G summed over both pairs at dial position R, m⁴."""
    return sum(pole_G(magnet_center(R, pair, a), a) for pair in ("front", "back"))


def damping_fit(R: float) -> float:
    """The fitted brake damping c·H(R)² at the crank, N·m·s/rad."""
    return C_EDDY * hill(R) ** 2


def main():
    a = A_MAG
    G_open = pole_G(0.10, a, box=0.08) / (math.pi * a * a * 0.10**2)
    print(f"Check: a pole far from the rim gives G = {G_open:.3f}·A·r² "
          f"(½ expected; the box edge takes a little)\n")

    c100 = damping_fit(100.0)
    print(f"Fitted brake damping at R=100: {c100:.2f} N·m·s/rad at the crank")
    print(f"(spin-downs fix damping/I; this is at I = {I_CRANK} kg·m²)\n")
    print(f"  {'dia cm':>6} {'L_m mm':>6} {'B T':>6} {'σ S/m':>8} "
          f"{'c geom':>7} {'ratio':>6} {'I implied':>9}")
    ratios = []
    for dia in (2.5, 2.75, 3.0):
        ad = dia / 200
        G = G_of_R(100.0, ad)
        for L_m in (4.0, 5.0):
            B = B_gap_yoked(L_m / 1000)
            for sigma in SIGMAS:
                c = GEAR**2 * sigma * T_DISK * B * B * G
                ratios.append(c / c100)
                print(f"  {dia:>6.2f} {L_m:>6.1f} {B:>6.3f} {sigma:>8.1e} "
                      f"{c:>7.1f} {c / c100:>6.2f} {I_CRANK * c / c100:>9.1f}")
    print(f"\n  Geometry over fit at R=100: {min(ratios):.2f}–{max(ratios):.2f}×, "
          f"so the magnets alone give I = {I_CRANK * min(ratios):.1f}–"
          f"{I_CRANK * max(ratios):.1f} kg·m²")
    print("  (B is an upper bound, so the low end is likelier).\n")

    Rg = np.arange(0, 101, 5.0)
    G = np.array([G_of_R(R) for R in Rg])
    shape_geom = G / G[-1]
    shape_fit = np.array([damping_fit(R) for R in Rg]) / c100
    print("Brake shape, damping relative to R=100 (2.5 cm magnets, linear dial):")
    print(f"  {'R':>3} {'geometry':>9} {'fit':>6}")
    for R, sg, sf in zip(Rg, shape_geom, shape_fit):
        if R % 10 == 0:
            print(f"  {R:>3.0f} {sg:>9.3f} {sf:>6.3f}")

    print("\nCritical speed (thin sheet) at the magnets' radius:")
    r_mag = R_DISK - a
    for sigma in SIGMAS:
        w = 2.0 / (MU_0 * sigma * T_DISK)
        wc = w / (GEAR * r_mag)
        print(f"  σ {sigma:.1e}: w = {w:4.1f} m/s → crank ω_c ≈ "
              f"{wc * 30 / math.pi:3.0f} rpm")

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(Rg, shape_fit, "k-", lw=2.2, label="spin-down fit c·H(R)²")
    ax.plot(Rg, shape_geom, "C0o--", lw=1.6,
            label="magnet geometry, eddy currents solved, linear dial")
    ax.set_xlabel("R")
    ax.set_ylabel("brake damping relative to R = 100")
    ax.set_title("Brake shape: geometry vs spin-down fit")
    ax.grid(alpha=0.3)
    ax.legend()
    fig.tight_layout()
    out_path = ROOT / "analysis_out" / "physics_first.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=130)
    plt.close()
    print(f"\nwrote {out_path}")


if __name__ == "__main__":
    main()
