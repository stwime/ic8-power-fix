import 'dart:math' as math;

import 'package:flutter_test/flutter_test.dart';
import 'package:ic8_bridge/physics/calibration.dart';

/// Pins the Dart model to analysis/bridge_model.py so the offline scripts
/// compute exactly what the app broadcasts. Values printed by
/// `python3 -c "from analysis.bridge_model import brake_power; ..."`.
void main() {
  test('brakePowerAt matches analysis/bridge_model.py', () {
    final cal = Calibration.defaults();
    const cases = [
      (10, 60.0, 25.494673254128777),
      (30, 80.0, 165.39060528944125),
      (50, 100.0, 524.144274235855),
      (70, 60.0, 350.33235088200934),
      (90, 100.0, 906.5821533567471),
    ];
    for (final (r, cad, expected) in cases) {
      final p = cal.brakePowerAt(r.toDouble(), cad * math.pi / 30.0);
      expect(p, closeTo(expected, 1e-6), reason: 'R=$r cad=$cad');
    }
    expect(cal.tauBrakeAt(0, 60 * math.pi / 30.0),
        closeTo(1.8773459556657706, 1e-9));
  });
}
