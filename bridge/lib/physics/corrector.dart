import 'dart:collection';
import 'dart:math' as math;
import 'constants.dart';

/// Streaming version of analysis/correct_power.py.
///
/// Feed it 1Hz samples from the IC8 (R, cadence in rpm, timestamp). It maintains
/// rolling state to compute median-filtered R and a 3-sample central difference
/// for dω/dt, and returns corrected power per sample.
class Corrector {
  final Queue<int> _rBuf = Queue();
  final Queue<({double t, double omega})> _omegaBuf = Queue();

  /// Most recent corrected outputs (for UI / logging).
  double lastSteadyW = 0;
  double lastKeW = 0;
  double lastCorrectedW = 0;
  double lastROmega = 0;
  double lastROmegaDot = 0;
  bool lastValid = false;

  /// Append one sample. Returns the corrected power in W (or null if the input
  /// was masked out: cadence cap without CSC backup, R==100, or cad<=0).
  double? push({
    required double timestampS,
    required int resistance,
    required double cadenceRpm,
    required bool csCadenceAvailable,
    required double cadenceRpmFtms,
  }) {
    // Saturation masks
    final bool inactive = cadenceRpm <= 0;
    final bool rcap = resistance >= Constants.rCap;
    final bool capWithoutCsc =
        cadenceRpmFtms >= Constants.cadCap && !csCadenceAvailable;

    // Slide R buffer for median filter
    _rBuf.addLast(resistance);
    while (_rBuf.length > Constants.rSmoothWindow) {
      _rBuf.removeFirst();
    }
    final List<int> rSorted = _rBuf.toList()..sort();
    final double rSmooth = rSorted[rSorted.length ~/ 2].toDouble();

    final double omega = cadenceRpm * math.pi / 30.0;

    // Slide omega buffer for central diff
    _omegaBuf.addLast((t: timestampS, omega: omega));
    while (_omegaBuf.length > Constants.omegaDotWindow) {
      _omegaBuf.removeFirst();
    }
    double omegaDot = 0.0;
    if (_omegaBuf.length >= 2) {
      final first = _omegaBuf.first;
      final last = _omegaBuf.last;
      final dt = last.t - first.t;
      if (dt > 0) omegaDot = (last.omega - first.omega) / dt;
    }

    lastROmega = omega;
    lastROmegaDot = omegaDot;

    if (inactive || rcap || capWithoutCsc) {
      lastValid = false;
      lastSteadyW = 0;
      lastKeW = 0;
      lastCorrectedW = 0;
      return null;
    }

    final double pSteady = (Constants.aBrake * rSmooth + Constants.bFriction) *
        Constants.iCrank * omega * omega;
    final double pKe = Constants.iCrank * omega * omegaDot;
    final double pCorrected = math.max(0.0, pSteady + pKe);

    lastSteadyW = pSteady;
    lastKeW = pKe;
    lastCorrectedW = pCorrected;
    lastValid = true;
    return pCorrected;
  }
}
