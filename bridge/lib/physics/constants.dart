/// Constants from analysis/correct_power.py and analysis/spindown_fit.py.
///
/// Spin-down fit gave: λ(R) = a·R + b per second, where
/// dω/dt = -λ(R)·ω in coastdown (no rider torque).
/// Multiply by I_crank·ω to get steady-state power dissipation.
///
/// Coefficients are from the CSC-based fit (R=5..33, RMS 0.012/s). High-R
/// coastdowns (R>50) start below 95 rpm and decay in 3-4 seconds, which is
/// too short to fit cleanly given 1 Hz CSC sampling — the linear extrapolation
/// is consistent with both the clean low-R fits and rider pedal-feel.
class Constants {
  static const double aBrake = 0.00673;     // 1/(s · R-unit)
  static const double bFriction = 0.0320;   // 1/s
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
