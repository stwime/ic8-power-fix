import 'dart:async';
import 'dart:io' show Platform;
import 'dart:typed_data';

import 'package:bluetooth_low_energy/bluetooth_low_energy.dart';

import 'decode_ftms.dart';

const int _kFtmsService = 0x1826;
const int _kIndoorBikeData = 0x2AD2;
const int _kFtmsFeature = 0x2ACC;
const int _kFtmsStatus = 0x2ADA;
const int _kFtmsControlPoint = 0x2AD9;

// FTMS Control Point opcodes (the bike is mechanical-only, so we only
// acknowledge the housekeeping ones; everything else is honestly NotSupported).
const int _kCpOpRequestControl = 0x00;
const int _kCpOpReset = 0x01;
const int _kCpOpStartResume = 0x07;
const int _kCpOpStopPause = 0x08;
const int _kCpOpResponse = 0x80;
const int _kCpResultSuccess = 0x01;
const int _kCpResultNotSupported = 0x02;

const int _kCyclingPowerService = 0x1818;
const int _kCyclingPowerMeasurement = 0x2A63;
const int _kCyclingPowerFeature = 0x2A65;
const int _kSensorLocation = 0x2A5D;

/// Cycling Power Feature (uint32 LE) — we only emit instantaneous power, so all
/// optional flags off. Apps still require the characteristic to exist.
final Uint8List _cpsFeatureValue = Uint8List.fromList([0, 0, 0, 0]);

/// Sensor Location (uint8) — 13 = "Rear Hub". Cosmetic.
final Uint8List _sensorLocationValue = Uint8List.fromList([13]);

/// Fitness Machine Feature: two uint32 LE.
///   Fitness Machine Features  bit 1 (Cadence) + bit 14 (Power Measurement)
///                             = 0x00004002
///   Target Setting Features   = 0 (we don't support trainer control)
final Uint8List _ftmsFeatureValue = Uint8List.fromList([
  0x02, 0x40, 0x00, 0x00,
  0x00, 0x00, 0x00, 0x00,
]);

/// Fitness Machine Status: opcode 0x04 = "Fitness Machine Started or Resumed
/// by the User". We send this once a central subscribes.
final Uint8List _ftmsStatusStarted = Uint8List.fromList([0x04]);

/// Advertise as a virtual FTMS + Cycling Power source. Subscribed centrals
/// (Rouvy/MyWhoosh on a separate device) get notifications driven by IC8
/// samples — see [update]. The 1Hz pacer is a fallback for receivers that
/// expect data even before the IC8 is producing samples.
class IC8Peripheral {
  final PeripheralManager manager;

  late GATTCharacteristic _indoorBikeDataChar;
  late GATTCharacteristic _ftmsFeatureChar;
  late GATTCharacteristic _ftmsStatusChar;
  late GATTCharacteristic _ftmsControlPointChar;
  late GATTCharacteristic _cyclingPowerMeasChar;
  late GATTCharacteristic _cyclingPowerFeatureChar;
  late GATTCharacteristic _sensorLocationChar;

  bool _running = false;
  Timer? _pacer;

  double _speedKmh = 0;
  double _cadenceRpm = 0;
  int _powerW = 0;

  final Set<Central> _subscribedFtms = {};
  final Set<Central> _subscribedCps = {};
  final Set<Central> _subscribedFtmsStatus = {};
  final Set<Central> _subscribedFtmsCp = {};

  StreamSubscription? _readReqSub;
  StreamSubscription? _writeReqSub;
  StreamSubscription? _notifyStateSub;

  IC8Peripheral(this.manager);

  Future<void> start({String name = 'IC Bike (corrected)'}) async {
    if (_running) return;

    _indoorBikeDataChar = GATTCharacteristic.mutable(
      uuid: UUID.short(_kIndoorBikeData),
      properties: const [GATTCharacteristicProperty.notify],
      permissions: const [],
      descriptors: const [],
    );
    _ftmsFeatureChar = GATTCharacteristic.mutable(
      uuid: UUID.short(_kFtmsFeature),
      properties: const [GATTCharacteristicProperty.read],
      permissions: const [GATTCharacteristicPermission.read],
      descriptors: const [],
    );
    _ftmsStatusChar = GATTCharacteristic.mutable(
      uuid: UUID.short(_kFtmsStatus),
      properties: const [GATTCharacteristicProperty.notify],
      permissions: const [],
      descriptors: const [],
    );
    _ftmsControlPointChar = GATTCharacteristic.mutable(
      uuid: UUID.short(_kFtmsControlPoint),
      properties: const [
        GATTCharacteristicProperty.write,
        GATTCharacteristicProperty.indicate,
      ],
      permissions: const [GATTCharacteristicPermission.write],
      descriptors: const [],
    );
    final ftmsService = GATTService(
      uuid: UUID.short(_kFtmsService),
      isPrimary: true,
      includedServices: [],
      characteristics: [
        _ftmsFeatureChar,
        _indoorBikeDataChar,
        _ftmsStatusChar,
        _ftmsControlPointChar,
      ],
    );

    _cyclingPowerMeasChar = GATTCharacteristic.mutable(
      uuid: UUID.short(_kCyclingPowerMeasurement),
      properties: const [GATTCharacteristicProperty.notify],
      permissions: const [],
      descriptors: const [],
    );
    _cyclingPowerFeatureChar = GATTCharacteristic.mutable(
      uuid: UUID.short(_kCyclingPowerFeature),
      properties: const [GATTCharacteristicProperty.read],
      permissions: const [GATTCharacteristicPermission.read],
      descriptors: const [],
    );
    _sensorLocationChar = GATTCharacteristic.mutable(
      uuid: UUID.short(_kSensorLocation),
      properties: const [GATTCharacteristicProperty.read],
      permissions: const [GATTCharacteristicPermission.read],
      descriptors: const [],
    );
    final cpsService = GATTService(
      uuid: UUID.short(_kCyclingPowerService),
      isPrimary: true,
      includedServices: [],
      characteristics: [
        _cyclingPowerFeatureChar,
        _cyclingPowerMeasChar,
        _sensorLocationChar,
      ],
    );

    await manager.removeAllServices();
    await manager.addService(ftmsService);
    await manager.addService(cpsService);

    _readReqSub = manager.characteristicReadRequested.listen((ev) async {
      final uuid = ev.characteristic.uuid;
      Uint8List? value;
      if (uuid == _ftmsFeatureChar.uuid) {
        value = _ftmsFeatureValue;
      } else if (uuid == _cyclingPowerFeatureChar.uuid) {
        value = _cpsFeatureValue;
      } else if (uuid == _sensorLocationChar.uuid) {
        value = _sensorLocationValue;
      }
      if (value != null) {
        final off = ev.request.offset;
        final slice = off >= value.length
            ? Uint8List(0)
            : Uint8List.fromList(value.sublist(off));
        await manager.respondReadRequestWithValue(ev.request, value: slice);
      } else {
        await manager.respondReadRequestWithError(
            ev.request, error: GATTError.readNotPermitted);
      }
    });

    _notifyStateSub = manager.characteristicNotifyStateChanged.listen((ev) async {
      if (ev.characteristic.uuid == _indoorBikeDataChar.uuid) {
        ev.state ? _subscribedFtms.add(ev.central) : _subscribedFtms.remove(ev.central);
      } else if (ev.characteristic.uuid == _cyclingPowerMeasChar.uuid) {
        ev.state ? _subscribedCps.add(ev.central) : _subscribedCps.remove(ev.central);
      } else if (ev.characteristic.uuid == _ftmsStatusChar.uuid) {
        if (ev.state) {
          _subscribedFtmsStatus.add(ev.central);
          // Tell newly-subscribed app the machine is running.
          await manager.notifyCharacteristic(
              ev.central, _ftmsStatusChar, value: _ftmsStatusStarted);
        } else {
          _subscribedFtmsStatus.remove(ev.central);
        }
      } else if (ev.characteristic.uuid == _ftmsControlPointChar.uuid) {
        ev.state ? _subscribedFtmsCp.add(ev.central) : _subscribedFtmsCp.remove(ev.central);
      }
    });

    _writeReqSub = manager.characteristicWriteRequested.listen(_onWriteRequest);

    // FTMS Service Data (3 bytes):
    //   byte 0    Flags                  bit 0 = Fitness Machine Available
    //   bytes 1-2 Fitness Machine Type   uint16 LE, bit 5 = Indoor Bike
    // The bluetooth_low_energy Darwin backend throws UnsupportedError if
    // serviceData is non-empty (see advertisement.dart docs + darwin api.dart),
    // so we only set it on platforms where it's actually supported.
    final supportsServiceData = Platform.isAndroid || Platform.isWindows;
    await manager.startAdvertising(Advertisement(
      name: name,
      serviceUUIDs: [
        UUID.short(_kFtmsService),
        UUID.short(_kCyclingPowerService),
      ],
      serviceData: supportsServiceData
          ? {UUID.short(_kFtmsService): Uint8List.fromList([0x01, 0x20, 0x00])}
          : const {},
      manufacturerSpecificData: const [],
    ));

    _pacer = Timer.periodic(const Duration(seconds: 1), (_) => _tick());
    _running = true;
  }

  Future<void> stop() async {
    _pacer?.cancel();
    _pacer = null;
    await _readReqSub?.cancel();
    _readReqSub = null;
    await _writeReqSub?.cancel();
    _writeReqSub = null;
    await _notifyStateSub?.cancel();
    _notifyStateSub = null;
    if (_running) await manager.stopAdvertising();
    await manager.removeAllServices();
    _running = false;
    _subscribedFtms.clear();
    _subscribedCps.clear();
    _subscribedFtmsStatus.clear();
    _subscribedFtmsCp.clear();
  }

  /// FTMS Control Point: this is a mechanical-resistance bike, so we ack the
  /// session-housekeeping opcodes (Request Control / Reset / Start / Stop) so
  /// apps stop nagging, and honestly NotSupport everything else (Set Target
  /// Power, Set Sim Params, etc.) so they fall back to power-only mode rather
  /// than driving an ERG loop into a brake we can't actually move.
  Future<void> _onWriteRequest(GATTCharacteristicWriteRequestedEventArgs ev) async {
    if (ev.characteristic.uuid != _ftmsControlPointChar.uuid) {
      await manager.respondWriteRequest(ev.request);
      return;
    }
    await manager.respondWriteRequest(ev.request);

    final value = ev.request.value;
    if (value.isEmpty) return;
    final opcode = value[0];
    final int result;
    switch (opcode) {
      case _kCpOpRequestControl:
      case _kCpOpReset:
      case _kCpOpStartResume:
      case _kCpOpStopPause:
        result = _kCpResultSuccess;
        break;
      default:
        result = _kCpResultNotSupported;
    }
    final response = Uint8List.fromList([_kCpOpResponse, opcode, result]);
    for (final c in _subscribedFtmsCp) {
      try {
        await manager.notifyCharacteristic(
            c, _ftmsControlPointChar, value: response);
      } catch (_) {/* central may have dropped subscription */}
    }
  }

  /// Update the most recent values and notify subscribers immediately.
  /// Driven by IC8 sample arrival (~1 Hz). Backgrounded apps can rely on this
  /// rather than the [Timer.periodic] fallback.
  void update({required double speedKmh, required double cadenceRpm,
               required int powerW}) {
    _speedKmh = speedKmh;
    _cadenceRpm = cadenceRpm;
    _powerW = powerW;
    _broadcast();
  }

  void _tick() {
    // Fallback pacer when IC8 isn't producing samples (e.g., between rides).
    _broadcast();
  }

  void _broadcast() {
    if (!_running) return;
    final ftmsBytes = encodeIndoorBikeData(
      speedKmh: _speedKmh, cadenceRpm: _cadenceRpm, powerW: _powerW);
    final cpsBytes = encodeCyclingPowerMeasurement(_powerW);
    _notifyAll(_subscribedFtms, _indoorBikeDataChar, ftmsBytes);
    _notifyAll(_subscribedCps, _cyclingPowerMeasChar, cpsBytes);
  }

  /// Notify every subscriber and prune any whose write fails — a central
  /// that dropped the link without us seeing the unsubscribe will otherwise
  /// stay in the set forever and we'd retry it every tick.
  void _notifyAll(Set<Central> subs, GATTCharacteristic char, Uint8List value) {
    // Snapshot the set: we may mutate it from the catchError callback.
    for (final c in subs.toList()) {
      unawaited(
        manager.notifyCharacteristic(c, char, value: value).catchError((_) {
          subs.remove(c);
        }),
      );
    }
  }
}
