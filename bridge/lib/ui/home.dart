import 'dart:async';

import 'package:bluetooth_low_energy/bluetooth_low_energy.dart';
import 'package:flutter/material.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../ble/central.dart';
import '../ble/peripheral.dart';
import '../physics/calibration.dart';
import '../prefs.dart';
import 'settings.dart';

class HomePage extends StatefulWidget {
  final Calibration calibration;
  final AppPrefs prefs;
  const HomePage({super.key, required this.calibration, required this.prefs});
  @override
  State<HomePage> createState() => _HomePageState();
}

class _HomePageState extends State<HomePage> {
  late final IC8Central _central;
  late final IC8Peripheral _peripheral;

  final List<({Peripheral peripheral, String name, int rssi})> _found = [];
  Peripheral? _connected;
  IC8Sample? _last;
  String _status = 'idle';
  bool _scanning = false;

  StreamSubscription? _scanSub;
  StreamSubscription? _sampleSub;
  StreamSubscription? _connStateSub;
  StreamSubscription? _centralStateSub;
  StreamSubscription? _peripheralStateSub;

  BluetoothLowEnergyState _centralBleState = BluetoothLowEnergyState.unknown;
  BluetoothLowEnergyState _peripheralBleState = BluetoothLowEnergyState.unknown;

  @override
  void initState() {
    super.initState();
    _central = IC8Central(CentralManager(), widget.calibration);
    _peripheral = IC8Peripheral(PeripheralManager());

    _centralBleState = CentralManager().state;
    _peripheralBleState = PeripheralManager().state;
    _centralStateSub = CentralManager().stateChanged.listen((ev) {
      if (mounted) setState(() => _centralBleState = ev.state);
    });
    _peripheralStateSub = PeripheralManager().stateChanged.listen((ev) {
      if (mounted) setState(() => _peripheralBleState = ev.state);
    });

    _sampleSub = _central.samples.listen((s) {
      _last = s;
      final pwr = (s.correctedW ?? 0).round();
      final cad = s.cadenceRpmCsc ?? s.ftms.cadenceRpm ?? 0;
      final speed = s.ftms.speedKmh ?? 0;
      _peripheral.update(speedKmh: speed, cadenceRpm: cad, powerW: pwr);
      if (mounted) setState(() {});
    });

    _connStateSub = _central.connState.listen((cs) {
      if (!mounted) return;
      setState(() {
        switch (cs) {
          case BridgeConnState.idle:
            _status = 'Ready';
          case BridgeConnState.connecting:
            _status = 'Connecting…';
          case BridgeConnState.connected:
            _status = 'Connected — sending power to your training app';
          case BridgeConnState.reconnecting:
            _status = 'Connection lost — trying to reconnect…';
          case BridgeConnState.disconnected:
            _status = 'Stopped';
        }
      });
    });
  }

  Future<void> _authorize() async {
    setState(() => _status = 'Asking for Bluetooth permission…');
    await CentralManager().authorize();
    await PeripheralManager().authorize();
    setState(() => _status = 'Permission granted');
  }

  Future<void> _startScan() async {
    setState(() { _found.clear(); _status = 'Searching for your bike…'; _scanning = true; });
    _scanSub?.cancel();
    _scanSub = _central.scanForBikes().listen((d) {
      // BLE often emits multiple advertisement events per device — the first
      // packet may carry only service UUIDs while the scan response carries
      // the local name. Merge so we keep the best name we've seen.
      final i = _found.indexWhere((e) => e.peripheral.uuid == d.peripheral.uuid);
      if (i < 0) {
        setState(() => _found.add(d));
      } else {
        final existing = _found[i];
        final mergedName = d.name.isNotEmpty ? d.name : existing.name;
        if (mergedName != existing.name || d.rssi != existing.rssi) {
          setState(() {
            _found[i] = (peripheral: existing.peripheral,
                name: mergedName, rssi: d.rssi);
          });
        }
      }
    });
  }

  Future<void> _stopScan() async {
    await _scanSub?.cancel();
    _scanSub = null;
    await _central.stopScan();
    setState(() { _status = 'Ready'; _scanning = false; });
  }

  Future<void> _connect(Peripheral p) async {
    await _scanSub?.cancel();
    _scanSub = null;
    await _central.stopScan();
    setState(() => _scanning = false);
    try {
      // Keep the device awake while we're bridging — Rouvy/MyWhoosh runs on a
      // separate device, so this phone's job is to stay foregrounded with BLE
      // alive end-to-end of the ride. iOS bluetooth-central/peripheral
      // background modes (Info.plist) cover the case where the screen sleeps.
      await WakelockPlus.enable();
      await _central.connect(p);
      _connected = p;
      await _peripheral.start(name: widget.prefs.proxyName);
    } catch (e) {
      await WakelockPlus.disable();
      setState(() => _status = 'Could not connect: $e');
    }
  }

  Future<void> _disconnect() async {
    await _peripheral.stop();
    await _central.disconnect();
    _connected = null;
    await WakelockPlus.disable();
  }

  @override
  void dispose() {
    _scanSub?.cancel();
    _sampleSub?.cancel();
    _connStateSub?.cancel();
    _centralStateSub?.cancel();
    _peripheralStateSub?.cancel();
    WakelockPlus.disable();
    _central.dispose();
    super.dispose();
  }

  /// True when either the central or peripheral side is in a state that an
  /// authorize() call can move forward (unknown = first run, unauthorized =
  /// denied/not yet asked). poweredOn/poweredOff/unsupported are resolved.
  bool get _needsAuthorize {
    bool needs(BluetoothLowEnergyState s) =>
        s == BluetoothLowEnergyState.unknown ||
        s == BluetoothLowEnergyState.unauthorized;
    return needs(_centralBleState) || needs(_peripheralBleState);
  }

  @override
  Widget build(BuildContext context) {
    final s = _last;
    final ftms = s?.ftms;
    final pwrBroadcast = ftms?.powerW ?? 0;
    final pwrCorrected = (s?.correctedW ?? 0).round();
    final cad = s?.cadenceRpmCsc ?? ftms?.cadenceRpm ?? 0;
    final r = ftms?.resistance ?? 0;
    final hr = ftms?.heartRate ?? 0;

    return Scaffold(
      appBar: AppBar(title: const Text('IC Bridge'), actions: [
        if (_needsAuthorize) IconButton(
          onPressed: _authorize,
          icon: const Icon(Icons.bluetooth_disabled),
          tooltip: 'Grant Bluetooth permission',
        ),
        IconButton(
          onPressed: () async {
            await Navigator.of(context).push(MaterialPageRoute(
              builder: (_) => SettingsPage(
                calibration: widget.calibration,
                prefs: widget.prefs,
                central: _central,
              ),
            ));
            if (mounted) setState(() {});
          },
          icon: const Icon(Icons.settings),
          tooltip: 'Settings',
        ),
      ]),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(_status),
          const SizedBox(height: 8),
          Row(children: [
            ElevatedButton(
                onPressed: (_connected == null && !_scanning)
                    ? _startScan : null,
                child: const Text('Find bike')),
            const SizedBox(width: 8),
            ElevatedButton(
                onPressed: _scanning ? _stopScan : null,
                child: const Text('Stop')),
            const SizedBox(width: 8),
            ElevatedButton(
                onPressed: _connected != null ? _disconnect : null,
                child: const Text('Disconnect')),
          ]),
          const Divider(),
          if (_connected == null) Expanded(
            child: ListView(children: [
              for (final d in _found) ListTile(
                title: Text(d.name.isEmpty ? 'Unknown device' : d.name),
                subtitle: Text('Signal: ${d.rssi} dBm'),
                onTap: () => _connect(d.peripheral),
              ),
            ]),
          ) else Expanded(
            child: GridView.count(
              crossAxisCount: 2, mainAxisSpacing: 12, crossAxisSpacing: 12,
              childAspectRatio: 1.6, children: [
                _tile('Power', '$pwrCorrected W', highlight: true),
                _tile('Cadence', '${cad.toStringAsFixed(0)} rpm'),
                _tile('Resistance', '$r'),
                _tile('Heart rate', '$hr bpm'),
                _tile('Bike says', '$pwrBroadcast W'),
                _tile('Correction',
                    '${pwrCorrected - pwrBroadcast >= 0 ? '+' : ''}'
                    '${pwrCorrected - pwrBroadcast} W'),
              ],
            ),
          ),
        ]),
      ),
    );
  }

  Widget _tile(String label, String value, {bool highlight = false}) {
    return Container(
      decoration: BoxDecoration(
        color: highlight ? Colors.green.shade100 : Colors.grey.shade200,
        borderRadius: BorderRadius.circular(8),
      ),
      padding: const EdgeInsets.all(12),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Text(label, style: const TextStyle(fontWeight: FontWeight.bold)),
        const Spacer(),
        Text(value, style: const TextStyle(fontSize: 24)),
      ]),
    );
  }
}
