import 'package:flutter_test/flutter_test.dart';
import 'package:ic8_bridge/physics/calibration.dart';
import 'package:ic8_bridge/physics/corrector.dart';
import 'package:ic8_bridge/physics/power_table.dart';

PowerTable _table() => PowerTable(
      r: [10, 50, 90],
      cadenceRpm: [60, 90],
      watts: [
        [50, 100],
        [150, 300],
        [300, 600],
      ],
      iCrank: 5.0,
      source: 'test',
    );

void main() {
  group('PowerTable.powerAt', () {
    test('returns grid values at grid points', () {
      final t = _table();
      expect(t.powerAt(10, 60), 50);
      expect(t.powerAt(50, 90), 300);
      expect(t.powerAt(90, 60), 300);
    });

    test('is bilinear inside the grid', () {
      final t = _table();
      // Midpoint of the R 10–50, cad 60–90 cell: mean of the four corners.
      expect(t.powerAt(30, 75), closeTo((50 + 100 + 150 + 300) / 4, 1e-9));
      expect(t.powerAt(50, 75), closeTo(225, 1e-9));
    });

    test('clamps R outside the grid', () {
      final t = _table();
      expect(t.powerAt(0, 60), 50);
      expect(t.powerAt(100, 90), 600);
    });

    test('extrapolates cadence linearly and floors at zero', () {
      final t = _table();
      // At R=50: 150 W at 60 rpm, 300 W at 90 rpm → +5 W/rpm.
      expect(t.powerAt(50, 120), closeTo(450, 1e-9));
      expect(t.powerAt(50, 30), closeTo(0, 1e-9));
      expect(t.powerAt(50, 0), 0);
    });
  });

  group('PowerTable JSON', () {
    test('round-trips', () {
      final t = _table();
      final back = PowerTable.decode(t.encode(), defaultICrank: 7.55);
      expect(back.r, t.r);
      expect(back.cadenceRpm, t.cadenceRpm);
      expect(back.watts, t.watts);
      expect(back.iCrank, 5.0);
      expect(back.source, 'test');
    });

    test('falls back to the default inertia when i_crank is absent', () {
      const json = '{"version":1,"r":[10,20],"cadence_rpm":[60,80],'
          '"watts":[[1,2],[3,4]]}';
      final t = PowerTable.decode(json, defaultICrank: 7.55);
      expect(t.iCrank, 7.55);
    });

    test('rejects malformed input with a readable message', () {
      expect(() => PowerTable.decode('nope', defaultICrank: 7.55),
          throwsA(isA<FormatException>()));
      expect(
          () => PowerTable.decode('{"version":2}', defaultICrank: 7.55),
          throwsA(isA<FormatException>()));
      // Non-increasing R grid.
      expect(
          () => PowerTable.decode(
              '{"version":1,"r":[20,10],"cadence_rpm":[60,80],'
              '"watts":[[1,2],[3,4]]}',
              defaultICrank: 7.55),
          throwsA(isA<FormatException>()));
      // Ragged watts.
      expect(
          () => PowerTable.decode(
              '{"version":1,"r":[10,20],"cadence_rpm":[60,80],'
              '"watts":[[1,2],[3]]}',
              defaultICrank: 7.55),
          throwsA(isA<FormatException>()));
    });
  });

  group('Corrector with a measured table', () {
    test('steady power comes from the table, not the physics', () {
      final cal = Calibration.defaults()..powerTable = _table();
      final c = Corrector(cal);
      double? p;
      for (int i = 0; i < 10; i++) {
        p = c.push(timestampS: i.toDouble(), resistance: 50,
            cadenceRpm: 90, csCadenceAvailable: true, cadenceRpmFtms: 90);
      }
      expect(p, closeTo(300, 1e-6));
      expect(cal.usesTable, isTrue);
    });

    test('powerScale does not touch table output', () {
      final cal = Calibration.defaults()
        ..powerTable = _table()
        ..powerScale = 1.5;
      final c = Corrector(cal);
      double? p;
      for (int i = 0; i < 10; i++) {
        p = c.push(timestampS: i.toDouble(), resistance: 50,
            cadenceRpm: 90, csCadenceAvailable: true, cadenceRpmFtms: 90);
      }
      expect(p, closeTo(300, 1e-6));
    });

    test('acceleration term uses the table inertia', () {
      final cal = Calibration.defaults()..powerTable = _table();
      expect(cal.keICrank, 5.0);
      final c = Corrector(cal);
      for (int i = 0; i < 5; i++) {
        c.push(timestampS: i.toDouble(), resistance: 50,
            cadenceRpm: 60, csCadenceAvailable: true, cadenceRpmFtms: 60);
      }
      for (int i = 0; i < 4; i++) {
        c.push(timestampS: 5.0 + i, resistance: 50,
            cadenceRpm: 60.0 + 10.0 * (i + 1),
            csCadenceAvailable: true, cadenceRpmFtms: 60.0 + 10.0 * (i + 1));
      }
      expect(c.lastKeW, greaterThan(0));
    });

    test('removing the table restores the physics path', () {
      final cal = Calibration.defaults()..powerTable = _table();
      cal.powerTable = null;
      expect(cal.usesTable, isFalse);
      expect(cal.isAtDefaults, isTrue);
      expect(cal.keICrank, cal.effectiveICrank);
    });
  });
}
