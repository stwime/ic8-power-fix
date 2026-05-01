import 'dart:math' as math;

import 'package:shared_preferences/shared_preferences.dart';

/// Tunable physics constants — split from [Constants] so they can be edited at
/// runtime via the settings screen and persisted across app launches.
///
/// Brake/residual drag use a saturating Hill form
///     λ(R) = β + α · R^p / (R^p + R_c^p)
/// fit on per-revolution ω(t) trajectories from a hand-curated set of
/// 42 video-tracked spindowns spanning R = 0 to 93
/// (analysis/fit_hill.py). The data physically shows saturation at
/// high R — the per-segment λ̂ at R = 80–93 sits flat at ~2.0–2.5 1/s
/// rather than continuing to rise — so the previous power-law form
/// (which has a constant log-log slope by construction) was
/// systematically wrong above R ≈ 30. The new fit lowers weighted RSS
/// by 9.2× on the same dataset.
///
///   α   = saturation amplitude (1/s) — per-bike (magnet × flywheel)
///   β   = residual drag at R=0 (1/s) — per-bike (bearings + air)
///   R_c = half-saturation dial position — geometry, fixed across bikes
///   p   = transition sharpness — geometry, fixed across bikes
///
/// β is pinned in the offline fit to the directly-measured per-segment
/// λ̂ at R=0 (median 0.0396 1/s) rather than left to float; the free
/// fit lifts β to ~0.08 because Hill's R^p shape can't simultaneously
/// hit R=0 and the steep R=10–30 rise, but R<10 is not a practical
/// riding region so pinning β makes the low-R end physically honest
/// without measurably hurting RSS at R≥15.
///
/// [defaultRcDial] and [defaultPower] are geometry-driven — they
/// reflect the eddy-brake mechanism (gap-vs-dial mapping, B²(d)
/// coupling), not per-unit calibration variation — so we don't expose
/// them in the auto-calibration flow. Only (α, β) are fit per bike.
/// See [Coastdown.fitBrake].
class Calibration {
  // Hill λ(R) = β + α·R^p/(R^p + R_c^p). Constants from
  // analysis/fit_hill.py on the hand-curated all_spindowns.csv (video
  // sources only, R relabeled from the BLE log).
  static const double defaultAlpha = 2.3623;    // 1/s — saturation amplitude
  static const double defaultBeta = 0.0396;     // 1/s — residual drag at R=0
  static const double defaultRcDial = 54.58;    // half-saturation dial position
  static const double defaultPower = 3.41;      // transition sharpness
  static const double defaultICrank = 9.14;     // kg·m² (effective, at crank)

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

  /// λ(R) = β + α · R^p / (R^p + R_c^p), with R_c and p held fixed.
  /// R clamped at 0; at R=0 the eddy term is zero so λ(0) = β.
  double lambdaAt(double r) {
    final rPos = r > 0 ? r : 0.0;
    if (rPos == 0.0) return beta;
    final rp = math.pow(rPos, defaultPower).toDouble();
    final rcp = math.pow(defaultRcDial, defaultPower).toDouble();
    return beta + alpha * rp / (rp + rcp);
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
