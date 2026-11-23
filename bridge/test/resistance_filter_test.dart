import 'package:flutter_test/flutter_test.dart';
import 'package:ic8_bridge/physics/resistance_filter.dart';

/// Sequences are from BLE logs in data/calibration; expected outputs are
/// analysis/bridge_model.py smooth_resistance on the same input.
void main() {
  List<int> run(List<int> raw) {
    final f = ResistanceFilter();
    return [for (final r in raw) f.push(r)];
  }

  test('a ±1 flicker while untouched never shows', () {
    // spin_downs_apr29.csv rows 58–66
    expect(run([15, 15, 15, 14, 15, 14, 14, 14, 14]),
        [15, 15, 15, 15, 15, 15, 14, 14, 14]);
  });

  test('a ±2 flicker never shows; the settled value is taken', () {
    // spin_downs_apr29.csv rows 140–150
    expect(run([59, 59, 59, 59, 57, 59, 57, 57, 57, 57, 57]),
        [59, 59, 59, 59, 59, 59, 59, 57, 57, 57, 57]);
  });

  test('a dial turn is taken in the same sample', () {
    // holding_a_few_seconds_at_lots_of_R_values.csv rows 290–298
    expect(run([67, 67, 67, 67, 66, 67, 75, 75, 75]),
        [67, 67, 67, 67, 67, 67, 75, 75, 75]);
  });

  test('a slow turn lags one sample at most', () {
    // holding_a_few_seconds_at_lots_of_R_values.csv rows 128–136
    expect(run([21, 21, 21, 19, 20, 19, 19, 18, 18]),
        [21, 21, 21, 21, 20, 20, 19, 19, 18]);
    expect(run([30, 31, 32, 33, 34]), [30, 30, 32, 32, 34]);
  });
}
