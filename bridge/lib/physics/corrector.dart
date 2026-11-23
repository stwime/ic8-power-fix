import 'dart:collection';
import 'dart:math' as math;
import 'calibration.dart';
import 'constants.dart';
import 'resistance_filter.dart';

/// Rider power from the IC8's 1 Hz samples (R, cadence in rpm, timestamp).
/// Keeps the dial position through [ResistanceFilter] and dω/dt from a
/// 3-sample difference, and returns brake power plus the flywheel term
/// I·ω·dω/dt per sample. analysis/bridge_model.py mirrors the physics.
class Corrector {
  /// Live calibration. Read each push so a settings change retunes power
  /// instantly without restarting the BLE pipeline.
  final Calibration calibration;

  Corrector(this.calibration);

  final ResistanceFilter _rFilter = ResistanceFilter();
  final Queue<({double t, double omega})> _omegaBuf = Queue();

  /// Most recent corrected outputs (for UI / logging).
  double lastSteadyW = 0;
  double lastKeW = 0;
  double lastCorrectedW = 0;
  double lastROmega = 0;
  double lastROmegaDot = 0;
  bool lastValid = false;

  /// Append one sample. Returns the corrected power in W (or null if the input
  /// is unusable: R==100 or cad<=0). When FTMS cadence is at its cap with no
  /// CSC fallback, true cadence is unknown — we clamp to the cap value, which
  /// underestimates real power above the cap rather than dropping the sample.
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
    // When FTMS is capped and CSC isn't available, treat actual cadence as
    // exactly the cap. Underestimates at true >cap RPM, but a plausible number
    // is more useful for the rider than a dropped sample.
    final double cadForPhysics =
        capWithoutCsc ? Constants.cadCap : cadenceRpm;

    final double rDial = _rFilter.push(resistance).toDouble();

    final double omega = cadForPhysics * math.pi / 30.0;

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

    if (inactive || rcap) {
      lastValid = false;
      lastSteadyW = 0;
      lastKeW = 0;
      lastCorrectedW = 0;
      return null;
    }

    // Steady term: measured table when one is loaded, physics otherwise.
    final double pSteady = calibration.steadyPowerAt(rDial, cadForPhysics);
    // KE uses the same inertia as the steady source: effective I_crank on
    // the physics path so the powerScale slider scales both terms
    // together, the table's own I on the measured path.
    final double pKe = calibration.keICrank * omega * omegaDot;
    final double pCorrected = math.max(0.0, pSteady + pKe);

    lastSteadyW = pSteady;
    lastKeW = pKe;
    lastCorrectedW = pCorrected;
    lastValid = true;
    return pCorrected;
  }
}
