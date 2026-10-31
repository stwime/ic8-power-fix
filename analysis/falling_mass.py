"""Absolute scale from a falling mass, no power meter needed.

Spin-downs only ever measure torque divided by inertia. A known mass on a
cord wound round a drum of known radius applies a known torque, which is
the missing absolute quantity. Film the crank with the same setup as the
spin-down videos, track it with analysis/track_crank.py, and run this on
the result.

Dynamics referred to the crank, with r_c the drum radius seen from the
crank (the drum radius itself on the crank side, radius × belt ratio on
the flywheel side):

    (s·I₀ + m·r_c²) · ω̇ = m·g·r_c − s·τ_model(R, ω)

The shipped model fixes τ/I from the spin-downs, so one number s scales
torque and inertia together. s is fit on the ω(t) trajectory by
integrating that equation. s·I₀ is the crank inertia; s is also the value
for the app's Power scale slider.

What a floor-limited drop can and cannot do: with the cord on the
flywheel rim, the belt ratio divides the travel, so a 2 m drop turns the
crank well under half a turn and lasts about a second. That is enough to
measure the inertia at R = 0 if the video is shot at 120 or 240 fps, but
the brake never reaches a terminal speed, so drops at R > 0 add little
beyond a consistency check. The high-R brake shape needs a power meter
(fit_reference.py). If a drop does plateau (ω̇ = 0), the brake torque
there equals m·g·r_c with no inertia involved and is reported separately.

Usage
  python analysis/falling_mass.py --track crank_video.csv --mass 5.0 \
      --radius 0.19 --drum flywheel --ratio 4.5 --R 0

  --mass    hanging mass in kg (weigh it)
  --radius  drum radius in m where the cord leaves it
  --drum    crank | flywheel: which side the cord is wound on
  --ratio   flywheel turns per crank turn (count on video; 4.5 is the
            value used in the repo)
  --R       dial position during the drop
  --t0/--t1 crop to the fall window in video seconds (default: from the
            first motion to the end of the file)

Do three drops at R = 0 and use their spread as the uncertainty of the
inertia. Drops at one higher R are an optional consistency check.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

import numpy as np
from scipy.integrate import solve_ivp
from scipy.optimize import minimize_scalar

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
    inertia = s * I_CRANK + m * r_c * r_c

    def rhs(_t, y):
        omega = max(y[0], 0.0)
        return [(m * G * r_c - s * tau_brake(R, omega, i_crank=I_CRANK)) / inertia]

    sol = solve_ivp(rhs, (t[0], t[-1]), [omega0], t_eval=t, max_step=0.05)
    return sol.y[0]


def fit_scale(t, w, m, r_c, R):
    omega0 = w[:3].mean()

    def rss(s):
        return float(((simulate(s, t, omega0, m, r_c, R) - w) ** 2).sum())

    res = minimize_scalar(rss, bounds=(0.2, 5.0), method="bounded")
    return res.x, np.sqrt(res.fun / len(t))


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


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--track", required=True, type=Path)
    ap.add_argument("--mass", required=True, type=float)
    ap.add_argument("--radius", required=True, type=float)
    ap.add_argument("--drum", choices=["crank", "flywheel"], required=True)
    ap.add_argument("--ratio", type=float, default=4.5)
    ap.add_argument("--R", type=float, required=True)
    ap.add_argument("--t0", type=float)
    ap.add_argument("--t1", type=float)
    args = ap.parse_args()

    t, w = load_track(args.track)
    if args.t0 is None:
        moving = np.nonzero(w > 0.5)[0]
        args.t0 = t[moving[0]] if len(moving) else t[0]
    if args.t1 is None:
        args.t1 = t[-1]
    sel = (t >= args.t0) & (t <= args.t1)
    t, w = t[sel], w[sel]
    if len(t) < 10:
        sys.exit("Fall window has too few samples; check --t0/--t1")

    r_c = args.radius * (args.ratio if args.drum == "flywheel" else 1.0)
    torque = args.mass * G * r_c
    print(f"Applied torque at the crank: {torque:.2f} N·m "
          f"(m={args.mass} kg, r_c={r_c:.4f} m), R={args.R:.0f}, "
          f"{len(t)} samples over {t[-1] - t[0]:.2f} s")

    s, rms = fit_scale(t, w, args.mass, r_c, args.R)
    print(f"Scale s = {s:.3f}  (trajectory RMS {rms:.3f} rad/s)")
    print(f"Crank inertia s·I₀ = {s * I_CRANK:.2f} kg·m²  "
          f"(shipped {I_CRANK})")
    print(f"Power scale slider: {s * 100:.0f}%")

    tp = terminal_point(t, w, args.R, args.mass, r_c, s)
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
