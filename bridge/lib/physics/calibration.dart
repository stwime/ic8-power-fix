import 'package:shared_preferences/shared_preferences.dart';

/// Tunable physics constants — split from [Constants] so they can be edited at
/// runtime via the settings screen and persisted across app launches.
///
/// Defaults come from analysis/spindown_fit.py on the CSC-based coastdown set
/// (R=1..45, RMS 0.016/s) plus an outdoor anchor for I_crank.
class Calibration {
  static const double defaultABrake = 0.00573;     // 1/(s · R-unit)
  static const double defaultBFriction = 0.0359;   // 1/s
  static const double defaultICrank = 12.4;        // kg·m² (effective, at crank)

  /// Bounds for the I_crank slider. Wide enough to cover plausible re-pinning
  /// without letting a stray tap drive power off into nonsense territory.
  static const double iCrankMin = 8.0;
  static const double iCrankMax = 18.0;

  static const String _keyABrake = 'cal.aBrake';
  static const String _keyBFriction = 'cal.bFriction';
  static const String _keyICrank = 'cal.iCrank';

  double aBrake;
  double bFriction;
  double iCrank;

  Calibration._({
    required this.aBrake,
    required this.bFriction,
    required this.iCrank,
  });

  /// In-memory only, no persistence. For tests.
  Calibration.defaults()
      : aBrake = defaultABrake,
        bFriction = defaultBFriction,
        iCrank = defaultICrank;

  static Future<Calibration> load() async {
    final prefs = await SharedPreferences.getInstance();
    return Calibration._(
      aBrake: prefs.getDouble(_keyABrake) ?? defaultABrake,
      bFriction: prefs.getDouble(_keyBFriction) ?? defaultBFriction,
      iCrank: prefs.getDouble(_keyICrank) ?? defaultICrank,
    );
  }

  Future<void> setICrank(double v) async {
    iCrank = v.clamp(iCrankMin, iCrankMax);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_keyICrank, iCrank);
  }

  Future<void> setABrake(double v) async {
    aBrake = v;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_keyABrake, aBrake);
  }

  Future<void> setBFriction(double v) async {
    bFriction = v;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_keyBFriction, bFriction);
  }

  /// Replace the brake/friction fit (typically from a coastdown calibration).
  Future<void> setBrakeFit({required double aBrake, required double bFriction}) async {
    this.aBrake = aBrake;
    this.bFriction = bFriction;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_keyABrake, aBrake);
    await prefs.setDouble(_keyBFriction, bFriction);
  }

  Future<void> resetToDefaults() async {
    aBrake = defaultABrake;
    bFriction = defaultBFriction;
    iCrank = defaultICrank;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_keyABrake);
    await prefs.remove(_keyBFriction);
    await prefs.remove(_keyICrank);
  }

  bool get isAtDefaults =>
      aBrake == defaultABrake &&
      bFriction == defaultBFriction &&
      iCrank == defaultICrank;
}
