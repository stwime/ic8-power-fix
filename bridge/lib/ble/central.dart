import 'dart:async';
import 'dart:typed_data';

import 'package:bluetooth_low_energy/bluetooth_low_energy.dart';

import 'decode_csc.dart';
import 'decode_ftms.dart';
import '../physics/calibration.dart';
import '../physics/corrector.dart';

const String _kFtmsService = '1826';
const String _kIndoorBikeData = '2AD2';
const String _kCscService = '1816';
const String _kCscMeasurement = '2A5B';

/// One row in our internal stream. Mirrors what parse_nrf_log.py emits per FTMS
/// notification: FTMS fields + most-recent CSC + derived CSC cadence.
///
/// [crankRevs] and [crankEventTimeS] are the latest CSC values as of this
/// FTMS sample, with revs and event time both unwrapped to monotonic 32-bit
/// counts and seconds. They feed the per-revolution coastdown fitter, which
/// uses CSC event timing (1/1024 s precision) instead of the BLE-arrival
/// timestamp [tS] (~0.5 s of jitter relative to the actual rev event).
class IC8Sample {
  final double tS;
  final FtmsIndoorBikeData ftms;
  final double? cadenceRpmCsc;
  final int? crankRevs;
  final double? crankEventTimeS;
  final double? correctedW;
  final Corrector corrector;

  IC8Sample({
    required this.tS,
    required this.ftms,
    required this.cadenceRpmCsc,
    required this.crankRevs,
    required this.crankEventTimeS,
    required this.correctedW,
    required this.corrector,
  });
}

/// Connection state surfaced to the UI. The link can drop spontaneously
/// (BLE radio glitches, the bike sleeping during a long stop, the phone
/// briefly walking out of range) — when that happens we keep `desired` set
/// and try to reattach. The states differ from raw GATT state because we
/// also need to expose "we know we're disconnected and we're waiting to
/// retry" as a distinct condition from "the user asked us to stop."
enum BridgeConnState {
  idle,
  connecting,
  connected,
  reconnecting,
  disconnected, // user-initiated
}

/// Discover, connect, and stream IC8 samples.
class IC8Central {
  final CentralManager manager;
  final Corrector corrector;

  IC8Central(this.manager, Calibration calibration)
      : corrector = Corrector(calibration) {
    _connStateSub = manager.connectionStateChanged.listen(_onConnState);
  }

  final StreamController<IC8Sample> _samples =
      StreamController<IC8Sample>.broadcast();
  Stream<IC8Sample> get samples => _samples.stream;

  final StreamController<BridgeConnState> _connStateCtrl =
      StreamController<BridgeConnState>.broadcast();
  Stream<BridgeConnState> get connState => _connStateCtrl.stream;
  BridgeConnState _state = BridgeConnState.idle;
  BridgeConnState get state => _state;

  Peripheral? _peripheral;
  Peripheral? _desired; // intended bike — kept across drops for auto-reconnect
  GATTCharacteristic? _ftmsChar;
  GATTCharacteristic? _cscChar;

  // Latest CSC reading, unwrapped.
  ({int? crankRevs, double? crankT, int? wheelRevs, double? wheelT})? _lastCsc;
  ({int crankRevs, double crankT})? _prevCscForDeriv;
  double? _t0;

  StreamSubscription? _notifySub;
  StreamSubscription? _connStateSub;
  Timer? _reconnectTimer;
  int _reconnectAttempt = 0;
  // Backoff for a flaky bike: the first try is fast (radio glitches recover
  // in seconds) but we cap at 30s so we don't hammer the BLE stack across a
  // long absence (e.g. bike powered off mid-ride).
  static const _reconnectBackoffSeconds = [1, 2, 4, 8, 15, 30];

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

  /// Connect to [p] and remember it as the desired bike — if BLE drops, the
  /// central will reattach automatically until [disconnect] is called.
  Future<void> connect(Peripheral p) async {
    _desired = p;
    _reconnectAttempt = 0;
    _reconnectTimer?.cancel();
    await _attach(p);
  }

  Future<void> _attach(Peripheral p) async {
    _setState(BridgeConnState.connecting);
    _peripheral = p;
    await manager.connect(p);
    final services = await manager.discoverGATT(p);

    _ftmsChar = null;
    _cscChar = null;
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

    await _notifySub?.cancel();
    _notifySub = manager.characteristicNotified.listen(_onNotify);
    await manager.setCharacteristicNotifyState(p, _ftmsChar!, state: true);
    if (_cscChar != null) {
      await manager.setCharacteristicNotifyState(p, _cscChar!, state: true);
    }
    _reconnectAttempt = 0;
    _setState(BridgeConnState.connected);
  }

  Future<void> disconnect() async {
    _desired = null;
    _reconnectTimer?.cancel();
    _reconnectTimer = null;
    await _notifySub?.cancel();
    _notifySub = null;
    if (_peripheral != null) {
      try {
        await manager.disconnect(_peripheral!);
      } catch (_) {/* already disconnected */}
    }
    _peripheral = null;
    _setState(BridgeConnState.disconnected);
  }

  void _onConnState(PeripheralConnectionStateChangedEventArgs ev) {
    final desired = _desired;
    if (desired == null) return;
    if (ev.peripheral.uuid != desired.uuid) return;
    if (ev.state == ConnectionState.disconnected
        && _state != BridgeConnState.connecting) {
      // Unexpected drop — bike fell asleep, BLE radio glitched, app paused
      // long enough for the OS to tear down the link, etc. Re-establish.
      _scheduleReconnect();
    }
  }

  void _scheduleReconnect() {
    final desired = _desired;
    if (desired == null) return;
    _reconnectTimer?.cancel();
    final attempt = _reconnectAttempt;
    final secs = attempt < _reconnectBackoffSeconds.length
        ? _reconnectBackoffSeconds[attempt]
        : _reconnectBackoffSeconds.last;
    _reconnectAttempt++;
    _setState(BridgeConnState.reconnecting);
    _reconnectTimer = Timer(Duration(seconds: secs), () async {
      if (_desired == null) return;
      try {
        await _attach(_desired!);
      } catch (_) {
        if (_desired != null) _scheduleReconnect();
      }
    });
  }

  void _setState(BridgeConnState s) {
    _state = s;
    _connStateCtrl.add(s);
  }

  Future<void> dispose() async {
    _reconnectTimer?.cancel();
    await _connStateSub?.cancel();
    await _notifySub?.cancel();
    await _connStateCtrl.close();
    await _samples.close();
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
      crankRevs: last?.crankRevs,
      crankEventTimeS: last?.crankT,
      correctedW: corrected, corrector: corrector,
    ));
  }
}
