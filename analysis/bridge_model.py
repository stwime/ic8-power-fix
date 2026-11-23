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

ALPHA = 165.0        # N·m, eddy peak torque amplitude (α/κ ≈ 1041 W prior)
BETA = 0.0154        # 1/s, viscous residual drag
TAU_C = 1.1468       # N·m, Coulomb residual drag
W = 0.5994           # mix weight on H1
R_H1 = 185.036
P1 = 0.669
R_H2 = 59.372
P2 = 2.246
KAPPA = 0.1585       # s/rad
I_CRANK = 7.55       # kg·m² at the crank


def _hill(r: float, r_h: float, p: float) -> float:
    rp = r ** p
    return rp / (rp + r_h ** p)


def hill(r: float) -> float:
    """H(R): sum of two Hill curves, zero at R=0, saturating below 1."""
    if r <= 0:
        return 0.0
    return W * _hill(r, R_H1, P1) + (1.0 - W) * _hill(r, R_H2, P2)


def tau_brake(r: float, omega: float, power_scale: float = 1.0,
              i_crank: float = I_CRANK) -> float:
    """Brake + residual torque at the crank, N·m, as the app computes it."""
    h = hill(r)
    x = KAPPA * h * omega
    tau_eddy = ALPHA * power_scale * h * 2.0 * x / (1.0 + x * x)
    tau_residual = power_scale * (TAU_C + i_crank * BETA * omega)
    return tau_eddy + tau_residual


def brake_power(r: float, cadence_rpm: float, power_scale: float = 1.0) -> float:
    """Steady-state power in W at dial position r and crank cadence."""
    omega = cadence_rpm * math.pi / 30.0
    return tau_brake(r, omega, power_scale) * omega


# What the IC8 itself broadcasts as power: P = k(R)·cad^1.6 with
# ln k = a + b·ln R + c·ln²R. Fit on 293 steady 1 Hz samples from a BLE
# log of resistance holds (R 10 to 96, 31 to 123 rpm). At fixed R the
# cadence exponent is 1.60; the fit is within 2.5% of the broadcast value
# in every 10-wide R band (sample scatter 7%). The README quotes the
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
