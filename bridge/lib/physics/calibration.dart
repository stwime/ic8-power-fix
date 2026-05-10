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
/// 46 video-tracked spindowns spanning R = 0 to 93 (analysis/fit_wouterse.py).
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
/// Anchoring chain — three independent inputs, zero perceived-effort
/// calibration:
///
///   1. [defaultICrank] from flywheel geometry. 18 kg flywheel (manufacturer
///      spec), 0.5 cm thick Al disc at 23 cm OD (= 2.24 kg by π·R²·t·ρ_Al),
///      iron belt at R = 12–16 cm (= 15.76 kg by mass conservation,
///      ~2.85 cm thick on each side of the disc by ρ_Fe = 7870 kg/m³ —
///      consistent with visual inspection of 2–3 cm protrusion each side).
///        I_Al_disc  = ½·m·R²       = 0.059 kg·m²
///        I_belt     = m·r_eff²      = 0.315 kg·m²   (r_eff² = 0.0200)
///        I_flywheel = 0.374 kg·m²
///        I_crank    = g²·I_flywheel = 7.58 kg·m²    (g = 4.5)
///
///   2. [defaultAlpha] from the manufacturer's 1000 W max-output spec.
///      Under strict Wouterse, the asymptotic peak brake power at any
///      single ω is α/κ. With α = 165 N·m the fit lands κ = 0.165 and
///      α/κ = 1001 W — matching the 1000 W rating to <1%.
///
///   3. Hill shape (R_h, p), κ, and β from a global fit on 46 video-
///      tracked spindowns spanning R = 0 to 93. RSS = 0.040 across
///      51792 samples.
///
/// All three are mutually consistent — the data, the geometry, and the
/// marketing spec land on the same calibration without invoking
/// perceived effort anywhere. Cross-check with magnet physics: at full
/// geometric overlap (R=100), the implied B inside the disc is
/// 0.30–0.35 T, the physically plausible band for the N42 sandwich
/// pairs with steel-yoke flux return shown in the brake-shoe image.
///
/// [powerScale] is a coupled absolute-scale knob: it multiplies α and
/// I_crank by the same factor, so the eddy steady term, the residual
/// drag term, and the KE term all scale by the same factor and the
/// total output is linear in [powerScale]. Cadence-shape and R-shape
/// are untouched. The decay-rate λ(R) = β + (2ακ/I)·H(R)² is
/// powerScale-invariant because α and I cancel — the bike's physical
/// coastdown rate doesn't depend on what the bridge displays.
///
/// The default 1.00 reflects the fully-anchored calibration above.
/// Tune against an external power meter when available; nothing in the
/// model claims absolute scale to better than ~10% without one.
class Calibration {
  // Wouterse params from analysis/fit_wouterse.py on 46 hand-curated
  // video-tracked spindowns (strict τ_max ∝ B², ω_c ∝ 1/B² coupling).
  // α pinned to 165 (anchored to 1000 W marketing max via α/κ = 1001 W).
  // I_crank pinned to 7.58 (anchored to flywheel geometry: 18 kg with
  // iron belt at R = 12–16 cm, ~2.85 cm thick each side).
  static const double defaultAlpha = 165.0;     // N·m — peak torque amplitude
  static const double defaultBeta = 0.0384;     // 1/s — residual drag at R=0
  static const double defaultRh = 87.608;       // Hill midpoint
  static const double defaultP = 1.196;         // Hill sharpness
  static const double defaultKappa = 0.1649;    // s/rad — 1/ω_c at saturation
  static const double defaultICrank = 7.58;     // kg·m² (effective, at crank)
  static const double defaultPowerScale = 1.00; // coupled α + I_crank scale

  /// Bounds for the Power scale slider — coupled multiplier on α and
  /// I_crank applied at every output evaluation. 0.5–2.0 covers the range
  /// of unit-to-unit firmware-calibration spread we'd plausibly see across
  /// IC8s on top of the 1.00 default.
  static const double powerScaleMin = 0.5;
  static const double powerScaleMax = 2.0;

  // v4 suffixes mark the full physics-anchored rebase: I_crank from
  // belt-geometry derivation (7.47 → 7.58), α from marketing 1000 W spec
  // (500 → 165), and Hill shape refit at the new (α, I) anchors. The
  // brake torque coefficient (2ακ/I) changed enough that loading any
  // stored v3 values under the new defaults would over- or under-shoot
  // by ~30%, so we wipe and reset.
  static const String _keyAlpha = 'cal.alpha.v4';
  static const String _keyBeta = 'cal.betaW.v4';
  static const String _keyICrank = 'cal.iCrank.v4';
  static const String _keyPowerScale = 'cal.powerScale.v4';

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

  /// Effective inertia at the crank after the user-facing [powerScale].
  /// Used both inside [tauBrakeAt] (for the residual-drag I·β·ω term)
  /// and by [Corrector] for the KE term I·ω·dω/dt, so both the steady
  /// and the transient terms scale linearly with [powerScale].
  double get effectiveICrank => iCrank * powerScale;

  /// Brake torque + bearing/air residual drag at the crank, in N·m.
  /// Sum of the eddy-current Wouterse term and a linear residual. The
  /// user-facing [powerScale] multiplies α (eddy term) and I_crank
  /// (residual term) by the same factor, so τ_brake scales linearly
  /// with [powerScale] without distorting cadence or R shape.
  double tauBrakeAt(double r, double omega) {
    final h = _hill(r);
    final x = defaultKappa * h * omega;
    final tauEddy = (alpha * powerScale) * h * 2.0 * x / (1.0 + x * x);
    final tauResidual = effectiveICrank * beta * omega;
    return tauEddy + tauResidual;
  }

  /// Steady-state brake power = τ_brake(R, ω)·ω, in W.
  double brakePowerAt(double r, double omega) {
    return tauBrakeAt(r, omega) * omega;
  }

  /// Effective decay rate λ(R) in the low-ω linear regime. Equal to
  /// −d(ln ω)/dt for an unloaded coastdown at small ω. Used by
  /// [Coastdown.fitBrake] which fits log-linear λ̂ on user coastdowns.
  ///   λ_lin(R) = β + 2·α·κ·H(R)² / I
  /// [powerScale] cancels because effective α and effective I scale
  /// together, so the modelled coastdown rate is invariant to the
  /// output-gain knob (as it should be — it's a property of the bike).
  double lambdaLinearAt(double r) {
    final h = _hill(r);
    return beta + 2.0 * alpha * defaultKappa * h * h / iCrank;
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
    await prefs.remove('cal.alphaW');
    await prefs.remove('cal.beta');
    await prefs.remove('cal.betaW');
    await prefs.remove('cal.iCrank');
    await prefs.remove('cal.rcDial');
    await prefs.remove('cal.powerScale');
    await prefs.remove('cal.powerScale.v2');
    await prefs.remove('cal.alpha.v3');
    await prefs.remove('cal.iCrank.v3');
    await prefs.remove('cal.powerScale.v3');
  }

  bool get isAtDefaults =>
      alpha == defaultAlpha &&
      beta == defaultBeta &&
      iCrank == defaultICrank &&
      powerScale == defaultPowerScale;
}
