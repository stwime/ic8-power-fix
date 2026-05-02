import 'dart:math' as math;

import 'package:shared_preferences/shared_preferences.dart';

/// Tunable physics constants — split from [Constants] so they can be edited at
/// runtime via the settings screen and persisted across app launches.
///
/// Brake torque uses the strict-Wouterse permanent-magnet eddy-brake model:
///
///     τ_brake(R, ω) = α · H(R) · 2x / (1 + x²)   with  x = κ · H(R) · ω
///     H(R) = R^p / (R^p + R_h^p)
///
/// fit on per-revolution ω(t) trajectories from a hand-curated set of
/// 42 video-tracked spindowns spanning R = 0 to 93 (analysis/fit_wouterse.py).
///
/// Three regimes in ω at fixed R:
///   ω << ω_c :  τ ≈ (2τ_max/ω_c)·ω           (linear damping)
///   ω = ω_c  :  τ = τ_max  with  τ_max = α·H(R), ω_c = 1/(κ·H(R))
///   ω >> ω_c :  τ ≈ 2τ_max·(ω_c/ω)           (induced field opposes source)
///
/// The strict coupling τ_max·ω_c = α/κ is a single constant set by disc
/// geometry (conductivity × thickness × pole-area × radius²) — so τ_max(R)
/// and 1/ω_c(R) share the single Hill shape H(R). Plus a residual drag
/// τ_residual(ω) = I·β·ω representing bearings + air at R = 0.
///
///   α   = peak torque amplitude (N·m) — per-bike (magnet × flywheel)
///   β   = residual drag at R=0 (1/s) — per-bike (bearings + air)
///   κ   = 1/ω_c at saturation (s/rad) — geometry, fixed across bikes
///   R_h = Hill midpoint of B²(R) — bike-firmware-calibration × geometry
///   p   = Hill sharpness — bike-firmware-calibration × geometry
///
/// [defaultRh] and [defaultP] absorb both the eddy-brake gap-vs-dial
/// physics and whatever non-linear mapping the IC8's firmware applies
/// between dial position and physical brake state — they're inseparable
/// from this dataset alone, so they live with the geometry constants and
/// aren't exposed in auto-calibration. Only (α, β) are fit per bike.
/// See [Coastdown.fitBrake].
///
/// [defaultICrank] is the direct flywheel-geometry calculation:
/// I_flywheel = 0.461 kg·m² (46 cm dia, 18 kg, two rings r=13–18 cm at
/// 2.5× thickness) and gear ratio g = 4.5 (measured) gives
/// I_crank = g²·I_flywheel ≈ 9.34 kg·m².
class Calibration {
  // Wouterse params from analysis/fit_wouterse.py on the hand-curated
  // video-only spindowns (strict τ_max ∝ B², ω_c ∝ 1/B² coupling).
  static const double defaultAlpha = 500.0;     // N·m — peak torque amplitude
  static const double defaultBeta = 0.0343;     // 1/s — residual drag at R=0
  static const double defaultRh = 167.64;       // Hill midpoint
  static const double defaultP = 1.07;          // Hill sharpness
  static const double defaultKappa = 0.1465;    // s/rad — 1/ω_c at saturation
  static const double defaultICrank = 9.34;     // kg·m² (effective, at crank)
  static const double defaultPowerScale = 1.0;  // user-facing α multiplier

  /// Bounds for the Power scale slider — multiplier on α applied at every
  /// τ_brake evaluation. 0.5–2.0 covers the range of unit-to-unit
  /// firmware-calibration spread we'd plausibly see across IC8s.
  static const double powerScaleMin = 0.5;
  static const double powerScaleMax = 2.0;

  // Bumped key suffix so a pre-Wouterse stored α/β (different units, ~10⁴
  // smaller) doesn't load and produce nonsense. resetToDefaults() also
  // wipes the old keys so they don't linger.
  static const String _keyAlpha = 'cal.alphaW';
  static const String _keyBeta = 'cal.betaW';
  static const String _keyICrank = 'cal.iCrank';
  static const String _keyPowerScale = 'cal.powerScale';

  double alpha;
  double beta;
  double iCrank;
  double powerScale;

  Calibration._({
    required this.alpha,
    required this.beta,
    required this.iCrank,
    required this.powerScale,
  });

  /// In-memory only, no persistence. For tests.
  Calibration.defaults()
      : alpha = defaultAlpha,
        beta = defaultBeta,
        iCrank = defaultICrank,
        powerScale = defaultPowerScale;

  static Future<Calibration> load() async {
    final prefs = await SharedPreferences.getInstance();
    return Calibration._(
      alpha: prefs.getDouble(_keyAlpha) ?? defaultAlpha,
      beta: prefs.getDouble(_keyBeta) ?? defaultBeta,
      iCrank: prefs.getDouble(_keyICrank) ?? defaultICrank,
      powerScale: prefs.getDouble(_keyPowerScale) ?? defaultPowerScale,
    );
  }

  /// H(R) = R^p / (R^p + R_h^p). The single shape function driving both
  /// τ_max(R) = α·H(R) and 1/ω_c(R) = κ·H(R) under strict Wouterse
  /// coupling. Continuous, monotone, zero at R=0, → 1 at R→∞.
  static double _hill(double r) {
    if (r <= 0) return 0.0;
    final rp = math.pow(r, defaultP).toDouble();
    final rhp = math.pow(defaultRh, defaultP).toDouble();
    return rp / (rp + rhp);
  }

  /// Brake torque + bearing/air residual drag at the crank, in N·m.
  /// Sum of the eddy-current Wouterse term and a linear residual. The
  /// user-facing [powerScale] multiplies α so the Settings slider scales
  /// steady-state brake power.
  double tauBrakeAt(double r, double omega) {
    final h = _hill(r);
    final x = defaultKappa * h * omega;
    final tauEddy = (alpha * powerScale) * h * 2.0 * x / (1.0 + x * x);
    final tauResidual = iCrank * beta * omega;
    return tauEddy + tauResidual;
  }

  /// Steady-state brake power = τ_brake(R, ω)·ω, in W.
  double brakePowerAt(double r, double omega) {
    return tauBrakeAt(r, omega) * omega;
  }

  /// Effective decay rate λ(R) in the low-ω linear regime. Equal to
  /// −d(ln ω)/dt for an unloaded coastdown at small ω. Used by
  /// [Coastdown.fitBrake] which fits log-linear λ̂ on user coastdowns.
  ///   λ_lin(R) = β + 2·(α·s)·κ·H(R)² / I,  s = powerScale
  double lambdaLinearAt(double r) {
    final h = _hill(r);
    return beta + 2.0 * (alpha * powerScale) * defaultKappa * h * h / iCrank;
  }

  Future<void> setICrank(double v) async {
    iCrank = v;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_keyICrank, iCrank);
  }

  Future<void> setPowerScale(double v) async {
    powerScale = v.clamp(powerScaleMin, powerScaleMax);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_keyPowerScale, powerScale);
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
    powerScale = defaultPowerScale;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_keyAlpha);
    await prefs.remove(_keyBeta);
    await prefs.remove(_keyICrank);
    await prefs.remove(_keyPowerScale);
    // Drop any stored keys from prior calibration models so a future
    // Calibration.load() doesn't see orphan values.
    await prefs.remove('cal.alpha');
    await prefs.remove('cal.beta');
    await prefs.remove('cal.rcDial');
  }

  bool get isAtDefaults =>
      alpha == defaultAlpha &&
      beta == defaultBeta &&
      iCrank == defaultICrank &&
      powerScale == defaultPowerScale;
}
