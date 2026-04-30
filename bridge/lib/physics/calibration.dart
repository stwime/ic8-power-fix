import 'dart:math' as math;

import 'package:shared_preferences/shared_preferences.dart';

/// Tunable physics constants — split from [Constants] so they can be edited at
/// runtime via the settings screen and persisted across app launches.
///
/// Brake/residual drag use a Hill form
///     λ(R) = α · R^p / (R^p + R_c^p) + β
/// fit on the pooled CSC-based per-revolution coastdown set
/// (analysis/spindown_fit.py). The Hill form is physics-derived from the
/// eddy-current B²(d) coupling: τ ∝ B²·ω with B² a power-law in
/// magnet-flywheel gap. It nests both the near-field (R << R_c, ~linear) and
/// far-field (R >> R_c, saturating) regimes in a single 4-parameter shape.
/// The earlier saturating form λ(R) = α·(1 − exp(−R/R_c)) + β under-fit
/// the moderate-R data because real B²(d) is power-law not exponential.
///
/// [rcDial] defaults to the IC8 reference ([defaultRcDial]) and is also a
/// per-bike calibration parameter: the fitter promotes from 2-param (α, β)
/// to 3-param (α, β, R_c) when the user's coastdown set spans enough R to
/// pin the half-max knee. See [Coastdown.fitBrake]. The Hill exponent
/// [pHill] is held fixed across bikes — it reflects the brake-mechanism
/// geometry, not per-unit calibration variation, so we don't expose it in
/// the auto-calibration flow.
class Calibration {
  static const double defaultAlpha = 0.207;     // 1/s — Hill amplitude
  static const double defaultBeta = 0.034;      // 1/s — residual drag at R=0
  static const double defaultRcDial = 38.5;     // R-units — Hill half-max knee
  static const double defaultPHill = 1.90;      // dimensionless — Hill exponent
  static const double defaultICrank = 24.5;     // kg·m² (effective, at crank)

  /// Bounds for the I_crank slider. Wide enough to cover any plausible
  /// indoor-cycle bike, from a light entry-level FTMS bike with a small
  /// flywheel up through heavy commercial spin bikes.
  static const double iCrankMin = 2.0;
  static const double iCrankMax = 40.0;

  /// Bounds for fitted R_c (and the manual-edit field). Below ~10 the Hill
  /// curve is a near-step at low R; above ~200 it's effectively linear in R
  /// over the dial's 1..100 range.
  static const double rcDialMin = 10.0;
  static const double rcDialMax = 200.0;

  static const String _keyAlpha = 'cal.alpha';
  static const String _keyBeta = 'cal.beta';
  static const String _keyRcDial = 'cal.rcDial';
  static const String _keyICrank = 'cal.iCrank';

  double alpha;
  double beta;
  double rcDial;
  double iCrank;

  Calibration._({
    required this.alpha,
    required this.beta,
    required this.rcDial,
    required this.iCrank,
  });

  /// In-memory only, no persistence. For tests.
  Calibration.defaults()
      : alpha = defaultAlpha,
        beta = defaultBeta,
        rcDial = defaultRcDial,
        iCrank = defaultICrank;

  static Future<Calibration> load() async {
    final prefs = await SharedPreferences.getInstance();
    return Calibration._(
      alpha: prefs.getDouble(_keyAlpha) ?? defaultAlpha,
      beta: prefs.getDouble(_keyBeta) ?? defaultBeta,
      rcDial: prefs.getDouble(_keyRcDial) ?? defaultRcDial,
      iCrank: prefs.getDouble(_keyICrank) ?? defaultICrank,
    );
  }

  /// λ(R) = α · R^p / (R^p + R_c^p) + β. R clamped at 0 since the dial is
  /// physically nonnegative and 0^p evaluates funny at fractional p.
  double lambdaAt(double r) {
    final rPos = r > 0 ? r : 0.0;
    final rp = math.pow(rPos, defaultPHill).toDouble();
    final rcp = math.pow(rcDial, defaultPHill).toDouble();
    return alpha * rp / (rp + rcp) + beta;
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

  Future<void> setRcDial(double v) async {
    rcDial = v.clamp(rcDialMin, rcDialMax);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_keyRcDial, rcDial);
  }

  /// Replace the brake/residual fit (typically from a coastdown calibration).
  /// [rc] is the Hill half-max knee — pass the fitted value when the
  /// calibration set spans enough R to identify it, otherwise the prior (e.g.
  /// the existing [rcDial], or [defaultRcDial] for a fresh user).
  Future<void> setBrakeFit({
    required double alpha,
    required double beta,
    required double rc,
  }) async {
    this.alpha = alpha;
    this.beta = beta;
    rcDial = rc.clamp(rcDialMin, rcDialMax);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_keyAlpha, alpha);
    await prefs.setDouble(_keyBeta, beta);
    await prefs.setDouble(_keyRcDial, rcDial);
  }

  Future<void> resetToDefaults() async {
    alpha = defaultAlpha;
    beta = defaultBeta;
    rcDial = defaultRcDial;
    iCrank = defaultICrank;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_keyAlpha);
    await prefs.remove(_keyBeta);
    await prefs.remove(_keyRcDial);
    await prefs.remove(_keyICrank);
  }

  bool get isAtDefaults =>
      alpha == defaultAlpha &&
      beta == defaultBeta &&
      rcDial == defaultRcDial &&
      iCrank == defaultICrank;
}
