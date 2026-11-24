import 'package:flutter_test/flutter_test.dart';
import 'package:ic8_bridge/physics/calibration.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Settings saved under an older model. A tuned value must keep giving the
/// same output (Power scale) or the same ratio to its default (brake); a
/// saved default must become the new default.
void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  const k = Calibration.defaultICrank / 7.55;

  Future<(Calibration, SharedPreferences)> loadWith(
      Map<String, Object> saved) async {
    SharedPreferences.setMockInitialValues(saved);
    final cal = await Calibration.load();
    return (cal, await SharedPreferences.getInstance());
  }

  test('nothing saved gives the defaults', () async {
    final (cal, _) = await loadWith({});
    expect(cal.isAtDefaults, isTrue);
  });

  test('a tuned Power scale keeps its output, once', () async {
    final (cal, prefs) = await loadWith({'cal.powerScale.v9': 0.90});
    expect(cal.powerScale, 1.02);
    expect(prefs.getDouble('cal.powerScale.v10'), 1.02);
    expect(prefs.containsKey('cal.powerScale.v9'), isFalse);
    // Old output 0.90 × old model equals new output within the 1% step.
    expect(cal.powerScale * k, closeTo(0.90, 0.005));
  });

  test('a Power scale saved at the old default becomes the new default',
      () async {
    final (cal, prefs) = await loadWith({'cal.powerScale.v9': 1.0});
    expect(cal.powerScale, Calibration.defaultPowerScale);
    expect(prefs.containsKey('cal.powerScale.v10'), isFalse);
    expect(prefs.containsKey('cal.powerScale.v9'), isFalse);
    expect(cal.isAtDefaults, isTrue);
  });

  test('a converted Power scale stays on the slider', () async {
    final (cal, _) = await loadWith({'cal.powerScale.v9': 1.9});
    expect(cal.powerScale, Calibration.powerScaleMax);
  });

  test('a tuned α carries over as the same ratio to its default, once',
      () async {
    final (v11, prefs) = await loadWith({'cal.alpha.v11': 145.55 * 1.2});
    expect(v11.brake, closeTo(Calibration.defaultBrake * 1.2, 1e-9));
    expect(prefs.getDouble('cal.brake.v12'), v11.brake);
    expect(prefs.containsKey('cal.alpha.v11'), isFalse);
    final (v10, _) = await loadWith({'cal.alpha.v10': 165.0 * 0.9});
    expect(v10.brake, closeTo(Calibration.defaultBrake * 0.9, 1e-9));
  });

  test('an α saved at its old default becomes the new default', () async {
    for (final (key, dflt) in [('cal.alpha.v11', 145.55), ('cal.alpha.v10', 165.0)]) {
      final (cal, prefs) = await loadWith({key: dflt});
      expect(cal.brake, Calibration.defaultBrake);
      expect(prefs.containsKey('cal.brake.v12'), isFalse);
      expect(prefs.containsKey(key), isFalse);
    }
  });

  test('a β fit against the old brake shape falls back to the default',
      () async {
    final (cal, prefs) = await loadWith({'cal.betaW.v10': 0.02});
    expect(cal.beta, Calibration.defaultBeta);
    expect(prefs.containsKey('cal.betaW.v10'), isFalse);
  });

  test('a value under the current key wins over an old one', () async {
    final (cal, _) = await loadWith(
        {'cal.powerScale.v10': 0.95, 'cal.powerScale.v9': 0.80});
    expect(cal.powerScale, 0.95);
  });

}
