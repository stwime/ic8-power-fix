"""Re-unwrap angle_mod_pi from a track_crank.py CSV using a velocity prior.

The original ``unwrap_mod_pi`` in track_crank.py picks each frame's unwrap
candidate (θ or θ+π) by minimising distance to the previous unwrapped value.
That's locally myopic: when |Δθ| per frame approaches the π/2 boundary
(normal during high-ω portions of pedaling), small PCA noise can flip the
chosen sign and the cumulative angle stops tracking the true direction of
rotation. The error persists for the rest of the segment.

Fix: maintain a smoothed running ω. Predict the next absolute angle as
θ_pred = θ_prev + ω̂·dt. Of the two unwrap candidates (which differ by π),
pick the one closer to θ_pred. ω̂ updates from a small EWMA over the most
recent accepted Δθ values, with a slow time constant so brief PCA wobbles
don't pull the prediction off course.

Usage:
    python analysis/reunwrap_crank.py crank_video.csv crank_video_v2.csv
"""

from __future__ import annotations

import csv
import math
import sys
from pathlib import Path


def reunwrap(times: list[float], mod_pi_angles: list[float],
             omega_seed_window_s: float = 1.0,
             ewma_tau_s: float = 0.5
             ) -> tuple[list[float], list[float]]:
    """Returns (unwrapped, omega) lists, same length as inputs.

    Strategy:
      - Bootstrap ω̂ from the first ``omega_seed_window_s`` of frames using
        the locally-myopic unwrap (assume rotation is slow enough at the
        very start that direction isn't ambiguous yet — spin-down videos
        start with the rider stopping pedaling, so the first second is
        usually the highest ω, but we still need to seed ω̂ somehow).
      - From there, predict each next angle from ω̂; pick the candidate
        closer to the prediction; update ω̂ with EWMA.
    """
    n = len(times)
    if n == 0:
        return [], []
    unwrapped = [mod_pi_angles[0]]
    omega = [0.0]

    # Phase 1: locally-myopic unwrap to seed ω̂. Use the first
    # ``omega_seed_window_s`` of frames or 30 frames, whichever is shorter,
    # then commit to velocity-prior unwrap from there. The seed window's
    # results may be noisy but they only affect the very start of any
    # segment.
    seed_end = 0
    while (seed_end + 1 < n
           and times[seed_end + 1] - times[0] < omega_seed_window_s
           and seed_end < 30):
        seed_end += 1

    for i in range(1, seed_end + 1):
        prev = unwrapped[-1]
        cur = mod_pi_angles[i]
        delta = cur - (prev % math.pi)
        if delta > math.pi / 2:
            delta -= math.pi
        elif delta < -math.pi / 2:
            delta += math.pi
        unwrapped.append(prev + delta)
        dt = times[i] - times[i - 1]
        omega.append(delta / dt if dt > 0 else 0.0)

    if seed_end < 1:
        return unwrapped, omega

    # Initialise ω̂ from the median of accepted Δθ/dt over the seed window
    # (median is robust to one or two bad frames during the seed).
    seed_omega = sorted(omega[1:])
    omega_hat = seed_omega[len(seed_omega) // 2]

    # Phase 2: velocity-prior unwrap.
    for i in range(seed_end + 1, n):
        prev = unwrapped[-1]
        cur = mod_pi_angles[i]
        dt = times[i] - times[i - 1]
        theta_pred = prev + omega_hat * dt
        # Two candidates for the new unwrapped angle: cur + k·π for the k
        # that brings it closest to theta_pred. cur is in [0, π), so we
        # search k = round((theta_pred - cur) / π).
        k = round((theta_pred - cur) / math.pi)
        new_unwrapped = cur + k * math.pi
        delta = new_unwrapped - prev
        unwrapped.append(new_unwrapped)
        new_omega = delta / dt if dt > 0 else omega_hat
        omega.append(new_omega)

        # EWMA update with time constant ewma_tau_s.
        alpha = 1 - math.exp(-dt / max(ewma_tau_s, 1e-6))
        omega_hat = (1 - alpha) * omega_hat + alpha * new_omega

    return unwrapped, omega


def main():
    if len(sys.argv) != 3:
        print("usage: reunwrap_crank.py <input.csv> <output.csv>")
        sys.exit(1)
    src = Path(sys.argv[1])
    dst = Path(sys.argv[2])

    # Read the rows we need; preserve other columns.
    rows = list(csv.DictReader(src.open()))
    if not rows:
        sys.exit("empty input")

    # Group consecutive rows by whether angle_mod_pi is present.
    # Where it's missing (failed PCA), we restart the unwrap chain on the
    # next valid run so a chain of failures doesn't corrupt subsequent
    # tracking.
    times: list[float] = []
    angles: list[float] = []
    indices: list[int] = []  # original row index for each (time, angle)
    chains: list[tuple[list[int], list[float], list[float]]] = []
    for i, r in enumerate(rows):
        if r["angle_mod_pi_rad"]:
            times.append(float(r["t_video_s"]))
            angles.append(float(r["angle_mod_pi_rad"]))
            indices.append(i)
        else:
            if times:
                chains.append((indices, times, angles))
            times, angles, indices = [], [], []
    if times:
        chains.append((indices, times, angles))

    # Re-unwrap each chain independently.
    unwrapped_per_idx: dict[int, float] = {}
    omega_per_idx: dict[int, float] = {}
    for idxs, ts, ang in chains:
        uw, om = reunwrap(ts, ang)
        for i, u, o in zip(idxs, uw, om):
            unwrapped_per_idx[i] = u
            omega_per_idx[i] = o

    # Write output: preserve all columns, overwrite angle_unwrapped_rad
    # and omega_rad_s where we have new values.
    fieldnames = list(rows[0].keys())
    with dst.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for i, r in enumerate(rows):
            if i in unwrapped_per_idx:
                r = dict(r)
                r["angle_unwrapped_rad"] = f"{unwrapped_per_idx[i]:.6f}"
                r["omega_rad_s"] = f"{omega_per_idx[i]:.6f}"
            w.writerow(r)
    print(f"wrote {dst} ({len(unwrapped_per_idx)} re-unwrapped frames)")


if __name__ == "__main__":
    main()
