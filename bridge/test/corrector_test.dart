import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';
import 'package:ic8_bridge/physics/calibration.dart';
import 'package:ic8_bridge/physics/constants.dart';
import 'package:ic8_bridge/physics/corrector.dart';

void main() {
  group('Corrector.push', () {
    test('steady state matches τ_brake(R, ω)·ω formula', () {
      final cal = Calibration.defaults();
      final c = Corrector(cal);
      // Warm up the median filter and ω-buffer at constant R, constant cad.
      const r = 30;
      const cad = 80.0;
      double? p;
      for (int i = 0; i < 10; i++) {
        p = c.push(
          timestampS: i.toDouble(),
          resistance: r,
          cadenceRpm: cad,
          csCadenceAvailable: true,
          cadenceRpmFtms: cad,
        );
      }
      final omega = cad * math.pi / 30.0;
      final expected = cal.brakePowerAt(r.toDouble(), omega);
      expect(p, isNotNull);
      expect(p!, closeTo(expected, 0.5));
      expect(c.lastKeW.abs(), closeTo(0, 1e-6));
    });

    test('KE term is positive on a cadence ramp-up', () {
      final c = Corrector(Calibration.defaults());
      // Hold then ramp from 60 → 90 rpm at +10 rpm/s.
      double? p;
      for (int i = 0; i < 5; i++) {
        c.push(timestampS: i.toDouble(), resistance: 25, cadenceRpm: 60.0,
               csCadenceAvailable: true, cadenceRpmFtms: 60.0);
      }
      for (int i = 0; i < 4; i++) {
        p = c.push(timestampS: 5.0 + i.toDouble(), resistance: 25,
                   cadenceRpm: 60.0 + 10.0 * (i + 1),
                   csCadenceAvailable: true, cadenceRpmFtms: 60.0 + 10.0 * (i + 1));
      }
      expect(p, isNotNull);
      expect(c.lastKeW, greaterThan(0));
    });

    test('returns null when cadence is zero (rider stopped)', () {
      final c = Corrector(Calibration.defaults());
      final p = c.push(timestampS: 0, resistance: 30, cadenceRpm: 0,
                       csCadenceAvailable: true, cadenceRpmFtms: 0);
      expect(p, isNull);
      expect(c.lastValid, isFalse);
    });

    test('clamps to cap when FTMS is at cap and no CSC backup is present', () {
      final cal = Calibration.defaults();
      final c = Corrector(cal);
      // Sample with FTMS reading at cap (true cadence unknown). Without CSC
      // we treat actual cad as exactly the cap rather than dropping the row.
      double? p;
      for (int i = 0; i < 5; i++) {
        p = c.push(timestampS: i.toDouble(), resistance: 30,
                   cadenceRpm: Constants.cadCap,
                   csCadenceAvailable: false,
                   cadenceRpmFtms: Constants.cadCap);
      }
      const omega = Constants.cadCap * math.pi / 30.0;
      final expected = cal.brakePowerAt(30.0, omega);
      expect(p, isNotNull);
      expect(p!, closeTo(expected, 0.5));
    });

    test('higher FTMS at cap (no CSC) still produces only cap-equivalent power', () {
      final cal = Calibration.defaults();
      final c = Corrector(cal);
      // Caller passes whatever value FTMS reported above cap (e.g. 130),
      // physics still uses cap value so the result matches the cap-equivalent.
      double? p;
      for (int i = 0; i < 5; i++) {
        p = c.push(timestampS: i.toDouble(), resistance: 30,
                   cadenceRpm: 130.0,
                   csCadenceAvailable: false,
                   cadenceRpmFtms: 130.0);
      }
      const omega = Constants.cadCap * math.pi / 30.0;
      final expected = cal.brakePowerAt(30.0, omega);
      expect(p, isNotNull);
      expect(p!, closeTo(expected, 0.5));
    });

    test('accepts cadence above FTMS cap when CSC is available', () {
      final c = Corrector(Calibration.defaults());
      double? p;
      for (int i = 0; i < 5; i++) {
        p = c.push(timestampS: i.toDouble(), resistance: 30,
                   cadenceRpm: 130.0, csCadenceAvailable: true,
                   cadenceRpmFtms: Constants.cadCap);
      }
      expect(p, isNotNull);
      expect(c.lastValid, isTrue);
    });

    test('returns null at R cap', () {
      final c = Corrector(Calibration.defaults());
      final p = c.push(timestampS: 0, resistance: Constants.rCap,
                       cadenceRpm: 80, csCadenceAvailable: true, cadenceRpmFtms: 80);
      expect(p, isNull);
    });

    test('R median filter rejects single-sample jitter', () {
      final cal = Calibration.defaults();
      final c = Corrector(cal);
      // Settle at R=30
      for (int i = 0; i < 5; i++) {
        c.push(timestampS: i.toDouble(), resistance: 30, cadenceRpm: 80,
               csCadenceAvailable: true, cadenceRpmFtms: 80);
      }
      // One outlier at R=80 — median of last 5 stays near 30
      final p = c.push(timestampS: 5, resistance: 80, cadenceRpm: 80,
                       csCadenceAvailable: true, cadenceRpmFtms: 80);
      const omega = 80.0 * math.pi / 30.0;
      final expected = cal.brakePowerAt(30.0, omega);
      expect(p, isNotNull);
      expect(p!, closeTo(expected, 1.0));
    });

    test('corrected power is non-negative even on aggressive deceleration', () {
      final c = Corrector(Calibration.defaults());
      // High cad, then sudden drop — KE term will be negative.
      for (int i = 0; i < 5; i++) {
        c.push(timestampS: i.toDouble(), resistance: 30, cadenceRpm: 100,
               csCadenceAvailable: true, cadenceRpmFtms: 100);
      }
      final p = c.push(timestampS: 5, resistance: 30, cadenceRpm: 50,
                       csCadenceAvailable: true, cadenceRpmFtms: 50);
      expect(p, isNotNull);
      expect(p!, greaterThanOrEqualTo(0));
    });

    test('powerScale scales steady AND KE by the same factor', () {
      // Coupled scaling: steady term, residual drag, and KE term all scale
      // linearly with powerScale, so total output is linear in powerScale
      // without any cadence/R-shape distortion.
      final cal1 = Calibration.defaults();
      cal1.powerScale = 1.0;
      final c1 = Corrector(cal1);
      // Hold then ramp 60 → 90 rpm at +10 rpm/s, R=25.
      double? p1;
      for (int i = 0; i < 5; i++) {
        c1.push(timestampS: i.toDouble(), resistance: 25, cadenceRpm: 60.0,
            csCadenceAvailable: true, cadenceRpmFtms: 60.0);
      }
      for (int i = 0; i < 4; i++) {
        p1 = c1.push(timestampS: 5.0 + i.toDouble(), resistance: 25,
            cadenceRpm: 60.0 + 10.0 * (i + 1),
            csCadenceAvailable: true, cadenceRpmFtms: 60.0 + 10.0 * (i + 1));
      }
      final pSteady1 = c1.lastSteadyW;
      final pKe1 = c1.lastKeW;

      final cal2 = Calibration.defaults();
      cal2.powerScale = 0.5;
      final c2 = Corrector(cal2);
      double? p2;
      for (int i = 0; i < 5; i++) {
        c2.push(timestampS: i.toDouble(), resistance: 25, cadenceRpm: 60.0,
            csCadenceAvailable: true, cadenceRpmFtms: 60.0);
      }
      for (int i = 0; i < 4; i++) {
        p2 = c2.push(timestampS: 5.0 + i.toDouble(), resistance: 25,
            cadenceRpm: 60.0 + 10.0 * (i + 1),
            csCadenceAvailable: true, cadenceRpmFtms: 60.0 + 10.0 * (i + 1));
      }
      final pSteady2 = c2.lastSteadyW;
      final pKe2 = c2.lastKeW;

      // Both scale by 0.5 (within FP tolerance).
      expect(pSteady2 / pSteady1, closeTo(0.5, 1e-6));
      expect(pKe2 / pKe1, closeTo(0.5, 1e-6));
      // And the sum scales by 0.5 too — i.e. the KE/steady ratio is
      // preserved across powerScale changes.
      expect(p2! / p1!, closeTo(0.5, 1e-6));
    });

    test('live calibration change retunes power without restart', () {
      final cal = Calibration.defaults();
      final c = Corrector(cal);
      const r = 30;
      const cad = 80.0;
      for (int i = 0; i < 10; i++) {
        c.push(timestampS: i.toDouble(), resistance: r, cadenceRpm: cad,
               csCadenceAvailable: true, cadenceRpmFtms: cad);
      }
      final omega = cad * math.pi / 30.0;
      final pBefore = c.lastCorrectedW;
      expect(pBefore, closeTo(cal.brakePowerAt(r.toDouble(), omega), 0.5));
      // Mutate powerScale in place — same Calibration object, no
      // Corrector restart. powerScale acts as a multiplier on α inside
      // tauBrakeAt, so a 1.5× scale should ≈1.5× the eddy-brake portion
      // of steady-state power.
      cal.powerScale = 1.5;
      final p = c.push(timestampS: 10, resistance: r, cadenceRpm: cad,
                       csCadenceAvailable: true, cadenceRpmFtms: cad);
      expect(p, isNotNull);
      expect(p!, closeTo(cal.brakePowerAt(r.toDouble(), omega), 0.5));
      expect(p, isNot(closeTo(pBefore, 1.0)));
    });
  });
}
