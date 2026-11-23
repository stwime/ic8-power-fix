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
      (10, 60.0, 22.489278522979546),
      (30, 80.0, 145.8944057165424),
      (50, 100.0, 462.3585830292022),
      (70, 60.0, 309.035484454725),
      (90, 100.0, 799.7151485203892),
    ];
    for (final (r, cad, expected) in cases) {
      final p = cal.brakePowerAt(r.toDouble(), cad * math.pi / 30.0);
      expect(p, closeTo(expected, 1e-6), reason: 'R=$r cad=$cad');
    }
    expect(cal.tauBrakeAt(0, 60 * math.pi / 30.0),
        closeTo(1.6560286178455672, 1e-9));
  });
}
