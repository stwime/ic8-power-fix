/// Protocol-level constants (BLE limits, filter windows). For tunable physics
/// constants (brake c, β, τ_c, I_crank, Power scale), see [Calibration].
class Constants {
  /// The IC8 caps its FTMS cadence at 125 rpm; treat ≥124 as capped and use
  /// the CSC crank-count cadence when it is available.
  static const double cadCap = 124.0;

  /// IC8 hard mechanical cap; the brake locks the crank, no useful info.
  static const int rCap = 100;

  /// Smallest R change taken at once. Untouched, the dial reading flickers
  /// by 1 or 2 for single samples; a turn moves it 3 or more per sample.
  /// See [ResistanceFilter].
  static const int rJump = 3;

  /// Central-difference window for ω̇ (samples). Smoothed lightly to reduce
  /// 1Hz quantization noise; keeps responsiveness for sprint ramps.
  static const int omegaDotWindow = 3;
}
