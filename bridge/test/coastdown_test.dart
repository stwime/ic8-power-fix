import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';
import 'package:ic8_bridge/physics/coastdown.dart';

CoastdownSample _s(double t, int r, double cad) =>
    CoastdownSample(timestampS: t, resistance: r, cadenceRpmCsc: cad);

/// Synthesize a coastdown at known λ: ω(t) = ω₀ exp(-λ t).
List<CoastdownSample> _synth({
  required int r,
  required double lambda,
  required double cad0,
  required int nSamples,
  double dt = 1.0,
  double t0 = 0.0,
}) {
  return [
    for (int i = 0; i < nSamples; i++)
      _s(t0 + i * dt, r, cad0 * math.exp(-lambda * i * dt)),
  ];
}

void main() {
  group('findCleanCoastdowns', () {
    test('detects a single decreasing run that meets minSamples', () {
      final rows = _synth(r: 20, lambda: 0.05, cad0: 100, nSamples: 10);
      final segs = findCleanCoastdowns(rows);
      expect(segs, hasLength(1));
      expect(segs.first.length, 10);
    });

    test('rejects runs starting below minCadStart', () {
      final rows = _synth(r: 20, lambda: 0.05, cad0: 60, nSamples: 10);
      expect(findCleanCoastdowns(rows), isEmpty);
    });

    test('breaks the run when R jumps outside rJitterMax', () {
      // 5 samples at R=20, then R jumps to 25.
      final a = _synth(r: 20, lambda: 0.05, cad0: 100, nSamples: 5);
      final b = _synth(r: 25, lambda: 0.05, cad0: 70, nSamples: 5,
          t0: 5.0);
      final segs = findCleanCoastdowns([...a, ...b]);
      // First run is exactly 5 samples (meets minSamples=4).
      expect(segs, isNotEmpty);
      expect(segs.first.length, 5);
      expect(segs.first.first.resistance, 20);
    });

    test('tolerates ±1 R jitter within a run', () {
      final rows = _synth(r: 20, lambda: 0.05, cad0: 100, nSamples: 10);
      // Flip a couple of samples to R=21 / R=19.
      final jittered = [
        for (int i = 0; i < rows.length; i++)
          CoastdownSample(
            timestampS: rows[i].timestampS,
            resistance: i == 3 ? 21 : (i == 7 ? 19 : rows[i].resistance),
            cadenceRpmCsc: rows[i].cadenceRpmCsc,
          ),
      ];
      final segs = findCleanCoastdowns(jittered);
      expect(segs, hasLength(1));
      expect(segs.first.length, 10);
    });

    test('drops short fragments below minSamples', () {
      // Only 3 decreasing samples, then cadence rises (rider pedaling again).
      final rows = [
        _s(0, 20, 100),
        _s(1, 20, 95),
        _s(2, 20, 90),
        _s(3, 20, 110), // bumps up, breaks the run
        _s(4, 20, 115),
      ];
      expect(findCleanCoastdowns(rows), isEmpty);
    });
  });

  group('fitDecay', () {
    test('recovers known λ to 4 decimals', () {
      final rows = _synth(r: 30, lambda: 0.0625, cad0: 100, nSamples: 12);
      final fit = fitDecay(rows);
      expect(fit.lambda, closeTo(0.0625, 1e-6));
      expect(fit.r2, closeTo(1.0, 1e-9));
    });

    test('r² drops with added noise but stays high for clean segments', () {
      final rng = math.Random(42);
      final rows = [
        for (int i = 0; i < 12; i++)
          _s(i.toDouble(), 30,
              100 * math.exp(-0.05 * i) * (1 + (rng.nextDouble() - 0.5) * 0.02)),
      ];
      final fit = fitDecay(rows);
      expect(fit.lambda, closeTo(0.05, 0.005));
      expect(fit.r2, greaterThan(0.95));
    });
  });

  group('fitBrake', () {
    test('recovers (a, b) when input points lie exactly on λ(R) = a·R + b', () {
      const a = 0.005;
      const b = 0.04;
      // Build clean coastdowns at three R values with λ = a·R + b.
      final allRows = <CoastdownSample>[];
      for (final r in [10, 30, 60]) {
        final lam = a * r + b;
        final seg = _synth(
            r: r, lambda: lam, cad0: 110, nSamples: 8,
            t0: allRows.isEmpty ? 0 : allRows.last.timestampS + 5);
        allRows.addAll(seg);
        // No spacer: the next run starts at cad 110 which is > the previous
        // run's last sample, so findCleanCoastdowns naturally breaks here.
      }
      final pts = extractCoastdownPoints(allRows);
      expect(pts.length, 3);
      final fit = fitBrake(pts);
      expect(fit.aBrake, closeTo(a, 1e-6));
      expect(fit.bFriction, closeTo(b, 1e-6));
      expect(fit.rms, lessThan(1e-6));
    });

    test('streaming detector emits points as runs complete', () {
      final emitted = <CoastdownPoint>[];
      final det = CoastdownDetector(emitted.add);

      // Run 1: r=10, λ=0.05.
      for (final s in _synth(r: 10, lambda: 0.05, cad0: 110, nSamples: 10)) {
        det.push(s);
      }
      expect(emitted, isEmpty); // run still in progress
      // Cadence rises (rider re-engages) — should finalize run 1.
      det.push(_s(11, 10, 120));
      expect(emitted, hasLength(1));
      expect(emitted.first.resistance, 10);
      expect(emitted.first.lambda, closeTo(0.05, 1e-6));

      // Run 2: cadence keeps rising then a new coastdown at r=20.
      for (final s in _synth(r: 20, lambda: 0.10, cad0: 100, nSamples: 8,
          t0: 12)) {
        det.push(s);
      }
      // Rider stops fully (cad=0) — finalize run 2.
      det.push(_s(20.5, 20, 0));
      expect(emitted, hasLength(2));
      expect(emitted[1].resistance, 20);
      expect(emitted[1].lambda, closeTo(0.10, 1e-6));
    });

    test('streaming detector exposes in-progress state', () {
      final det = CoastdownDetector((_) {});
      expect(det.currentRunLength, 0);
      expect(det.currentRunR, isNull);
      for (final s in _synth(r: 22, lambda: 0.08, cad0: 90, nSamples: 3)) {
        det.push(s);
      }
      expect(det.currentRunLength, 3);
      expect(det.currentRunR, 22);
      expect(det.currentRunCadence, lessThan(90));
    });

    test('streaming detector discards short runs silently', () {
      final emitted = <CoastdownPoint>[];
      final det = CoastdownDetector(emitted.add);
      // Only 3 valid samples (below minSamples=4).
      for (final s in _synth(r: 22, lambda: 0.08, cad0: 90, nSamples: 3)) {
        det.push(s);
      }
      det.push(_s(3, 22, 0)); // forces finalize
      expect(emitted, isEmpty);
    });

    test('leadingTrim drops the first sample by default for long-enough runs',
        () {
      final emitted = <CoastdownPoint>[];
      final det = CoastdownDetector(emitted.add);
      // 10 valid samples then cadence rises (forces finalize).
      for (final s in _synth(r: 30, lambda: 0.07, cad0: 110, nSamples: 10)) {
        det.push(s);
      }
      det.push(_s(11, 30, 120));
      expect(emitted, hasLength(1));
      // Default leadingTrim=1: 10 captured samples → 9 used in the fit.
      expect(emitted.first.n, 9);
      // First sample (cad=110) is trimmed; cadHi reflects sample 1, not 0.
      expect(emitted.first.cadHi,
          closeTo(110 * math.exp(-0.07), 1e-6));
    });

    test('leadingTrim is capped to keep at least minSamples in the fit', () {
      final emitted = <CoastdownPoint>[];
      // leadingTrim=3 but a 4-sample run would be trimmed to 1 — that violates
      // minSamples=4, so the cap forces zero trimming.
      final det = CoastdownDetector(emitted.add, leadingTrim: 3);
      for (final s in _synth(r: 30, lambda: 0.07, cad0: 110, nSamples: 4)) {
        det.push(s);
      }
      det.push(_s(5, 30, 0));
      expect(emitted, hasLength(1));
      expect(emitted.first.n, 4); // no trim applied
    });

    test('rejects fewer than two distinct R values', () {
      final pts = [
        const CoastdownPoint(resistance: 20, lambda: 0.1, r2: 1.0, n: 5,
            cadHi: 100, cadLo: 60, durationS: 5),
        const CoastdownPoint(resistance: 20, lambda: 0.11, r2: 1.0, n: 5,
            cadHi: 100, cadLo: 60, durationS: 5),
      ];
      expect(() => fitBrake(pts), throwsArgumentError);
    });
  });
}
