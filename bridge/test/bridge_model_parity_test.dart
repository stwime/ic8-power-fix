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
      (10, 60.0, 22.428455984009855),
      (30, 80.0, 142.09430437269813),
      (50, 100.0, 518.974382070843),
      (70, 60.0, 322.76300555901906),
      (90, 100.0, 1257.3539788990513),
    ];
    for (final (r, cad, expected) in cases) {
      final p = cal.brakePowerAt(r.toDouble(), cad * math.pi / 30.0);
      expect(p, closeTo(expected, 1e-6), reason: 'R=$r cad=$cad');
    }
    expect(cal.tauBrakeAt(0, 60 * math.pi / 30.0),
        closeTo(1.6777346799761355, 1e-9));
  });
}
