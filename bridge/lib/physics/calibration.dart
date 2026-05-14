import 'dart:math' as math;

import 'package:shared_preferences/shared_preferences.dart';

/// Tunable physics constants — split from [Constants] so they can be edited at
/// runtime via the settings screen and persisted across app launches.
///
/// Brake torque uses the strict-Wouterse permanent-magnet eddy-brake model
/// plus a two-term residual drag (Coulomb + viscous):
///
///     τ_brake(R, ω) = α · H(R) · 2x / (1 + x²) + τ_c + I · β · ω
///       with  x = κ · H(R) · ω,    H(R) = R^p / (R^p + R_h^p)
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
/// and 1/ω_c(R) share the single Hill shape H(R).
///
/// Residual drag has two components, both R-independent:
///   τ_c       = Coulomb (bearings + belt + seal friction) — constant in ω
///   I · β · ω = viscous (windage + air-film) — linear in ω
/// Isolating the R=0 spin-downs (no eddy contribution) and fitting drag-
/// shape alone, Coulomb + viscous beats viscous-only by ~15× in RSS
/// (analysis/residual_drag_shape.py). Physically that's right: bearing and
/// belt friction are roughly constant in ω, not viscous.
///
///   α   = peak torque amplitude (N·m) — anchored to 1000 W spec, shared
///         across IC8/IC4/C6/C7 (same magnets, same actuator, same firmware)
///   β   = viscous residual drag (1/s) — per-bike (windage + air-film)
///   τ_c = Coulomb residual drag (N·m) — per-bike default (bearings + belt)
///   κ   = 1/ω_c at saturation (s/rad) — geometry, fixed across bikes
///   R_h = Hill midpoint of B²(R) — bike-firmware-calibration × geometry
///   p   = Hill sharpness — bike-firmware-calibration × geometry
///
/// [defaultRh] and [defaultP] absorb both the eddy-brake gap-vs-dial
/// physics and whatever non-linear mapping the IC8's firmware applies
/// between dial position and physical brake state — they're inseparable
/// from this dataset alone, so they live with the geometry constants and
/// aren't exposed in auto-calibration. Only β is fit per bike — α and
/// I_crank are structurally degenerate in spin-down data (only their
/// ratio appears in I·ω̇ = -τ), so per-bike α fitting just absorbs
/// I_crank deviations into a wrong α. Absolute scale is the
/// [powerScale] slider's job, against an external power meter.
/// See [Coastdown.fitBrake].
///
/// Anchoring chain — three independent inputs, zero perceived-effort
/// calibration:
///
///   1. [defaultICrank] from flywheel geometry. 18 kg total flywheel
///      (manufacturer spec): a 5 mm uniform Al disc plus two lead
///      weight-rings, one on each face. Disc radius R = 0.23 m
///      (46 cm OD); rings measured by ruler against the outer edge:
///        Disc:   π·R²·t·ρ_Al = π·(0.23)²·0.005·2700 = 2.24 kg
///        Ring A: r = 14–18 cm, h ≈ 2.03 cm, ρ_Pb = 11340 → 9.25 kg
///        Ring B: r = 13–17 cm, h ≈ 1.52 cm, ρ_Pb = 11340 → 6.50 kg
///      Both rings have ~2-3 mm chamfered edges extending past the
///      flat-top radii above (chamfer cuts the corner, not all the
///      way to zero thickness). The chamfer volume closes the 18 kg
///      budget at flat-top thicknesses comfortably within the
///      measured "less than" ruler bounds (h ≤ 2.0 cm, h ≤ 1.5 cm).
///      Symmetric chamfers shift I by <0.3% (outer chamfer has
///      slightly more circumference than inner) — below the flat-
///      ring formula's precision, so the constants below are kept
///      from the flat-ring model. Iron would need rings 46% over
///      the bounds, brass 35%, copper 28%, bismuth 18% — all ruled
///      out. Lead is the only material consistent with the measured
///      ring volumes and the 18 kg flywheel total.
///        I_disc    = ½·m·R²              = 0.0594 kg·m²
///        I_ring_A  = m·(r_in² + r_out²)/2 = 0.2405 kg·m²
///        I_ring_B  = m·(r_in² + r_out²)/2 = 0.1490 kg·m²
///        I_flywheel                       = 0.4488 kg·m²
///        I_crank   = g²·I_flywheel = 9.09 kg·m²   (g = 4.5)
///
///   2. [defaultAlpha] from the manufacturer's 1000 W max-output spec.
///      Under strict Wouterse, the asymptotic peak brake power at any
///      single ω is α/κ. With α = 165 N·m the fit lands κ = 0.1585 and
///      α/κ = 1041 W — matching the 1000 W rating to ~4%. The saturation
///      bell-curve isn't directly observed in our coastdowns (which sit
///      in the linear-damping regime ω << ω_c), but it's a real physical
///      constraint of permanent-magnet eddy brakes — finite magnetic
///      flux through the disc bounds the peak absorbable power. The
///      marketing spec is our anchor for where that ceiling sits.
///
///   3. Hill shape (R_h, p), κ, β, and τ_c from a global fit on 46 video-
///      tracked spindowns spanning R = 0 to 93. RSS = 0.0337 across
///      51,792 samples — a 21% RSS improvement over the viscous-only
///      residual drag the previous calibration used.
///
/// All three are mutually consistent — the data, the geometry, and the
/// marketing spec land on the same calibration without invoking
/// perceived effort anywhere.
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
  // α pinned to 165 (anchored to 1000 W marketing max via α/κ = 1041 W).
  // I_crank pinned to 9.09 (anchored to flywheel geometry: 18 kg with
  // a 5 mm uniform Al disc + lead weight-rings on both faces, side A
  // at r = 14–18 cm and side B at r = 13–17 cm). Residual drag is split
  // into Coulomb (τ_c, bearings + belt) and viscous (β, windage); R=0
  // spin-downs alone prefer this split by ~15× in RSS over viscous-only.
  static const double defaultAlpha = 165.0;     // N·m — peak torque amplitude
  static const double defaultBeta = 0.0216;     // 1/s — viscous residual drag
  static const double defaultTauC = 1.2134;     // N·m — Coulomb residual drag
  static const double defaultRh = 74.426;       // Hill midpoint
  static const double defaultP = 1.233;         // Hill sharpness
  static const double defaultKappa = 0.1585;    // s/rad — 1/ω_c at saturation
  static const double defaultICrank = 9.09;     // kg·m² (effective, at crank)
  static const double defaultPowerScale = 1.00; // coupled α + I_crank scale

  /// Bounds for the Power scale slider — coupled multiplier on α and
  /// I_crank applied at every output evaluation. 0.5–2.0 covers the range
  /// of unit-to-unit firmware-calibration spread we'd plausibly see across
  /// IC8s on top of the 1.00 default.
  static const double powerScaleMin = 0.5;
  static const double powerScaleMax = 2.0;

  // v8 splits residual drag into Coulomb + viscous (was viscous-only).
  // Re-fit on the same 46 video spindowns lands at τ_c = 1.21 N·m,
  // β = 0.0216 1/s — about half the old β absorbed into the new τ_c.
  // Hill shape and κ shift slightly with the new degree of freedom
  // (R_h 72.9→74.4, p 1.27→1.23, κ 0.160→0.1585). Global RSS drops
  // 21%. Loading v7 (β, I_crank) under v8 defaults would leave τ_c
  // at default while β reverts to its old viscous-only value, so the
  // model becomes over-damped — wipe and reset.
  static const String _keyAlpha = 'cal.alpha.v8';
  static const String _keyBeta = 'cal.betaW.v8';
  static const String _keyICrank = 'cal.iCrank.v8';
  static const String _keyPowerScale = 'cal.powerScale.v8';

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
  }

  bool get isAtDefaults =>
      alpha == defaultAlpha &&
      beta == defaultBeta &&
      iCrank == defaultICrank &&
      powerScale == defaultPowerScale;
}
