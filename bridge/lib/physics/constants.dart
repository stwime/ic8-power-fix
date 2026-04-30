/// Protocol-level constants (BLE limits, filter windows). For tunable physics
/// constants (a_brake, b_friction, I_crank), see [Calibration].
class Constants {
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
