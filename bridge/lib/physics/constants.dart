/// Constants from analysis/correct_power.py and analysis/spindown_fit.py.
///
/// Spin-down fit gave: λ(R) = a·R + b per second, where
/// dω/dt = -λ(R)·ω in coastdown (no rider torque).
/// Multiply by I_crank·ω to get steady-state power dissipation.
///
/// Coefficients from the CSC-based fit, 15 clean coastdowns spanning R=1..45
/// (RMS 0.016/s). Above R≈45 the rider can't reach 125 rpm, so coastdowns are
/// too short to fit cleanly; the linear extrapolation is consistent with
/// pedal-feel up to the R=100 hard stop.
///
/// `bFriction` is the y-intercept at R=0, i.e. residual drag with the dial at
/// its bottom of travel. Despite the name, it is NOT classical Coulomb friction
/// (decay is exponential not linear-in-time, ruling that out). It lumps belt
/// drag, bearing drag, and any residual eddy drag from the magnet at its
/// mechanical home position. The decomposition of `b` doesn't matter for the
/// power correction — what matters is that λ(R) matches measurement.
class Constants {
  static const double aBrake = 0.00573;     // 1/(s · R-unit)
  static const double bFriction = 0.0359;   // 1/s — residual drag, not Coulomb friction
  static const double iCrank = 11.0;        // kg·m² (effective, at crank)

  /// FTMS BLE caps cadence at 125 rpm (uint16 at 0.5 rpm); treat ≥124 as suspect
  /// and prefer CSC-derived cadence in that case.
  static const double cadCap = 124.0;

  /// IC8 hard mechanical cap; the brake locks the crank, no useful info.
  static const int rCap = 100;

  /// Median-filter window for the noisy R sensor (jitters ±1 even untouched).
  static const int rSmoothWindow = 5;

  /// Central-difference window for ω̇ (samples). Smoothed lightly to reduce
  /// 1Hz quantization noise; keeps responsiveness for sprint ramps.
  static const int omegaDotWindow = 3;
}
