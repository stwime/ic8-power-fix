import 'dart:math' as math;

/// One coastdown sample, CSC-derived.
class CoastdownSample {
  final double timestampS;
  final int resistance;
  final double cadenceRpmCsc;
  const CoastdownSample({
    required this.timestampS,
    required this.resistance,
    required this.cadenceRpmCsc,
  });
}

/// A clean coastdown segment with its log-linear λ fit.
class CoastdownPoint {
  final int resistance;
  final double lambda;     // 1/s
  final double r2;
  final int n;
  final double cadHi;
  final double cadLo;
  final double durationS;
  const CoastdownPoint({
    required this.resistance,
    required this.lambda,
    required this.r2,
    required this.n,
    required this.cadHi,
    required this.cadLo,
    required this.durationS,
  });
}

/// Result of fitting λ(R) = a·R + b across multiple [CoastdownPoint]s.
class BrakeFit {
  final double aBrake;
  final double bFriction;
  /// Per-sample-count weighted RMS residual (1/s).
  final double rms;
  /// (R, λ_meas, λ_pred) per input point, in input order.
  final List<({int r, double measured, double predicted})> residuals;
  const BrakeFit({
    required this.aBrake,
    required this.bFriction,
    required this.rms,
    required this.residuals,
  });
}

/// Port of analysis/spindown_fit.py:find_clean_coastdowns.
///
/// A run begins when CSC cadence is ≥ [minCadStart]. The run extends as long as
/// CSC cadence is non-increasing (small [flatTol] for tied notifications) and
/// resistance stays within ±[rJitterMax] of the run's starting R. Runs shorter
/// than [minSamples] are discarded.
List<List<CoastdownSample>> findCleanCoastdowns(
  List<CoastdownSample> rows, {
  double minCadStart = 70,
  int minSamples = 4,
  int rJitterMax = 1,
  double flatTol = 0.05,
}) {
  final segs = <List<CoastdownSample>>[];
  int i = 0;
  while (i < rows.length - minSamples) {
    final c0 = rows[i].cadenceRpmCsc;
    if (c0 < minCadStart) {
      i++;
      continue;
    }
    final r0 = rows[i].resistance;
    int j = i;
    while (j + 1 < rows.length) {
      final cNext = rows[j + 1].cadenceRpmCsc;
      final rNext = rows[j + 1].resistance;
      final cCurr = rows[j].cadenceRpmCsc;
      // cNext > 0 guard: a missing/zero CSC value (no crank events in the
      // notification window) shouldn't be folded into the run — log(0) blows
      // up the fit, and in practice it means the rider stopped fully.
      if (cNext > 0 &&
          cNext < cCurr + flatTol &&
          (rNext - r0).abs() <= rJitterMax) {
        j++;
      } else {
        break;
      }
    }
    if (j - i + 1 >= minSamples) {
      segs.add(rows.sublist(i, j + 1));
      i = j + 1;
    } else {
      i++;
    }
  }
  return segs;
}

/// Log-linear fit ω(t) = ω₀ exp(-λ t) over a coastdown segment.
/// Returns λ in 1/s and r² of ln(c) vs t.
({double lambda, double r2}) fitDecay(List<CoastdownSample> seg) {
  final n = seg.length;
  final t = List<double>.generate(n, (i) => seg[i].timestampS);
  final y = List<double>.generate(n, (i) => math.log(seg[i].cadenceRpmCsc));
  final tMean = t.reduce((a, b) => a + b) / n;
  final yMean = y.reduce((a, b) => a + b) / n;
  double sxx = 0, sxy = 0, syy = 0;
  for (int i = 0; i < n; i++) {
    final dx = t[i] - tMean;
    final dy = y[i] - yMean;
    sxx += dx * dx;
    sxy += dx * dy;
    syy += dy * dy;
  }
  final slope = sxy / sxx;
  final intercept = yMean - slope * tMean;
  double ssRes = 0;
  for (int i = 0; i < n; i++) {
    final pred = slope * t[i] + intercept;
    final e = y[i] - pred;
    ssRes += e * e;
  }
  final r2 = 1 - ssRes / math.max(syy, 1e-12);
  return (lambda: -slope, r2: r2);
}

/// Drop the first [leadingTrim] samples to suppress the transient where the
/// rider may still be touching the pedals (legs add drag), capped so the
/// remaining segment retains at least [minSamples] samples.
List<CoastdownSample> _trimLeading(
  List<CoastdownSample> seg, {
  required int leadingTrim,
  required int minSamples,
}) {
  final canDrop = math.max(0, math.min(leadingTrim, seg.length - minSamples));
  return canDrop == 0 ? seg : seg.sublist(canDrop);
}

/// Apply [findCleanCoastdowns] then [fitDecay] to each segment, dropping any
/// fit with r² below [minR2]. The leading [leadingTrim] samples of each run
/// are discarded before fitting (capped to keep ≥ minSamples=4 in the fit) —
/// they are dominated by the rider-feet-still-touching transient.
List<CoastdownPoint> extractCoastdownPoints(
  List<CoastdownSample> rows, {
  double minR2 = 0.95,
  int leadingTrim = 1,
}) {
  final out = <CoastdownPoint>[];
  for (final seg in findCleanCoastdowns(rows)) {
    final samples = _trimLeading(seg, leadingTrim: leadingTrim, minSamples: 4);
    final fit = fitDecay(samples);
    if (fit.r2 < minR2) continue;
    out.add(CoastdownPoint(
      resistance: samples.first.resistance,
      lambda: fit.lambda,
      r2: fit.r2,
      n: samples.length,
      cadHi: samples.first.cadenceRpmCsc,
      cadLo: samples.last.cadenceRpmCsc,
      durationS: samples.last.timestampS - samples.first.timestampS,
    ));
  }
  return out;
}

/// Streaming version of [findCleanCoastdowns] for the live UI. Push samples
/// as they arrive; when a clean run terminates and meets r² and length
/// thresholds, [onPoint] fires with the fitted [CoastdownPoint].
class CoastdownDetector {
  final void Function(CoastdownPoint) onPoint;
  final double minCadStart;
  final int minSamples;
  final int rJitterMax;
  final double flatTol;
  final double minR2;
  final int leadingTrim;

  final List<CoastdownSample> _run = [];

  CoastdownDetector(
    this.onPoint, {
    this.minCadStart = 70,
    this.minSamples = 4,
    this.rJitterMax = 1,
    this.flatTol = 0.05,
    this.minR2 = 0.95,
    this.leadingTrim = 1,
  });

  /// Number of samples in the run currently being recorded (0 if idle).
  int get currentRunLength => _run.length;

  /// Resistance value the current run is locked to (null if idle).
  int? get currentRunR => _run.isEmpty ? null : _run.first.resistance;

  /// Most recent cadence in the current run (null if idle).
  double? get currentRunCadence =>
      _run.isEmpty ? null : _run.last.cadenceRpmCsc;

  void push(CoastdownSample s) {
    if (s.cadenceRpmCsc <= 0) {
      _finalize();
      return;
    }
    if (_run.isEmpty) {
      if (s.cadenceRpmCsc >= minCadStart) _run.add(s);
      return;
    }
    final r0 = _run.first.resistance;
    final cCurr = _run.last.cadenceRpmCsc;
    final cNext = s.cadenceRpmCsc;
    if (cNext < cCurr + flatTol && (s.resistance - r0).abs() <= rJitterMax) {
      _run.add(s);
    } else {
      _finalize();
      // Re-attempt: this sample might be the start of the next run.
      if (s.cadenceRpmCsc >= minCadStart) _run.add(s);
    }
  }

  /// Force-finalize any in-progress run (e.g. when the user navigates away).
  void flush() => _finalize();

  /// Discard the in-progress run without emitting (e.g. user tapped Discard).
  void discard() => _run.clear();

  void _finalize() {
    if (_run.length >= minSamples) {
      final samples = _trimLeading(
          _run, leadingTrim: leadingTrim, minSamples: minSamples);
      final fit = fitDecay(samples);
      if (fit.r2 >= minR2) {
        onPoint(CoastdownPoint(
          resistance: samples.first.resistance,
          lambda: fit.lambda,
          r2: fit.r2,
          n: samples.length,
          cadHi: samples.first.cadenceRpmCsc,
          cadLo: samples.last.cadenceRpmCsc,
          durationS: samples.last.timestampS - samples.first.timestampS,
        ));
      }
    }
    _run.clear();
  }
}

/// Weighted least-squares fit λ(R) = a·R + b. Weights are √n per point, matching
/// analysis/spindown_fit.py:main. Requires ≥2 points across ≥2 distinct R values.
BrakeFit fitBrake(List<CoastdownPoint> points) {
  if (points.length < 2) {
    throw ArgumentError('need ≥2 points, got ${points.length}');
  }
  final distinctR = points.map((p) => p.resistance).toSet();
  if (distinctR.length < 2) {
    throw ArgumentError('need ≥2 distinct R values, got ${distinctR.length}');
  }
  // Solve weighted (W·A) [a; b] = W·λ via 2x2 normal equations.
  // A = [[R_i, 1]], W = diag(√n_i). Then AᵀW²A · x = AᵀW²λ.
  double sw = 0, sR = 0, sR2 = 0, slam = 0, sRlam = 0;
  for (final p in points) {
    final w = p.n.toDouble(); // weight² for normal equations
    sw += w;
    sR += w * p.resistance;
    sR2 += w * p.resistance * p.resistance;
    slam += w * p.lambda;
    sRlam += w * p.resistance * p.lambda;
  }
  // [[sR2, sR], [sR, sw]] [a; b] = [sRlam; slam]
  final det = sR2 * sw - sR * sR;
  if (det.abs() < 1e-12) {
    throw StateError('singular fit (degenerate R distribution)');
  }
  final a = (sw * sRlam - sR * slam) / det;
  final b = (sR2 * slam - sR * sRlam) / det;

  double sse = 0;
  double wsum = 0;
  final residuals = <({int r, double measured, double predicted})>[];
  for (final p in points) {
    final pred = a * p.resistance + b;
    residuals.add((r: p.resistance, measured: p.lambda, predicted: pred));
    final e = p.lambda - pred;
    sse += p.n * e * e;
    wsum += p.n;
  }
  final rms = math.sqrt(sse / wsum);
  return BrakeFit(aBrake: a, bFriction: b, rms: rms, residuals: residuals);
}
