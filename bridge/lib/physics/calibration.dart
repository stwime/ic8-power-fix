import 'dart:math' as math;

import 'package:shared_preferences/shared_preferences.dart';

/// Tunable physics constants — split from [Constants] so they can be edited at
/// runtime via the settings screen and persisted across app launches.
///
/// Brake torque uses the strict-Wouterse permanent-magnet eddy-brake model
/// plus a two-term residual drag (Coulomb + viscous):
///
///     τ_brake(R, ω) = α · H(R) · 2x / (1 + x²) + τ_c + I · β · ω
///       with  x = κ · H(R) · ω
///       and  H(R) = w · R^p1/(R^p1 + R_h1^p1)
///                  + (1−w) · R^p2/(R^p2 + R_h2^p2)
///
/// fit on per-revolution ω(t) trajectories from a hand-curated set of
/// 46 video-tracked spindowns spanning R = 0 to 93 (analysis/fit_wouterse.py).
///
/// Three regimes in ω at fixed R (eddy term only — residual drag adds on
/// independently):
///   ω << ω_c :  τ_eddy ≈ (2τ_max/ω_c)·ω      (linear damping)
///   ω = ω_c  :  τ_eddy = τ_max  with  τ_max = α·H(R), ω_c = 1/(κ·H(R))
///   ω >> ω_c :  τ_eddy ≈ 2τ_max·(ω_c/ω)      (induced field opposes source)
///
/// The strict coupling τ_max·ω_c = α/κ is a single constant set by disc
/// geometry (conductivity × thickness × pole-area × radius²) — so τ_max(R)
/// and 1/ω_c(R) share the single H(R) shape.
///
/// H(R) is a sum of two Hill curves: the empirical B²(R) has a mid-
/// band shoulder a single sigmoid can't bend to. The two-Hill
/// decomposition is empirical — the IC8 has two magnet pairs but both
/// engage across the same R range, so the second Hill doesn't map
/// cleanly to one of them.
///
/// Residual drag has two components, both R-independent:
///   τ_c       = Coulomb (bearings + belt + seal friction) — constant in ω
///   I · β · ω = viscous (windage + air-film) — linear in ω
/// Isolating the R=0 spin-downs (no eddy contribution) and fitting drag-
/// shape alone, Coulomb + viscous beats viscous-only by ~15× in RSS
/// (analysis/residual_drag_shape.py). Physically that's right: bearing and
/// belt friction are roughly constant in ω, not viscous.
///
///   α     = peak torque amplitude (N·m) — anchored to 1000 W spec, shared
///           across IC8/IC4/C6/C7 (same magnets, same actuator, same firmware)
///   β     = viscous residual drag (1/s) — per-bike (windage + air-film)
///   τ_c   = Coulomb residual drag (N·m) — per-bike default (bearings + belt)
///   κ     = 1/ω_c at saturation (s/rad) — geometry, fixed across bikes,
///           pinned so the 1000 W anchor stays meaningful when H(R) is free
///   w     = mixing weight on H1 (broad) — H-shape, bike-firmware × geom
///   R_h1, p1 = broad Hill midpoint and sharpness — H-shape
///   R_h2, p2 = sharp Hill midpoint and sharpness — H-shape
///
/// The H-shape constants ({w, R_h1, p1, R_h2, p2}) absorb both the eddy-
/// brake gap-vs-dial physics and whatever non-linear mapping the IC8's
/// firmware applies between dial position and physical brake state —
/// they're inseparable from this dataset alone, so they live with the
/// geometry constants and aren't exposed in auto-calibration. Only β is
/// fit per bike — α and I_crank are structurally degenerate in spin-down
/// data (only their ratio appears in I·ω̇ = -τ), so per-bike α fitting
/// just absorbs I_crank deviations into a wrong α. Absolute scale is the
/// [powerScale] slider's job, against an external power meter.
/// See [Coastdown.fitBrake].
///
/// Anchoring chain — three independent inputs:
///
///   1. [defaultICrank] = 7.55 kg·m², pinned against an outdoor 4iiii
///      crank-meter session. The bridge's total steady-state output is
///      P = I·λ_total(R)·ω² + τ_c·ω where λ_total and τ_c come from
///      the spin-down data, so I is the only knob that sets absolute
///      output without invalidating the fit.
///
///   2. [defaultAlpha] pinned to the manufacturer's 1000 W max-output
///      spec. Under strict Wouterse, the asymptotic peak brake power at
///      any single ω is α/κ. With α = 165 N·m the fit lands κ = 0.1585
///      and α/κ = 1041 W — matching the 1000 W rating to ~4%. The
///      saturation bell-curve isn't directly observed in our coastdowns
///      (which sit in the linear-damping regime ω << ω_c), but finite
///      magnetic flux through the disc bounds the peak absorbable power
///      as a real physical constraint.
///
///   3. H(R) shape ({w, R_h1, p1, R_h2, p2}), β, and τ_c from a global
///      fit on 46 video-tracked spindowns spanning R = 0 to 93, with α
///      and κ pinned to the 1041 W anchor and I_crank pinned to 7.55.
///      RSS = 0.0188 across 51,792 samples.
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
  //
  // α = 165 and κ = 0.1585 anchor α/κ ≈ 1041 W against the 1000 W
  // marketing max-output spec; the saturation bell-curve isn't
  // directly observed in the linear-regime coastdowns, so this anchor
  // sets where the ceiling sits. I_crank = 7.55 is pinned by the
  // outdoor 4iiii crank meter (Lunch_Ride.fit May 2026,
  // Lunch_Ride_harder_effort.fit Sept 2025); it's the only knob in
  // P = I·λ_total(R)·ω² + τ_c·ω that sets absolute output without
  // invalidating the spin-down fit. H, β, τ_c are fit globally at
  // those pinned α, κ, I — RSS = 0.0188 across 51,792 samples.
  //
  // Residual drag splits into Coulomb (τ_c, bearings + belt) and
  // viscous (β, windage); R=0 spin-downs prefer this split by ~15×
  // in RSS over viscous-only. H(R) is a sum of two Hill curves.
  static const double defaultAlpha = 165.0;     // N·m — peak torque amplitude (1000 W spec anchor)
  static const double defaultBeta = 0.0154;     // 1/s — viscous residual drag
  static const double defaultTauC = 1.1468;     // N·m — Coulomb residual drag
  static const double defaultW = 0.5994;        // mix weight on H1 (broad)
  static const double defaultRh1 = 185.036;     // broad Hill midpoint
  static const double defaultP1 = 0.669;        // broad Hill sharpness
  static const double defaultRh2 = 59.372;      // sharp Hill midpoint
  static const double defaultP2 = 2.246;        // sharp Hill sharpness
  static const double defaultKappa = 0.1585;    // s/rad — 1/ω_c at saturation (1000 W spec anchor)
  static const double defaultICrank = 7.55;     // kg·m² (outdoor-PM pinned)
  static const double defaultPowerScale = 1.00; // coupled α + I_crank scale

  /// Bounds for the Power scale slider — coupled multiplier on α and
  /// I_crank applied at every output evaluation. 0.5–2.0 covers the range
  /// of unit-to-unit firmware-calibration spread we'd plausibly see across
  /// IC8s on top of the 1.00 default.
  static const double powerScaleMin = 0.5;
  static const double powerScaleMax = 2.0;

  // Storage keys are versioned so that bumping a default invalidates
  // any value persisted under a prior version.
  static const String _keyAlpha = 'cal.alpha.v10';
  static const String _keyBeta = 'cal.betaW.v10';
  static const String _keyICrank = 'cal.iCrank.v10';
  static const String _keyPowerScale = 'cal.powerScale.v9';

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

  /// H(R) = w·R^p1/(R^p1 + R_h1^p1) + (1−w)·R^p2/(R^p2 + R_h2^p2).
  /// Sum-of-two-Hills shape driving both τ_max(R) = α·H(R) and
  /// 1/ω_c(R) = κ·H(R) under strict Wouterse coupling. Continuous,
  /// monotone, zero at R=0, asymptotes to a constant ≤ 1 at R→∞.
  static double _hillTerm(double r, double rH, double p) {
    final rp = math.pow(r, p).toDouble();
    final rhp = math.pow(rH, p).toDouble();
    return rp / (rp + rhp);
  }

  /// Public accessor for the H(R) shape function so other physics modules
  /// (e.g. [Coastdown.fitBrake]) can reuse the same brake-shape definition
  /// without re-deriving the Hill coefficients.
  static double hillAt(double r) {
    if (r <= 0) return 0.0;
    return defaultW * _hillTerm(r, defaultRh1, defaultP1)
        + (1.0 - defaultW) * _hillTerm(r, defaultRh2, defaultP2);
  }

  static double _hill(double r) => hillAt(r);

  /// Effective inertia at the crank after the user-facing [powerScale].
  /// Used both inside [tauBrakeAt] (for the residual-drag I·β·ω term)
  /// and by [Corrector] for the KE term I·ω·dω/dt, so both the steady
  /// and the transient terms scale linearly with [powerScale].
  double get effectiveICrank => iCrank * powerScale;

  /// Brake torque + Coulomb + viscous residual drag at the crank, in N·m.
  /// Sum of the eddy-current Wouterse term, a constant Coulomb term, and
  /// a linear viscous term. The user-facing [powerScale] multiplies α
  /// (eddy term), I_crank (viscous residual term), and τ_c (Coulomb
  /// residual term) by the same factor, so τ_brake scales linearly with
  /// [powerScale] without distorting cadence or R shape.
  double tauBrakeAt(double r, double omega) {
    final h = _hill(r);
    final x = defaultKappa * h * omega;
    final tauEddy = (alpha * powerScale) * h * 2.0 * x / (1.0 + x * x);
    final tauResidual = powerScale * (defaultTauC + iCrank * beta * omega);
    return tauEddy + tauResidual;
  }

  /// Steady-state brake power = τ_brake(R, ω)·ω, in W.
  double brakePowerAt(double r, double omega) {
    return tauBrakeAt(r, omega) * omega;
  }

  /// Effective decay rate λ(R) in the low-ω linear regime. Equal to
  /// −d(ln ω)/dt for an unloaded coastdown at small ω, ignoring the
  /// constant Coulomb contribution (which shows up as a small additive
  /// term, not as a multiplicative rate). Used by [Coastdown.fitBrake]
  /// which fits log-linear λ̂ on user coastdowns — that estimator is
  /// slightly biased high by Coulomb at low ω, and the per-bike β fit
  /// silently absorbs the bias. The bias is ~5–10% of λ at typical
  /// riding cadences (70–110 rpm).
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
    await prefs.remove('cal.alpha.v4');
    await prefs.remove('cal.betaW.v4');
    await prefs.remove('cal.iCrank.v4');
    await prefs.remove('cal.powerScale.v4');
    await prefs.remove('cal.alpha.v5');
    await prefs.remove('cal.betaW.v5');
    await prefs.remove('cal.iCrank.v5');
    await prefs.remove('cal.powerScale.v5');
    await prefs.remove('cal.alpha.v6');
    await prefs.remove('cal.betaW.v6');
    await prefs.remove('cal.iCrank.v6');
    await prefs.remove('cal.powerScale.v6');
    await prefs.remove('cal.alpha.v7');
    await prefs.remove('cal.betaW.v7');
    await prefs.remove('cal.iCrank.v7');
    await prefs.remove('cal.powerScale.v7');
    await prefs.remove('cal.alpha.v8');
    await prefs.remove('cal.betaW.v8');
    await prefs.remove('cal.iCrank.v8');
    await prefs.remove('cal.powerScale.v8');
  }

  bool get isAtDefaults =>
      alpha == defaultAlpha &&
      beta == defaultBeta &&
      iCrank == defaultICrank &&
      powerScale == defaultPowerScale;
}
