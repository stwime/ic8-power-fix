import 'dart:math' as math;

import 'calibration.dart';

/// One coastdown sample as forwarded from [IC8Central].
///
/// [timestampS] is the BLE-arrival time and [cadenceRpmCsc] is the 1 Hz
/// derived cadence — both used for segmentation only (detecting the
/// non-increasing run and locking on a stable R).
///
/// The actual decay fit uses [crankRevs] and [crankEventTimeS] — the
/// per-revolution CSC counters/timestamps reported by the bike. Each
/// strictly-increasing pair is one revolution event, timed to 1/1024 s. This
/// avoids the ~0.5 s jitter between [timestampS] and the rev event it
/// nominally represents, which dominates the high-R / short-coastdown fit.
class CoastdownSample {
  final double timestampS;
  final int resistance;
  final double cadenceRpmCsc;
  final int? crankRevs;
  final double? crankEventTimeS;
  const CoastdownSample({
    required this.timestampS,
    required this.resistance,
    required this.cadenceRpmCsc,
    this.crankRevs,
    this.crankEventTimeS,
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

/// Result of fitting Wouterse linear-regime
///     λ_eff(R) = β + (2ακ/I) · H(R)²   with H(R) = R^p / (R^p + R_h^p)
/// across multiple [CoastdownPoint]s. Everything except β is held fixed at
/// the [Calibration] defaults: the shape (p, R_h, κ) is firmware-mapping +
/// geometry, the inertia I is direct geometry, and α is anchored to the
/// manufacturer's 1000 W max-output spec via α/κ = 1020 W. Per-bike
/// spin-downs structurally can't separate α from I (only the ratio shows
/// up in I·ω̇ = -τ), so fitting α from coastdowns just absorbs I_crank
/// deviations into a wrong α. β is the one quantity that genuinely varies
/// unit-to-unit (drivetrain friction, belt tension, bearing wear) and isn't
/// degenerate with anything else — so it's the only thing we fit.
class BrakeFit {
  final double beta;
  /// Per-sample-count weighted RMS residual (1/s).
  final double rms;
  /// (R, λ_meas, λ_pred) per input point, in input order.
  final List<({int r, double measured, double predicted})> residuals;
  const BrakeFit({
    required this.beta,
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

/// Per-revolution log-linear fit of ω(t) = ω₀ exp(-λ t).
///
/// Walks the segment for strictly-increasing (crankRevs, crankEventTimeS)
/// pairs. For each consecutive pair the average angular frequency over the
/// inter-rev interval is
///     cad_i = 60 · (ΔN_i / Δt_i) rpm
/// which equals the instantaneous cadence at the interval midpoint to
/// O((λΔt)²/24) under exponential decay — negligible for the dial range
/// we operate in. Log-linear regression of ln(cad_i) vs the interval
/// midpoint recovers λ.
///
/// Falls back to the BLE-row form (timestampS, cadenceRpmCsc) when crank
/// rev data isn't carried on the samples (e.g. older callers, tests
/// without per-rev fields, or a CSC characteristic that didn't decode).
/// In production the BLE central always populates the per-rev fields so
/// the fallback is not exercised on the device.
({double lambda, double r2, int n, double cadHi, double cadLo,
    double durationS}) fitDecay(List<CoastdownSample> seg) {
  // Extract distinct rev observations.
  final revs = <int>[];
  final ets = <double>[];
  for (final s in seg) {
    final n = s.crankRevs;
    final t = s.crankEventTimeS;
    if (n == null || t == null) continue;
    if (revs.isNotEmpty && (n <= revs.last || t <= ets.last + 1e-6)) continue;
    revs.add(n);
    ets.add(t);
  }
  if (revs.length >= 4) {
    final pts = <({double t, double y, double cad})>[];
    for (int i = 1; i < revs.length; i++) {
      final dRev = revs[i] - revs[i - 1];
      final dt = ets[i] - ets[i - 1];
      final cad = 60.0 * dRev / dt;
      pts.add((t: 0.5 * (ets[i - 1] + ets[i]), y: math.log(cad), cad: cad));
    }
    final lr = _logLinearFit(pts);
    return (
      lambda: lr.lambda,
      r2: lr.r2,
      n: pts.length,
      cadHi: pts.first.cad,
      cadLo: pts.last.cad,
      durationS: ets.last - ets.first,
    );
  }
  // Fallback: BLE-row times and cadences.
  final pts = <({double t, double y, double cad})>[];
  for (final s in seg) {
    if (s.cadenceRpmCsc <= 0) continue;
    pts.add((t: s.timestampS, y: math.log(s.cadenceRpmCsc),
        cad: s.cadenceRpmCsc));
  }
  final lr = _logLinearFit(pts);
  return (
    lambda: lr.lambda,
    r2: lr.r2,
    n: pts.length,
    cadHi: pts.first.cad,
    cadLo: pts.last.cad,
    durationS: pts.last.t - pts.first.t,
  );
}

({double lambda, double r2}) _logLinearFit(
    List<({double t, double y, double cad})> pts) {
  final n = pts.length;
  double tMean = 0, yMean = 0;
  for (final p in pts) {
    tMean += p.t;
    yMean += p.y;
  }
  tMean /= n;
  yMean /= n;
  double sxx = 0, sxy = 0, syy = 0;
  for (final p in pts) {
    final dx = p.t - tMean;
    final dy = p.y - yMean;
    sxx += dx * dx;
    sxy += dx * dy;
    syy += dy * dy;
  }
  final slope = sxy / sxx;
  final intercept = yMean - slope * tMean;
  double ssRes = 0;
  for (final p in pts) {
    final e = p.y - (slope * p.t + intercept);
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

/// Apply [findCleanCoastdowns] then [fitDecay] to each segment. The leading
/// [leadingTrim] samples of each run are discarded before fitting (capped to
/// keep ≥ minSamples=4 in the fit) — they are dominated by the
/// rider-feet-still-touching transient.
///
/// Note: this no longer gates on r². The earlier r²≥0.95 cutoff was circular —
/// r² of a log-linear fit measures how exponential the decay is, which is
/// exactly the modelling assumption being tested. Filtering on it censors
/// the data that would falsify the model. r² is still surfaced on each
/// emitted [CoastdownPoint] for the UI to label fit quality.
List<CoastdownPoint> extractCoastdownPoints(
  List<CoastdownSample> rows, {
  int leadingTrim = 1,
}) {
  final out = <CoastdownPoint>[];
  for (final seg in findCleanCoastdowns(rows)) {
    final samples = _trimLeading(seg, leadingTrim: leadingTrim, minSamples: 4);
    final fit = fitDecay(samples);
    out.add(CoastdownPoint(
      resistance: samples.first.resistance,
      lambda: fit.lambda,
      r2: fit.r2,
      n: fit.n,
      cadHi: fit.cadHi,
      cadLo: fit.cadLo,
      durationS: fit.durationS,
    ));
  }
  return out;
}

/// Streaming version of [findCleanCoastdowns] for the live UI. Push samples
/// as they arrive; when a clean run terminates and is at least [minSamples]
/// long, [onPoint] fires with the fitted [CoastdownPoint]. Fit quality (r²,
/// duration, cadence range) is surfaced on the point for the UI to label.
class CoastdownDetector {
  final void Function(CoastdownPoint) onPoint;
  final double minCadStart;
  final int minSamples;
  final int rJitterMax;
  final double flatTol;
  final int leadingTrim;

  final List<CoastdownSample> _run = [];

  CoastdownDetector(
    this.onPoint, {
    this.minCadStart = 70,
    this.minSamples = 4,
    this.rJitterMax = 1,
    this.flatTol = 0.05,
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
      onPoint(CoastdownPoint(
        resistance: samples.first.resistance,
        lambda: fit.lambda,
        r2: fit.r2,
        n: fit.n,
        cadHi: fit.cadHi,
        cadLo: fit.cadLo,
        durationS: fit.durationS,
      ));
    }
    _run.clear();
  }
}

/// Weighted least-squares fit of β in the Wouterse linear-regime relation
///     λ_eff(R) = β + (2·α·κ / I) · H(R)²
/// with everything except β held fixed at the [Calibration] defaults.
///
/// User coastdowns at typical cadences sit well below the brake's
/// critical ω, so the bell-curve term collapses to linear damping with
/// effective decay rate λ_eff(R). With α pinned by the 1000 W max-output
/// spec and I_crank pinned by flywheel geometry — both shared across all
/// IC8/IC4/C6/C7 units — only β has a physical reason to vary unit-to-unit
/// and isn't degenerate with anything else. The fitter subtracts the
/// model's H(R)² contribution and recovers β as a single weighted mean
/// of the residuals.
///
/// Weights combine √n (sample count) with log(cadHi/cadLo) (dynamic
/// range observed) — a 125→10 segment carries more information about
/// the decay rate than a 77→44 one even at the same n.
///
/// Requires ≥2 points across ≥2 distinct R values (kept as a sanity bar
/// against single-R datasets even though β alone is identifiable from
/// one point in principle).
BrakeFit fitBrake(
  List<CoastdownPoint> points, {
  double alpha = Calibration.defaultAlpha,
  double kappa = Calibration.defaultKappa,
  double iCrank = Calibration.defaultICrank,
}) {
  if (points.length < 2) {
    throw ArgumentError('need ≥2 points, got ${points.length}');
  }
  final distinctR = points.map((p) => p.resistance).toSet();
  if (distinctR.length < 2) {
    throw ArgumentError('need ≥2 distinct R values, got ${distinctR.length}');
  }

  final scale = 2.0 * kappa / iCrank;

  double designAt(int r) {
    if (r <= 0) return 0.0;
    final h = Calibration.hillAt(r.toDouble());
    return scale * h * h;
  }

  double weightOf(CoastdownPoint pt) {
    final wRoot = math.sqrt(pt.n.toDouble()) * math.log(pt.cadHi / pt.cadLo);
    return wRoot * wRoot;
  }

  // β-only WLS at fixed (α, p, R_h, κ, I): subtract the modelled H(R)²
  // contribution and take the weighted mean of the residuals.
  double sw = 0, sres = 0;
  for (final pt in points) {
    final u = designAt(pt.resistance);
    final w = weightOf(pt);
    sw += w;
    sres += w * (pt.lambda - alpha * u);
  }
  if (sw < 1e-12) {
    throw StateError('zero-weight fit (degenerate point set)');
  }
  final beta = sres / sw;

  double sse = 0;
  final residuals = <({int r, double measured, double predicted})>[];
  for (final pt in points) {
    final pred = alpha * designAt(pt.resistance) + beta;
    residuals.add((r: pt.resistance, measured: pt.lambda, predicted: pred));
    final e = pt.lambda - pred;
    sse += weightOf(pt) * e * e;
  }
  return BrakeFit(
    beta: beta,
    rms: math.sqrt(sse / sw),
    residuals: residuals,
  );
}
