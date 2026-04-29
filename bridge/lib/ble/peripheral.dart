import 'dart:async';

import 'package:bluetooth_low_energy/bluetooth_low_energy.dart';

import 'decode_ftms.dart';

const int _kFtmsService = 0x1826;
const int _kIndoorBikeData = 0x2AD2;
const int _kCyclingPowerService = 0x1818;
const int _kCyclingPowerMeasurement = 0x2A63;

/// Advertise as a virtual FTMS + Cycling Power source. Subscribed centrals
/// (Rouvy/MyWhoosh on a separate device) get notifications at 1Hz.
class IC8Peripheral {
  final PeripheralManager manager;

  late GATTCharacteristic _indoorBikeDataChar;
  late GATTCharacteristic _cyclingPowerMeasChar;
  bool _running = false;
  Timer? _pacer;

  /// Latest values to publish on the next 1Hz tick.
  double _speedKmh = 0;
  double _cadenceRpm = 0;
  int _powerW = 0;

  final Set<Central> _subscribedFtms = {};
  final Set<Central> _subscribedCps = {};

  IC8Peripheral(this.manager);

  Future<void> start({String name = 'IC8 Bridge'}) async {
    if (_running) return;

    final ftmsService = GATTService(
      uuid: UUID.short(_kFtmsService),
      isPrimary: true,
      includedServices: [],
      characteristics: [
        GATTCharacteristic.mutable(
          uuid: UUID.short(_kIndoorBikeData),
          properties: const [GATTCharacteristicProperty.notify],
          permissions: const [],
          descriptors: const [],
        ),
      ],
    );
    _indoorBikeDataChar = ftmsService.characteristics.first;

    final cpsService = GATTService(
      uuid: UUID.short(_kCyclingPowerService),
      isPrimary: true,
      includedServices: [],
      characteristics: [
        GATTCharacteristic.mutable(
          uuid: UUID.short(_kCyclingPowerMeasurement),
          properties: const [GATTCharacteristicProperty.notify],
          permissions: const [],
          descriptors: const [],
        ),
      ],
    );
    _cyclingPowerMeasChar = cpsService.characteristics.first;

    await manager.removeAllServices();
    await manager.addService(ftmsService);
    await manager.addService(cpsService);

    manager.characteristicNotifyStateChanged.listen((ev) {
      if (ev.characteristic.uuid == _indoorBikeDataChar.uuid) {
        ev.state ? _subscribedFtms.add(ev.central) : _subscribedFtms.remove(ev.central);
      } else if (ev.characteristic.uuid == _cyclingPowerMeasChar.uuid) {
        ev.state ? _subscribedCps.add(ev.central) : _subscribedCps.remove(ev.central);
      }
    });

    await manager.startAdvertising(Advertisement(
      name: name,
      serviceUUIDs: [
        UUID.short(_kFtmsService),
        UUID.short(_kCyclingPowerService),
      ],
      serviceData: const {},
      manufacturerSpecificData: const [],
    ));

    _pacer = Timer.periodic(const Duration(seconds: 1), (_) => _tick());
    _running = true;
  }

  Future<void> stop() async {
    _pacer?.cancel();
    _pacer = null;
    if (_running) await manager.stopAdvertising();
    await manager.removeAllServices();
    _running = false;
    _subscribedFtms.clear();
    _subscribedCps.clear();
  }

  void update({required double speedKmh, required double cadenceRpm,
               required int powerW}) {
    _speedKmh = speedKmh;
    _cadenceRpm = cadenceRpm;
    _powerW = powerW;
  }

  void _tick() {
    final ftmsBytes = encodeIndoorBikeData(
      speedKmh: _speedKmh, cadenceRpm: _cadenceRpm, powerW: _powerW);
    final cpsBytes = encodeCyclingPowerMeasurement(_powerW);
    for (final c in _subscribedFtms) {
      manager.notifyCharacteristic(c, _indoorBikeDataChar, value: ftmsBytes);
    }
    for (final c in _subscribedCps) {
      manager.notifyCharacteristic(c, _cyclingPowerMeasChar, value: cpsBytes);
    }
  }
}
