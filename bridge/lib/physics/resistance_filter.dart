import 'constants.dart';

/// The dial position the corrector uses.
///
/// Untouched, the IC8's reading flickers by 1, occasionally 2, for single
/// samples; turning the dial moves it 3 or more per sample. A change of
/// [Constants.rJump] or more is taken at once. A smaller one is taken when
/// the next sample is off in the same direction, so a flicker never shows
/// and a slow turn lags one sample at most. analysis/bridge_model.py
/// (smooth_resistance) mirrors this.
class ResistanceFilter {
  int? _held;
  int _pendingSign = 0;

  int push(int raw) {
    final held = _held;
    if (held == null) return _held = raw;
    final d = raw - held;
    if (d == 0) {
      _pendingSign = 0;
    } else if (d.abs() >= Constants.rJump || d.sign == _pendingSign) {
      _held = raw;
      _pendingSign = 0;
    } else {
      _pendingSign = d.sign;
    }
    return _held!;
  }
}
