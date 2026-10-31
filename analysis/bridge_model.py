"""Python replica of the bridge's shipped physics model.

Mirrors bridge/lib/physics/calibration.dart at its defaults so offline
scripts (fit_reference.py, falling_mass.py) can compute exactly what the
app would broadcast for a given R and cadence. Keep the constants in
step with Calibration.default* in the Dart file; bridge/test/
bridge_model_parity_test.dart pins a few values from here.
"""

from __future__ import annotations

import math

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


if __name__ == "__main__":
    for r in (10, 30, 50, 70, 90):
        print(r, " ".join(f"{brake_power(r, c):7.1f}" for c in (60, 80, 100)))
