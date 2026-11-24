import 'dart:math' as math;

import 'package:shared_preferences/shared_preferences.dart';

import 'power_table.dart';

/// Tunable physics constants — split from [Constants] so they can be edited at
/// runtime via the settings screen and persisted across app launches.
///
/// The IC8's brake is two pairs of permanent magnets straddling the
/// aluminium flywheel at a fixed gap; the dial slides them onto the disc.
/// Below its critical speed an eddy brake is linear damping, and the
/// spin-downs show no fade at any speed they reach (~150 rpm at R ≤ 44,
/// 90–100 rpm at R = 66–85), so torque at the crank is
///
///     τ_brake(R, ω) = c · H(R)² · ω + τ_c + I · β · ω
///       with  H(R) = w · R^p1/(R^p1 + R_h1^p1)
///                  + (1−w) · R^p2/(R^p2 + R_h2^p2)
///
/// fit on per-revolution ω(t) trajectories from 46 hand-curated,
/// video-tracked spin-downs spanning R = 0 to 93
/// (analysis/fit_spindowns.py). A fade at a fixed critical speed makes
/// the fit steadily worse as that speed comes down from infinity; at
/// 300 rpm RSS is 4% worse, at 160 rpm 40%.
///
/// H(R) is a sum of two Hill curves, an empirical shape. From R = 40 up
/// it matches, within ~15%, the shape the magnet geometry gives with the
/// eddy currents solved (analysis/physics_first_brake.py).
///
/// Residual drag has two components, both R-independent:
///   τ_c       = Coulomb (bearings + belt + seal friction) — constant in ω
///   I · β · ω = viscous (windage + air-film) — linear in ω
/// Isolating the R=0 spin-downs (no eddy contribution) and fitting drag-
/// shape alone, Coulomb + viscous beats viscous-only by ~15× in RSS
/// (analysis/residual_drag_shape.py).
///
///   c     = brake damping at H = 1 (N·m·s/rad at the crank) — set by
///           I_crank through the spin-down fit, shared across
///           IC8/IC4/C6/C7 (same magnets, same actuator, same firmware).
///           Settings calls it "Brake".
///   β     = viscous residual drag (1/s) — per-bike (windage + air-film)
///   τ_c   = Coulomb residual drag (N·m) — per-bike default (bearings + belt)
///   w, R_h1, p1, R_h2, p2 = H-shape
///
/// The H-shape constants absorb both the magnet-overlap physics and
/// whatever mapping the IC8 applies between dial reading and carrier
/// position, so they aren't exposed in auto-calibration. Only β is fit
/// per bike: c and I_crank are degenerate in spin-down data (only their
/// ratio appears in I·ω̇ = −τ), so fitting c per bike just absorbs I_crank
/// deviations into a wrong c. Absolute scale comes from I_crank, and the
/// [powerScale] slider corrects it against an external power meter or the
/// rider's FTP measured on one. See [Coastdown.fitBrake].
///
/// Anchoring chain:
///
///   1. [defaultICrank] = 6.66 kg·m², the flywheel's geometry: a 5 mm
///      aluminium disc (R = 23 cm) with a steel weight ring on each face
///      (r = 14–18 cm × 2.03 cm and 13–17 cm × 1.52 cm; a magnet sticks
///      to them, so they are not lead), 13.1 kg in all, times g² for the
///      belt ratio g = 4.5. Ring heights and g are ruler-measured, and
///      cast iron instead of steel would give 6.21, so this holds to
///      roughly 10%. The rider's FTP cross-checks it
///      (analysis/ftp_anchor.py).
///
///   2. Spin-downs fix torque over inertia, so c and τ_c follow from I.
///
///   3. H(R) shape, β, τ_c and c from a global fit on the 46 spin-downs at
///      the pinned I. RSS = 0.01185 across 51,792 samples.
///
/// [powerScale] is a coupled absolute-scale knob: it multiplies c, τ_c
/// and I_crank by the same factor, so the brake term, the residual drag
/// and the KE term all scale together and the total output is linear in
/// [powerScale]. Cadence-shape and R-shape are untouched, and so is the
/// modelled coastdown rate (c/I + β), a property of the bike.
///
/// The default 1.00 is the geometry above. Tune against an external
/// power meter or your FTP; nothing in the model claims absolute scale
/// to better than ~10% without one.
class Calibration {
  // From analysis/fit_spindowns.py at I_crank = 6.66 (flywheel geometry,
  // steel rings). analysis/bridge_model.py mirrors these values.
  static const double defaultBrake = 43.7682;   // N·m·s/rad — brake damping c at H = 1
  static const double defaultBeta = 0.013854;   // 1/s — viscous residual drag
  static const double defaultTauC = 1.0980;     // N·m — Coulomb residual drag
  static const double defaultW = 0.44985;       // mix weight on H1 (broad)
  static const double defaultRh1 = 129.292;     // broad Hill midpoint
  static const double defaultP1 = 0.64984;      // broad Hill sharpness
  static const double defaultRh2 = 78.890;      // sharp Hill midpoint
  static const double defaultP2 = 1.87239;      // sharp Hill sharpness
  static const double defaultICrank = 6.66;     // kg·m² — flywheel geometry, steel rings, g = 4.5
  static const double defaultPowerScale = 1.00; // coupled c + τ_c + I_crank scale

  /// Bounds for the Power scale slider — coupled multiplier on c, τ_c and
  /// I_crank applied at every output evaluation. 0.5–2.0 covers the range
  /// of unit-to-unit firmware-calibration spread we'd plausibly see across
  /// IC8s on top of the 1.00 default.
  static const double powerScaleMin = 0.5;
  static const double powerScaleMax = 2.0;

  // Storage keys are versioned so that bumping a default invalidates
  // any value persisted under a prior version. The brake moved to a new
  // key when the model went from a fading (Wouterse) brake with amplitude
  // α to linear damping c; [load] carries a tuned α over as the same
  // ratio to its default. β moved with the new H-shape: a β fit against
  // the old shape doesn't belong to this one, so it falls back to the
  // default. Power scale moved when I_crank went from 7.55 to 6.66;
  // [load] converts a value tuned under the old key so the output it gave
  // is unchanged.
  static const String _keyBrake = 'cal.brake.v12';
  static const String _keyBeta = 'cal.betaW.v12';
  static const String _keyPowerScale = 'cal.powerScale.v10';
  static const String _keyPowerTable = 'cal.powerTable.v1';

  /// Old α keys and the default each was relative to.
  static const Map<String, double> _alphaKeys = {
    'cal.alpha.v11': 145.55,
    'cal.alpha.v10': 165.0,
  };
  static const String _keyPowerScaleV9 = 'cal.powerScale.v9';
  static const double _iCrankV10 = 7.55;

  double brake;
  double beta;
  double powerScale;

  /// Measured P(R, cadence) from a reference power meter on this bike.
  /// When set, it replaces the physics model's steady term and
  /// [powerScale] is not applied: the table is already in real watts.
  /// See [PowerTable] and docs/reference-session.md.
  PowerTable? powerTable;

  Calibration._({
    required this.brake,
    required this.beta,
    required this.powerScale,
    this.powerTable,
  });

  /// In-memory only, no persistence. For tests.
  Calibration.defaults()
      : brake = defaultBrake,
        beta = defaultBeta,
        powerScale = defaultPowerScale,
        powerTable = null;

  static Future<Calibration> load() async {
    final prefs = await SharedPreferences.getInstance();
    PowerTable? table;
    final tableJson = prefs.getString(_keyPowerTable);
    if (tableJson != null) {
      try {
        table = PowerTable.decode(tableJson, defaultICrank: defaultICrank);
      } on FormatException {
        // A table that no longer parses is worse than none: fall back to
        // the physics model rather than failing at launch.
        await prefs.remove(_keyPowerTable);
      }
    }
    final brake = await _readBrake(prefs);
    // A Power scale saved under the old key was relative to I_crank =
    // 7.55; it scales inversely with I, rounded to the slider's 1% step.
    // A saved default stays a default.
    const k = defaultICrank / _iCrankV10;
    final powerScale = await _readOrConvert(
        prefs, _keyPowerScale, _keyPowerScaleV9,
        (p) => p == defaultPowerScale
            ? null
            : ((p / k * 100).round() / 100)
                .clamp(powerScaleMin, powerScaleMax)
                .toDouble());
    await prefs.remove('cal.betaW.v10');
    return Calibration._(
      brake: brake ?? defaultBrake,
      beta: prefs.getDouble(_keyBeta) ?? defaultBeta,
      powerScale: powerScale ?? defaultPowerScale,
      powerTable: table,
    );
  }

  /// The saved brake; failing that, a tuned α under an old key as the same
  /// ratio to its default, saved under the new key once. A saved default
  /// stays a default. Old α keys are dropped either way.
  static Future<double?> _readBrake(SharedPreferences prefs) async {
    double? brake = prefs.getDouble(_keyBrake);
    for (final MapEntry(key: key, value: alphaDefault) in _alphaKeys.entries) {
      final old = prefs.getDouble(key);
      if (old == null) continue;
      if (brake == null && old != alphaDefault) {
        brake = defaultBrake * old / alphaDefault;
        await prefs.setDouble(_keyBrake, brake);
      }
      await prefs.remove(key);
    }
    return brake;
  }

  /// The value saved under [key]; failing that, [convert] applied to the
  /// value under [oldKey], saved under [key], and the old entry dropped,
  /// so the conversion runs once. [convert] returns null to mean "use
  /// the default".
  static Future<double?> _readOrConvert(SharedPreferences prefs, String key,
      String oldKey, double? Function(double) convert) async {
    final current = prefs.getDouble(key);
    if (current != null) return current;
    final old = prefs.getDouble(oldKey);
    if (old == null) return null;
    final converted = convert(old);
    if (converted != null) await prefs.setDouble(key, converted);
    await prefs.remove(oldKey);
    return converted;
  }

  /// True when a measured table drives the steady-state output.
  bool get usesTable => powerTable != null;

  /// Inertia for the acceleration term. The table carries its own value
  /// (unscaled, measured watts); the physics path uses [effectiveICrank].
  double get keICrank => powerTable?.iCrank ?? effectiveICrank;

  /// Steady-state power in W from whichever source is active.
  double steadyPowerAt(double r, double cadenceRpm) {
    final table = powerTable;
    if (table != null) return table.powerAt(r, cadenceRpm);
    return brakePowerAt(r, cadenceRpm * math.pi / 30.0);
  }

  Future<void> setPowerTable(PowerTable? table) async {
    powerTable = table;
    final prefs = await SharedPreferences.getInstance();
    if (table == null) {
      await prefs.remove(_keyPowerTable);
    } else {
      await prefs.setString(_keyPowerTable, table.encode());
    }
  }

  /// H(R) = w·R^p1/(R^p1 + R_h1^p1) + (1−w)·R^p2/(R^p2 + R_h2^p2).
  /// The brake's damping at dial position R is c·H(R)². Continuous,
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
  double get effectiveICrank => defaultICrank * powerScale;

  /// Brake torque + Coulomb + viscous residual drag at the crank, in N·m.
  /// The user-facing [powerScale] multiplies c (brake), τ_c (Coulomb) and
  /// I_crank (viscous) by the same factor, so τ_brake scales linearly with
  /// [powerScale] without distorting cadence or R shape.
  double tauBrakeAt(double r, double omega) {
    final h = _hill(r);
    final tauEddy = (brake * powerScale) * h * h * omega;
    final tauResidual = powerScale * (defaultTauC + defaultICrank * beta * omega);
    return tauEddy + tauResidual;
  }

  /// Steady-state brake power = τ_brake(R, ω)·ω, in W.
  double brakePowerAt(double r, double omega) {
    return tauBrakeAt(r, omega) * omega;
  }

  Future<void> setPowerScale(double v) async {
    powerScale = v.clamp(powerScaleMin, powerScaleMax);
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_keyPowerScale, powerScale);
  }

  Future<void> setBrake(double v) async {
    brake = v;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_keyBrake, brake);
  }

  Future<void> setBeta(double v) async {
    beta = v;
    final prefs = await SharedPreferences.getInstance();
    await prefs.setDouble(_keyBeta, beta);
  }

  Future<void> resetToDefaults() async {
    brake = defaultBrake;
    beta = defaultBeta;
    powerScale = defaultPowerScale;
    powerTable = null;
    final prefs = await SharedPreferences.getInstance();
    await prefs.remove(_keyBrake);
    await prefs.remove(_keyBeta);
    await prefs.remove(_keyPowerScale);
    await prefs.remove(_keyPowerTable);
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
    await prefs.remove('cal.powerScale.v9');
    await prefs.remove('cal.betaW.v10');
    for (final key in _alphaKeys.keys) {
      await prefs.remove(key);
    }
  }

  bool get isAtDefaults =>
      brake == defaultBrake &&
      beta == defaultBeta &&
      powerScale == defaultPowerScale &&
      powerTable == null;
}
