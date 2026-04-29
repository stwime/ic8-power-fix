import 'dart:async';
import 'dart:typed_data';

import 'package:bluetooth_low_energy/bluetooth_low_energy.dart';

import 'decode_csc.dart';
import 'decode_ftms.dart';
import '../physics/corrector.dart';

const String _kFtmsService = '1826';
const String _kIndoorBikeData = '2AD2';
const String _kCscService = '1816';
const String _kCscMeasurement = '2A5B';

/// One row in our internal stream. Mirrors what parse_nrf_log.py emits per FTMS
/// notification: FTMS fields + most-recent CSC + derived CSC cadence.
class IC8Sample {
  final double tS;
  final FtmsIndoorBikeData ftms;
  final double? cadenceRpmCsc;
  final double? correctedW;
  final Corrector corrector;

  IC8Sample({
    required this.tS,
    required this.ftms,
    required this.cadenceRpmCsc,
    required this.correctedW,
    required this.corrector,
  });
}

/// Discover, connect, and stream IC8 samples.
class IC8Central {
  final CentralManager manager;
  final Corrector corrector = Corrector();

  IC8Central(this.manager);

  final StreamController<IC8Sample> _samples =
      StreamController<IC8Sample>.broadcast();
  Stream<IC8Sample> get samples => _samples.stream;

  Peripheral? _peripheral;
  GATTCharacteristic? _ftmsChar;
  GATTCharacteristic? _cscChar;

  // Latest CSC reading, unwrapped.
  ({int? crankRevs, double? crankT, int? wheelRevs, double? wheelT})? _lastCsc;
  ({int crankRevs, double crankT})? _prevCscForDeriv;
  double? _t0;

  StreamSubscription? _notifySub;

  /// Stream discovered peripherals advertising the FTMS service. UI uses this.
  Stream<({Peripheral peripheral, String name, int rssi})> scanForBikes() async* {
    final controller =
        StreamController<({Peripheral peripheral, String name, int rssi})>();
    final sub = manager.discovered.listen((event) {
      final adv = event.advertisement;
      final name = adv.name ?? '';
      final isFtms = adv.serviceUUIDs.any(
          (u) => u.toString().toLowerCase().contains('1826'));
      if (isFtms || name.toUpperCase().startsWith('IC')) {
        controller.add((peripheral: event.peripheral, name: name, rssi: event.rssi));
      }
    });
    await manager.startDiscovery();
    yield* controller.stream;
    await sub.cancel();
  }

  Future<void> stopScan() async => manager.stopDiscovery();

  Future<void> connect(Peripheral p) async {
    _peripheral = p;
    await manager.connect(p);
    final services = await manager.discoverGATT(p);

    for (final s in services) {
      final sUuid = s.uuid.toString().toLowerCase();
      for (final c in s.characteristics) {
        final cUuid = c.uuid.toString().toLowerCase();
        if (sUuid.contains(_kFtmsService.toLowerCase()) &&
            cUuid.contains(_kIndoorBikeData.toLowerCase())) {
          _ftmsChar = c;
        }
        if (sUuid.contains(_kCscService.toLowerCase()) &&
            cUuid.contains(_kCscMeasurement.toLowerCase())) {
          _cscChar = c;
        }
      }
    }
    if (_ftmsChar == null) {
      throw StateError('IC8 missing FTMS Indoor Bike Data characteristic');
    }

    _notifySub = manager.characteristicNotified.listen(_onNotify);
    await manager.setCharacteristicNotifyState(p, _ftmsChar!, state: true);
    if (_cscChar != null) {
      await manager.setCharacteristicNotifyState(p, _cscChar!, state: true);
    }
  }

  Future<void> disconnect() async {
    await _notifySub?.cancel();
    if (_peripheral != null) await manager.disconnect(_peripheral!);
    _peripheral = null;
  }

  void _onNotify(GATTCharacteristicNotifiedEventArgs ev) {
    final cUuid = ev.characteristic.uuid.toString().toLowerCase();
    final value = Uint8List.fromList(ev.value);

    if (cUuid.contains(_kCscMeasurement.toLowerCase())) {
      _ingestCsc(value);
      return;
    }
    if (cUuid.contains(_kIndoorBikeData.toLowerCase())) {
      _ingestFtms(value);
    }
  }

  void _ingestCsc(Uint8List payload) {
    final csc = CscMeasurement.decode(payload);
    final prev = _lastCsc;
    int? crankRevs = csc.crankRevs;
    double? crankT = csc.crankEventTimeS;
    int? wheelRevs = csc.wheelRevs;
    double? wheelT = csc.wheelEventTimeS;
    if (prev != null) {
      if (crankT != null && prev.crankT != null) {
        crankT = unwrapEventTime(prev.crankT!, crankT);
      }
      if (crankRevs != null && prev.crankRevs != null) {
        crankRevs = unwrapRevs(prev.crankRevs!, crankRevs, 65536);
      }
      if (wheelT != null && prev.wheelT != null) {
        wheelT = unwrapEventTime(prev.wheelT!, wheelT);
      }
    }
    _lastCsc = (crankRevs: crankRevs, crankT: crankT,
                wheelRevs: wheelRevs, wheelT: wheelT);
  }

  void _ingestFtms(Uint8List payload) {
    final ftms = FtmsIndoorBikeData.decode(payload);
    final nowMs = DateTime.now().millisecondsSinceEpoch / 1000.0;
    _t0 ??= nowMs;
    final tS = nowMs - _t0!;

    // CSC-derived cadence: (Δrevs / Δevent_time) × 60. Only advance the deriv
    // baseline when crank_event_time changed — gives sub-rpm resolution at low cad.
    double? cadCsc;
    final last = _lastCsc;
    final prev = _prevCscForDeriv;
    if (last != null && last.crankRevs != null && last.crankT != null) {
      if (prev != null) {
        final dRev = last.crankRevs! - prev.crankRevs;
        final dT = last.crankT! - prev.crankT;
        if (dRev > 0 && dT > 0) cadCsc = dRev / dT * 60.0;
      }
      if (prev == null || last.crankT! > prev.crankT + 1e-6) {
        _prevCscForDeriv = (crankRevs: last.crankRevs!, crankT: last.crankT!);
      }
    }

    final cadFtms = ftms.cadenceRpm ?? 0.0;
    final cadence = cadCsc ?? cadFtms;
    final r = ftms.resistance ?? 0;

    final corrected = corrector.push(
      timestampS: tS,
      resistance: r,
      cadenceRpm: cadence,
      csCadenceAvailable: cadCsc != null,
      cadenceRpmFtms: cadFtms,
    );

    _samples.add(IC8Sample(
      tS: tS, ftms: ftms, cadenceRpmCsc: cadCsc,
      correctedW: corrected, corrector: corrector,
    ));
  }
}
