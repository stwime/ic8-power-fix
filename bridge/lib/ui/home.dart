import 'dart:async';

import 'package:bluetooth_low_energy/bluetooth_low_energy.dart';
import 'package:flutter/material.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../ble/central.dart';
import '../ble/peripheral.dart';

class HomePage extends StatefulWidget {
  const HomePage({super.key});
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

  @override
  void initState() {
    super.initState();
    _central = IC8Central(CentralManager());
    _peripheral = IC8Peripheral(PeripheralManager());

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
            _status = 'idle';
          case BridgeConnState.connecting:
            _status = 'connecting…';
          case BridgeConnState.connected:
            _status = 'bridging';
          case BridgeConnState.reconnecting:
            _status = 'bike dropped — reconnecting…';
          case BridgeConnState.disconnected:
            _status = 'disconnected';
        }
      });
    });
  }

  Future<void> _authorize() async {
    setState(() => _status = 'authorizing…');
    await CentralManager().authorize();
    await PeripheralManager().authorize();
    setState(() => _status = 'authorized');
  }

  Future<void> _startScan() async {
    setState(() { _found.clear(); _status = 'scanning'; _scanning = true; });
    _scanSub?.cancel();
    _scanSub = _central.scanForBikes().listen((d) {
      if (!_found.any((e) => e.peripheral.uuid == d.peripheral.uuid)) {
        setState(() => _found.add(d));
      }
    });
  }

  Future<void> _stopScan() async {
    await _scanSub?.cancel();
    _scanSub = null;
    await _central.stopScan();
    setState(() { _status = 'idle'; _scanning = false; });
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
      await _peripheral.start();
    } catch (e) {
      await WakelockPlus.disable();
      setState(() => _status = 'error: $e');
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
    WakelockPlus.disable();
    _central.dispose();
    super.dispose();
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
    final csc = s?.cadenceRpmCsc != null;

    return Scaffold(
      appBar: AppBar(title: const Text('IC8 Bridge'), actions: [
        IconButton(
          onPressed: _authorize,
          icon: const Icon(Icons.shield),
          tooltip: 'Authorize BLE',
        ),
      ]),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('Status: $_status'),
          const SizedBox(height: 8),
          Row(children: [
            ElevatedButton(
                onPressed: (_connected == null && !_scanning)
                    ? _startScan : null,
                child: const Text('Scan')),
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
                title: Text(d.name.isEmpty ? '(unnamed)' : d.name),
                subtitle: Text('${d.peripheral.uuid}  rssi=${d.rssi}'),
                onTap: () => _connect(d.peripheral),
              ),
            ]),
          ) else Expanded(
            child: GridView.count(
              crossAxisCount: 2, mainAxisSpacing: 12, crossAxisSpacing: 12,
              childAspectRatio: 1.6, children: [
                _tile('CORRECTED', '$pwrCorrected W', highlight: true),
                _tile('BROADCAST', '$pwrBroadcast W'),
                _tile('CADENCE', '${cad.toStringAsFixed(1)} rpm'
                    '${csc ? ' (CSC)' : ' (FTMS)'}'),
                _tile('R', '$r'),
                _tile('HR', '$hr bpm'),
                _tile('Δ', '${pwrCorrected - pwrBroadcast} W'),
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
