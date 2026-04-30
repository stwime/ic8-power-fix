import 'dart:math' as math;

import 'package:shared_preferences/shared_preferences.dart';

/// Tunable physics constants — split from [Constants] so they can be edited at
/// runtime via the settings screen and persisted across app launches.
///
/// Brake/residual drag use a power-law form
///     λ(R) = α · R^p + β
/// fit on cumulative-angle spindowns (analysis/fit_lambda_R_v3.py).
/// Over IC8's R ∈ [0, 89] the brake never reaches saturation, so the
/// earlier 4-parameter Hill form λ(R) = α·R^p/(R^p+R_c^p) + β collapsed to
/// a power-law with R_c far above the dial range — adding R_c didn't
/// improve fit quality (wRMS) and left R_c unidentified. The simpler
/// power-law has the same wRMS with one fewer parameter.
///
/// The exponent [defaultPower] is held fixed across bikes — it reflects
/// the brake-mechanism geometry (eddy-current B²(d) coupling), not
/// per-unit calibration variation, so we don't expose it in the
/// auto-calibration flow. Only (α, β) are fit per bike. See [Coastdown.fitBrake].
class Calibration {
  static const double defaultAlpha = 0.001020;  // 1/s · R^-p — power-law amp
  static const double defaultBeta = 0.0252;     // 1/s — residual drag at R=0
  static const double defaultPower = 1.646;     // dimensionless — brake exponent
  static const double defaultICrank = 9.3;      // kg·m² (effective, at crank)

  /// Bounds for the I_crank slider. Wide enough to cover any plausible
  /// indoor-cycle bike, from a light entry-level FTMS bike with a small
  /// flywheel up through heavy commercial spin bikes.
  static const double iCrankMin = 2.0;
  static const double iCrankMax = 40.0;

  static const String _keyAlpha = 'cal.alpha';
  static const String _keyBeta = 'cal.beta';
  static const String _keyICrank = 'cal.iCrank';

  double alpha;
  double beta;
  double iCrank;

  Calibration._({
    required this.alpha,
    required this.beta,
    required this.iCrank,
  });

  /// In-memory only, no persistence. For tests.
  Calibration.defaults()
      : alpha = defaultAlpha,
        beta = defaultBeta,
        iCrank = defaultICrank;

  static Future<Calibration> load() async {
    final prefs = await SharedPreferences.getInstance();
    return Calibration._(
      alpha: prefs.getDouble(_keyAlpha) ?? defaultAlpha,
      beta: prefs.getDouble(_keyBeta) ?? defaultBeta,
      iCrank: prefs.getDouble(_keyICrank) ?? defaultICrank,
    );
  }

  /// λ(R) = α · R^p + β. R clamped at 0 since the dial is physically
  /// nonnegative and 0^p evaluates funny at fractional p.
  double lambdaAt(double r) {
    final rPos = r > 0 ? r : 0.0;
    if (rPos == 0.0) return beta;
    return alpha * math.pow(rPos, defaultPower).toDouble() + beta;
  }

  Future<void> setICrank(double v) async {
    iCrank = v.clamp(iCrankMin, iCrankMax);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_keyICrank, iCrank);
  }

  Future<void> setAlpha(double v) async {
    alpha = v;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_keyAlpha, alpha);
  }

  Future<void> setBeta(double v) async {
    beta = v;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_keyBeta, beta);
  }

  /// Replace the brake/residual fit (typically from a coastdown calibration).
  Future<void> setBrakeFit({
    required double alpha,
    required double beta,
  }) async {
    this.alpha = alpha;
    this.beta = beta;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_keyAlpha, alpha);
    await prefs.setDouble(_keyBeta, beta);
  }

  Future<void> resetToDefaults() async {
    alpha = defaultAlpha;
    beta = defaultBeta;
    iCrank = defaultICrank;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_keyAlpha);
    await prefs.remove(_keyBeta);
    await prefs.remove(_keyICrank);
    // Drop any stored R_c from the prior Hill calibration so a future
    // Calibration.load() doesn't see an orphan key.
    await prefs.remove('cal.rcDial');
  }

  bool get isAtDefaults =>
      alpha == defaultAlpha &&
      beta == defaultBeta &&
      iCrank == defaultICrank;
}
