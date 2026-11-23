import 'package:flutter_test/flutter_test.dart';
import 'package:ic8_bridge/physics/calibration.dart';
import 'package:shared_preferences/shared_preferences.dart';

/// Settings saved before I_crank moved from 7.55 to 6.66 were relative to
/// the old model. A tuned value must keep giving the same output; a saved
/// default must become the new default.
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

  test('a tuned brake keeps its output; the old default maps to the new',
      () async {
    final (tuned, _) = await loadWith({'cal.alpha.v10': 180.0});
    expect(tuned.alpha, closeTo(180.0 * k, 1e-9));
    final (dflt, prefs) = await loadWith({'cal.alpha.v10': 165.0});
    expect(dflt.alpha, Calibration.defaultAlpha);
    expect(prefs.containsKey('cal.alpha.v11'), isFalse);
    expect(prefs.containsKey('cal.alpha.v10'), isFalse);
  });

  test('a value under the current key wins over an old one', () async {
    final (cal, _) = await loadWith(
        {'cal.powerScale.v10': 0.95, 'cal.powerScale.v9': 0.80});
    expect(cal.powerScale, 0.95);
  });

  test('the old defaults and the new ones give the same spin-down', () {
    // Only torque/inertia is in the spin-down data: α and τ_c moved with
    // I_crank, so the decay rate at every R is unchanged.
    expect(Calibration.defaultAlpha / Calibration.defaultICrank,
        closeTo(165.0 / 7.55, 1e-4));
    expect(Calibration.defaultTauC / Calibration.defaultICrank,
        closeTo(1.1468 / 7.55, 1e-5));
  });
}
