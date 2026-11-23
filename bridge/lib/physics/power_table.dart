import 'dart:convert';

/// A measured steady-state power map P(R, cadence) from a reference power
/// meter on this bike. When one is loaded, [Corrector] uses it instead of
/// the spin-down physics for the steady term. The table is correct by
/// construction at the measured points and interpolates between them.
///
/// Produced by `analysis/fit_reference.py` from a power-meter session
/// (see docs/reference-session.md). JSON format, version 1:
///
///     {
///       "version": 1,
///       "source": "Assioma Uno, 2026-11-14",
///       "r": [10, 20, ..., 90],
///       "cadence_rpm": [60, 80, 100],
///       "watts": [[...per cadence...], ...per R...],
///       "i_crank": 6.12
///     }
///
/// `watts[i][j]` is the power at `r[i]` and `cadence_rpm[j]`. Both grids
/// must be strictly increasing. `i_crank` is the inertia used for the
/// acceleration term, in kg·m² at the crank: the model's inertia times
/// the session's mean power-meter/model ratio, since spin-downs fix only
/// torque over inertia. It is optional and defaults to the physics
/// model's value.
class PowerTable {
  static const int formatVersion = 1;

  final List<double> r;
  final List<double> cadenceRpm;
  final List<List<double>> watts;
  final double iCrank;
  final String source;

  PowerTable({
    required this.r,
    required this.cadenceRpm,
    required this.watts,
    required this.iCrank,
    this.source = '',
  }) {
    _validate();
  }

  void _validate() {
    if (r.length < 2 || cadenceRpm.length < 2) {
      throw const FormatException('Need at least two R and two cadence points');
    }
    for (int i = 1; i < r.length; i++) {
      if (r[i] <= r[i - 1]) throw const FormatException('R grid must increase');
    }
    for (int j = 1; j < cadenceRpm.length; j++) {
      if (cadenceRpm[j] <= cadenceRpm[j - 1]) {
        throw const FormatException('Cadence grid must increase');
      }
    }
    if (watts.length != r.length) {
      throw const FormatException('watts must have one row per R');
    }
    for (final row in watts) {
      if (row.length != cadenceRpm.length) {
        throw const FormatException('watts rows must have one entry per cadence');
      }
      for (final w in row) {
        if (!w.isFinite || w < 0) {
          throw const FormatException('watts must be finite and non-negative');
        }
      }
    }
    if (!iCrank.isFinite || iCrank <= 0) {
      throw const FormatException('i_crank must be positive');
    }
  }

  int get cellCount => r.length * cadenceRpm.length;

  /// Steady-state power in W. Bilinear inside the grid. Outside it, R is
  /// clamped to the nearest edge (the dial is bounded and the shape beyond
  /// the last measured R is unknown), while cadence extrapolates linearly
  /// from the two outermost grid points and is floored at zero, so a sprint
  /// above the top measured cadence still rises instead of flat-lining.
  double powerAt(double rIn, double cadIn) {
    final rr = rIn.clamp(r.first, r.last).toDouble();
    final (i0, i1, fr) = _bracket(r, rr);
    final (j0, j1, fc) = _bracketExtrapolating(cadenceRpm, cadIn);
    final p0 = watts[i0][j0] + (watts[i0][j1] - watts[i0][j0]) * fc;
    final p1 = watts[i1][j0] + (watts[i1][j1] - watts[i1][j0]) * fc;
    final p = p0 + (p1 - p0) * fr;
    return p < 0 ? 0 : p;
  }

  /// Index pair and fraction for `x` inside `grid` (x already clamped).
  static (int, int, double) _bracket(List<double> grid, double x) {
    if (x <= grid.first) return (0, 0, 0);
    if (x >= grid.last) return (grid.length - 1, grid.length - 1, 0);
    int i = 0;
    while (grid[i + 1] < x) {
      i++;
    }
    final f = (x - grid[i]) / (grid[i + 1] - grid[i]);
    return (i, i + 1, f);
  }

  /// Like [_bracket] but lets the fraction leave [0, 1] at both ends so the
  /// caller extrapolates along the outermost segment.
  static (int, int, double) _bracketExtrapolating(List<double> grid, double x) {
    int i;
    if (x <= grid.first) {
      i = 0;
    } else if (x >= grid.last) {
      i = grid.length - 2;
    } else {
      i = 0;
      while (grid[i + 1] < x) {
        i++;
      }
    }
    final f = (x - grid[i]) / (grid[i + 1] - grid[i]);
    return (i, i + 1, f);
  }

  Map<String, dynamic> toJson() => {
        'version': formatVersion,
        'source': source,
        'r': r,
        'cadence_rpm': cadenceRpm,
        'watts': watts,
        'i_crank': iCrank,
      };

  String encode() => jsonEncode(toJson());

  /// Parses the JSON produced by `analysis/fit_reference.py`. Throws
  /// [FormatException] with a readable message on any problem.
  static PowerTable decode(String text, {required double defaultICrank}) {
    final dynamic raw;
    try {
      raw = jsonDecode(text);
    } on FormatException {
      throw const FormatException('Not valid JSON');
    }
    if (raw is! Map) throw const FormatException('Expected a JSON object');
    final version = raw['version'];
    if (version != formatVersion) {
      throw FormatException('Unsupported table version $version');
    }
    List<double> nums(dynamic v, String name) {
      if (v is! List) throw FormatException('"$name" must be a list');
      return v.map((e) {
        if (e is num) return e.toDouble();
        throw FormatException('"$name" must contain numbers');
      }).toList();
    }
    final wattsRaw = raw['watts'];
    if (wattsRaw is! List) throw const FormatException('"watts" must be a list');
    final watts = wattsRaw.map((row) => nums(row, 'watts')).toList();
    final i = raw['i_crank'];
    return PowerTable(
      r: nums(raw['r'], 'r'),
      cadenceRpm: nums(raw['cadence_rpm'], 'cadence_rpm'),
      watts: watts,
      iCrank: i is num ? i.toDouble() : defaultICrank,
      source: raw['source'] is String ? raw['source'] as String : '',
    );
  }
}
