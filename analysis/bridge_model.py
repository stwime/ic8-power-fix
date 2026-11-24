"""Python replica of the bridge's shipped physics model.

Mirrors bridge/lib/physics/calibration.dart at its defaults so offline
scripts (fit_reference.py, falling_mass.py, ftp_anchor.py) can compute
exactly what the app would broadcast for a given R and cadence. Keep the
constants in step with Calibration.default* in the Dart file; bridge/test/
bridge_model_parity_test.dart pins a few values from here.

Also holds the bike's own broadcast formula (ic8_broadcast_power), which
training-app .fit files need: they record the bike's power and cadence
but drop the dial position.
"""

from __future__ import annotations

import math

import numpy as np

# Spin-downs fix torque over inertia, so C_EDDY, TAU_C and I_CRANK only
# matter together: scaling all three by one factor leaves every fitted ω(t)
# unchanged. I_CRANK is the flywheel geometry with steel rings (see
# docs/physics.md, "Inertia"), and C_EDDY and TAU_C are the spin-down fit
# (analysis/fit_spindowns.py) at that I.
C_EDDY = 43.7682     # N·m·s/rad, brake damping at the crank at H = 1
BETA = 0.013854      # 1/s, viscous residual drag
TAU_C = 1.0980       # N·m, Coulomb residual drag
W = 0.44985          # mix weight on H1
R_H1 = 129.292
P1 = 0.64984
R_H2 = 78.890
P2 = 1.87239
I_CRANK = 6.66       # kg·m² at the crank


def _hill(r, r_h: float, p: float):
    rp = r ** p
    return rp / (rp + r_h ** p)


def hill(r):
    """H(R): sum of two Hill curves, zero at R ≤ 0, saturating below 1.
    Works on floats and numpy arrays."""
    r = np.maximum(np.asarray(r, dtype=float), 0.0)
    h = W * _hill(r, R_H1, P1) + (1.0 - W) * _hill(r, R_H2, P2)
    return h if h.ndim else float(h)


def tau_brake(r, omega, power_scale: float = 1.0, i_crank: float = I_CRANK):
    """Brake + residual torque at the crank, N·m, as the app computes it.
    Works on floats and numpy arrays."""
    h = hill(r)
    tau_eddy = C_EDDY * power_scale * h * h * omega
    tau_residual = power_scale * (TAU_C + i_crank * BETA * omega)
    return tau_eddy + tau_residual


R_JUMP = 3   # Constants.rJump


def smooth_resistance(r):
    """The dial position the app uses for each sample, as
    bridge/lib/physics/resistance_filter.dart computes it: a change of
    R_JUMP or more is taken at once, a smaller one when the next sample
    is off in the same direction. NaN samples (gaps in a log) give NaN
    and leave the filter as it was."""
    out = np.full(len(r), np.nan)
    held, pending = None, 0
    for i, v in enumerate(r):
        if not np.isfinite(v):
            continue
        v = int(v)
        if held is None:
            held = v
        d = v - held
        sign = (d > 0) - (d < 0)
        if d == 0:
            pending = 0
        elif abs(d) >= R_JUMP or sign == pending:
            held, pending = v, 0
        else:
            pending = sign
        out[i] = held
    return out


def brake_power(r, cadence_rpm, power_scale: float = 1.0):
    """Steady-state power in W at dial position r and crank cadence.
    Works on floats and numpy arrays."""
    omega = cadence_rpm * math.pi / 30.0
    return tau_brake(r, omega, power_scale) * omega


# What the IC8 itself broadcasts as power: P = k(R)·cad^1.6 with
# ln k = a + b·ln R + c·ln²R. Fit on 293 steady 1 Hz samples from a BLE
# log of resistance holds (R 10 to 96, 31 to 123 rpm). At fixed R the
# cadence exponent is 1.60; the fit is within 2.5% of the broadcast value
# in every 10-wide R band (sample scatter 7%). docs/physics.md quotes the
# plainer power law 0.0125·R^0.82·cad^1.6, which is within 7%. The extra
# precision matters for resistance_from_broadcast: model power is steep in
# R, so a 7% error in the bike's number becomes about 14% in model power.
IC8_LNK = (-3.1016, 0.08239, 0.10409)   # a, b, c
IC8_CAD_EXP = 1.6


def ic8_broadcast_power(r, cadence_rpm):
    """The bike's own power number in W. Works on floats and numpy arrays."""
    a, b, c = IC8_LNK
    u = np.log(r)
    return np.exp(a + b * u + c * u * u) * cadence_rpm ** IC8_CAD_EXP


def resistance_from_broadcast(power_w, cadence_rpm):
    """Invert ic8_broadcast_power: the dial position that makes the bike
    show power_w at cadence_rpm. ln k rises monotonically for R ≥ 1, so the
    larger root of the quadratic in ln R is the one. Below the minimum of
    k (R ≈ 0.7) there is no root and the minimum is returned. Works on
    floats and numpy arrays."""
    a, b, c = IC8_LNK
    y = np.log(power_w / cadence_rpm ** IC8_CAD_EXP)
    disc = np.maximum(b * b - 4.0 * c * (a - y), 0.0)
    return np.exp((-b + np.sqrt(disc)) / (2.0 * c))


if __name__ == "__main__":
    for r in (10, 30, 50, 70, 90):
        print(r, " ".join(f"{brake_power(r, c):7.1f}" for c in (60, 80, 100)))
