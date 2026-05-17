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
/// H(R) is a sum of two Hill curves, not one. A single 2-param Hill
/// systematically over-brakes by 0.3–0.85 rad/s across R = 22..44 in the
/// middle of the spin-down — the empirical B²(R) has a shoulder a single
/// sigmoid can't bend to. Two Hills give the H(R) curve enough flexibility
/// to absorb that mid-band structure, dropping the global RSS 38% over
/// the single-Hill fit (0.0337 → 0.0209). The decomposition is empirical,
/// not a clean physical mapping to the two magnet pairs (which both
/// engage over the same R range and already sum to a smooth ramp in the
/// geometric H_geom — analysis/physics_first_brake.py and fit_geom_hill.py).
/// Plausible physical sources of the residual shoulder are yoke flux
/// saturation, anti-polar pair coupling, or σ_Al frequency dependence,
/// but none of those derive a clean second-Hill knob from physics.
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
///   w     = mixing weight on the sharp Hill — H-shape, bike-firmware × geom
///   R_h1, p1 = sharp Hill midpoint and sharpness — H-shape
///   R_h2, p2 = broad Hill midpoint and sharpness — H-shape
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
///   2. [defaultAlpha] pinned to the manufacturer's 1000 W max-output
///      spec. Under strict Wouterse, the asymptotic peak brake power at
///      any single ω is α/κ. With α = 165 N·m the fit lands κ = 0.1585
///      and α/κ = 1041 W — matching the 1000 W rating to ~4%. The
///      saturation bell-curve isn't directly observed in our coastdowns
///      (which sit in the linear-damping regime ω << ω_c), but it's a
///      real physical constraint of permanent-magnet eddy brakes — finite
///      magnetic flux through the disc bounds the peak absorbable power.
///      The marketing spec is the anchor for where that ceiling sits.
///
///   3. H(R) shape ({w, R_h1, p1, R_h2, p2}), β, and τ_c from a global
///      fit on 46 video-tracked spindowns spanning R = 0 to 93, with α
///      and κ pinned to the 1041 W anchor and I_crank pinned to the
///      recalibrated value (see [defaultICrank] below). RSS = 0.0188
///      across 51,792 samples.
///
///   4. [defaultICrank] recalibrated from the 9.09 geometric value down
///      to 7.55 against the outdoor 4iiii crank meter. The bridge's
///      total steady-state output is P = I·λ_total(R)·ω² + τ_c·ω where
///      λ_total and τ_c come from the spin-down data, so I is the only
///      knob that lowers output without breaking the spin-down fit.
///      With the gear ratio measured exactly, the 17% gap falls
///      entirely on the flywheel inertia — most plausibly the rings
///      sit below their ruler-derived upper bounds, putting the
///      flywheel mass below the manufacturer's 18 kg spec. See the
///      comment block on [defaultICrank] below.
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
  // α = 165 and κ = 0.1585 pinned to anchor α/κ ≈ 1041 W against the
  // 1000 W marketing max-output spec; the saturation bell-curve isn't
  // directly observed in the linear-regime coastdowns so this anchor
  // sets where the ceiling sits.
  //
  // I_crank lowered from the 9.09 geometric value to 7.55 against the
  // outdoor 4iiii L-only crank meter (Lunch_Ride.fit May 2026,
  // Lunch_Ride_harder_effort.fit Sept 2025), dropping total bridge
  // output by ~17%. The bridge's steady-state output is
  // P = I·λ_total(R)·ω² + τ_c·ω where λ_total(R) and τ_c are measured
  // directly from spin-downs, so I is the only knob that lowers output
  // without invalidating the fit. Re-running fit_wouterse.py at the
  // new I lands H, β, τ_c at the values below with RSS = 0.0188
  // (slightly better than the I=9.09 fit's 0.0209).
  //
  // g is measured exactly, so the 17% drop in I lands entirely on
  // the flywheel inertia. Ring heights were ruler-measured upper
  // bounds and the 18 kg flywheel spec is manufacturer-stated, not
  // weighed — most plausibly the rings sit below those bounds and
  // the actual flywheel mass is below 18 kg.
  //
  // Residual drag is split into Coulomb (τ_c, bearings + belt) and
  // viscous (β, windage); R=0 spin-downs alone prefer this split by
  // ~15× in RSS over viscous-only. H(R) is a sum of two Hill curves;
  // the optimizer found a different basin from the v9 fit (R_h1 and
  // R_h2 swapped roles — broad Hill is now H1) but produces the same
  // H(R) function shape under the relabeling.
  static const double defaultAlpha = 165.0;     // N·m — peak torque amplitude (1000 W spec anchor)
  static const double defaultBeta = 0.0154;     // 1/s — viscous residual drag
  static const double defaultTauC = 1.1468;     // N·m — Coulomb residual drag
  static const double defaultW = 0.5994;        // mix weight on H1 (broad)
  static const double defaultRh1 = 185.036;     // broad Hill midpoint
  static const double defaultP1 = 0.669;        // broad Hill sharpness
  static const double defaultRh2 = 59.372;      // sharp Hill midpoint
  static const double defaultP2 = 2.246;        // sharp Hill sharpness
  static const double defaultKappa = 0.1585;    // s/rad — 1/ω_c at saturation (1000 W spec anchor)
  static const double defaultICrank = 7.55;     // kg·m² (recalibrated from 9.09 geometric)
  static const double defaultPowerScale = 1.00; // coupled α + I_crank scale

  /// Bounds for the Power scale slider — coupled multiplier on α and
  /// I_crank applied at every output evaluation. 0.5–2.0 covers the range
  /// of unit-to-unit firmware-calibration spread we'd plausibly see across
  /// IC8s on top of the 1.00 default.
  static const double powerScaleMin = 0.5;
  static const double powerScaleMax = 2.0;

  // v9 swaps the single 2-param Hill H(R) for a sum-of-two-Hills (5 H
  // shape params). κ pinned to the previous single-Hill optimum 0.1585
  // so α/κ = 1041 W stays put. Re-fit on the same 46 video spindowns
  // lands at β = 0.0157, τ_c = 1.358 — slight rebalancing between the
  // residual-drag terms relative to v8 (β = 0.0216, τ_c = 1.213) as
  // the two-Hill H(R) absorbs the mid-band over-braking that v8's β
  // was over-compensating for. Global RSS drops 38% (0.0337 → 0.0209).
  // Loading v8 β under v9 defaults would over-damp by ~30% at low
  // ω — wipe and reset.
  //
  // v10 keeps α and κ at the v9 spec-anchor values (165, 0.1585) but
  // lowers I_crank from 9.09 to 7.55 after external recalibration
  // against the outdoor 4iiii meter, dropping total bridge output by
  // ~17%. H, β, τ_c are re-fit at the new I (still matching the
  // spin-down data — RSS even improves slightly). Old saved I_crank
  // from v9 (9.09) would over-read by ~17%, and the v9 H, β, τ_c are
  // tied to that I, so all four keys are bumped to invalidate stored
  // v9 values. powerScale key stays at v9 since its default (1.00)
  // is unchanged.
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
