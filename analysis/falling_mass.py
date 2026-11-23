"""Absolute scale from a falling mass, no power meter needed.

Spin-downs only ever measure torque divided by inertia. A known mass
applying a known torque is the missing absolute quantity. Film the crank
with the same setup as the spin-down videos, track it with
analysis/track_crank.py, and run this on the result.

Two ways to apply the mass:

  --drum pedal     (easiest) Hang the mass from the end of a crank arm
                   (a loop girth-hitched round the arm next to the pedal,
                   not the mass stood on the pedal, which tips it off)
                   with the arm horizontal and release. The crank arm
                   is the lever, so nothing is wound and the belt ratio
                   does not enter.
                   Torque is m·g·L·cos ψ with ψ the angle swept past
                   horizontal; the fit uses the half turn down to the
                   bottom, where ω peaks. --radius is the crank length
                   (pedal spindle to bottom bracket, about 0.17 m).
  --drum flywheel  Cord wound on the flywheel rim; --radius is the rim
  --drum crank     radius, --ratio the belt ratio. The ratio divides the
                   travel, so a floor-limited drop turns the crank under
                   half a turn in about a second: film at 120 or 240 fps.

The shipped model fixes τ/I from the spin-downs, so one number s scales
torque and inertia together:

    pedal:  (s·I₀ + m·L²cos²ψ)·ω̇ = m·g·L·cos ψ + m·L²·cos ψ·sin ψ·ω² − s·τ_model(R, ω)
    drum:   (s·I₀ + m·r_c²)·ω̇ = m·g·r_c − s·τ_model(R, ω)

s is fit on the ω(t) trajectory by integrating the equation. s·I₀ is the
crank inertia; s is also the value for the app's Power scale slider.

What this anchors: the inertia, and with it absolute power everywhere the
spin-downs covered the riding cadence (R ≤ 74 up to about 100 rpm, R ≥ 81
up to 60–90 rpm, see data/calibration/all_spindowns.csv). It does not
test the brake shape beyond that range; a power meter does
(fit_reference.py). Do it at R = 0, where the brake torque is smallest
next to the applied torque.

Usage
  python analysis/falling_mass.py --track drop.csv --mass 8.0 \
      --radius 0.17 --drum pedal --R 0

  --mass    hanging mass in kg (weigh it)
  --radius  crank length (pedal) or drum radius (flywheel/crank) in m
  --R       dial position during the drop (use 0)
  --t0/--t1 crop to the drop in video seconds; by default the window
            starts at first motion and, for the pedal, ends at the ω peak

Do three drops at R = 0 and use their spread as the uncertainty.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import least_squares

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.bridge_model import I_CRANK, tau_brake  # noqa: E402

G = 9.80665


def load_track(path: Path):
    t, w = [], []
    for row in csv.DictReader(path.open()):
        if row["omega_rad_s"] in ("", None):
            continue
        t.append(float(row["t_video_s"]))
        w.append(float(row["omega_rad_s"]))
    if len(t) < 10:
        sys.exit(f"{path}: too few samples")
    return np.array(t), np.array(w)


def simulate(s, t, omega0, m, r_c, R):
    """Drum variant: constant applied torque m·g·r_c."""
    inertia = s * I_CRANK + m * r_c * r_c

    def rhs(_t, y):
        omega = max(y[0], 0.0)
        return [(m * G * r_c - s * tau_brake(R, omega, i_crank=I_CRANK)) / inertia]

    sol = solve_ivp(rhs, (t[0], t[-1]), [omega0], t_eval=t, max_step=0.05,
                    rtol=1e-6, atol=1e-8)
    return sol.y[0]


def simulate_pedal(s, t, omega0, m, L, R, psi0=0.0):
    """Pedal-lever variant: the mass hangs from the pedal, the crank starts
    psi0 rad past horizontal (0 = horizontal) and swings down. State is
    [ψ, ω]; the mass's own motion adds m·L²cos²ψ to the inertia and a
    centripetal term, both small next to I₀ but free to include."""

    def rhs(_t, y):
        psi, omega = y
        omega = max(omega, 0.0)
        c, sn = np.cos(psi), np.sin(psi)
        inertia = s * I_CRANK + m * L * L * c * c
        torque = (m * G * L * c + m * L * L * c * sn * omega * omega
                  - s * tau_brake(R, omega, i_crank=I_CRANK))
        return [omega, torque / inertia]

    sol = solve_ivp(rhs, (t[0], t[-1]), [psi0, omega0], t_eval=t, max_step=0.02,
                    rtol=1e-6, atol=1e-8)
    return sol.y[1]


def fit_scale(t, w, m, r_c, R, pedal=False, psi0=0.0):
    """Fit the scale s and the initial ω together. Fitting ω₀ instead of
    reading it off the first noisy frames matters: a 0.03 rad/s error in
    ω₀ over a 1.5 s swing biases s by about 2%."""
    sim = ((lambda s, w0: simulate_pedal(s, t, w0, m, r_c, R, psi0)) if pedal
           else (lambda s, w0: simulate(s, t, w0, m, r_c, R)))

    def resid(x):
        return sim(x[0], x[1]) - w

    x0 = [1.0, max(float(w[:3].mean()), 0.0)]
    res = least_squares(resid, x0, bounds=([0.2, 0.0], [5.0, 20.0]))
    return res.x[0], float(np.sqrt((res.fun ** 2).mean()))


def terminal_point(t, w, R, m, r_c, m_scale, window_s=1.0, tol=0.02):
    """If ω settles (relative change below tol over the last window), the
    brake torque there is exactly m·g·r_c. Returns (omega, tau) or None."""
    sel = t >= t[-1] - window_s
    if sel.sum() < 5:
        return None
    ws = w[sel]
    if ws.mean() <= 0 or (ws.max() - ws.min()) / ws.mean() > tol:
        return None
    return float(ws.mean()), m * G * r_c


def release_index(w, rise=0.3, rest=0.05, smooth=5):
    """Index of the release: walk back from the first clearly moving frame
    to the last frame that was still at rest. Starting the fit here keeps
    the model's start angle equal to the real one."""
    k = np.ones(smooth) / smooth
    ws = np.convolve(w, k, mode="same")
    moving = np.nonzero(ws > rise)[0]
    if not len(moving):
        return 0
    i = int(moving[0])
    while i > 0 and ws[i - 1] > rest:
        i -= 1
    return i


def first_peak_end(w, drop_frac=0.05, drop_abs=0.15, smooth=5):
    """Index just after ω's first peak: where the smoothed ω has fallen
    below its running maximum by drop_frac of it and by at least drop_abs
    rad/s, so frame noise on the rising flank does not count as a peak.
    For the pedal swing that is the bottom."""
    k = np.ones(smooth) / smooth
    ws = np.convolve(w, k, mode="same")
    run_max = np.maximum.accumulate(ws)
    drop = np.maximum(run_max * drop_frac, drop_abs)
    below = np.nonzero((ws < run_max - drop) & (run_max > 0.3))[0]
    return int(below[0]) if len(below) else len(w)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--track", required=True, type=Path)
    ap.add_argument("--mass", required=True, type=float)
    ap.add_argument("--radius", required=True, type=float)
    ap.add_argument("--drum", choices=["pedal", "crank", "flywheel"], required=True)
    ap.add_argument("--ratio", type=float, default=4.5,
                    help="flywheel turns per crank turn (flywheel drum only)")
    ap.add_argument("--start-deg", type=float, default=0.0,
                    help="pedal only: crank angle past horizontal at release")
    ap.add_argument("--R", type=float, required=True)
    ap.add_argument("--t0", type=float)
    ap.add_argument("--t1", type=float)
    args = ap.parse_args()
    pedal = args.drum == "pedal"

    t, w = load_track(args.track)
    if args.t0 is None:
        args.t0 = t[release_index(w)]
    sel = t >= args.t0
    t, w = t[sel], w[sel]
    if args.t1 is None:
        end = first_peak_end(w) if pedal else len(w)
        t, w = t[:end], w[:end]
    else:
        sel = t <= args.t1
        t, w = t[sel], w[sel]
    if len(t) < 10:
        sys.exit("Fall window has too few samples; check --t0/--t1")

    r_c = args.radius * (args.ratio if args.drum == "flywheel" else 1.0)
    torque = args.mass * G * r_c
    what = "peak torque at the crank (horizontal)" if pedal else "applied torque at the crank"
    print(f"{what[0].upper()}{what[1:]}: {torque:.2f} N·m "
          f"(m={args.mass} kg, lever {r_c:.4f} m), R={args.R:.0f}, "
          f"{len(t)} samples over {t[-1] - t[0]:.2f} s")

    s, rms = fit_scale(t, w, args.mass, r_c, args.R, pedal=pedal,
                       psi0=np.radians(args.start_deg))
    print(f"Scale s = {s:.3f}  (trajectory RMS {rms:.3f} rad/s)")
    print(f"Crank inertia s·I₀ = {s * I_CRANK:.2f} kg·m²  "
          f"(shipped {I_CRANK})")
    print(f"Power scale slider: {s * 100:.0f}%")

    tp = None if pedal else terminal_point(t, w, args.R, args.mass, r_c, s)
    if tp is not None:
        omega_t, tau_t = tp
        tau_model = tau_brake(args.R, omega_t)
        print(f"Terminal speed reached: {omega_t * 30 / np.pi:.1f} rpm, "
              f"brake torque there = {tau_t:.2f} N·m measured vs "
              f"{tau_model:.2f} N·m in the model → scale {tau_t / tau_model:.3f}")
    elif args.R > 0:
        print("No terminal plateau in the window; the scale above comes from "
              "the trajectory fit only.")


if __name__ == "__main__":
    main()
