import 'dart:async';

import 'package:app_settings/app_settings.dart';
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
  final ValueNotifier<IC8Sample?> _lastSample = ValueNotifier(null);
  String _status = 'Ready';
  _StatusTone _tone = _StatusTone.ready;
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
      final pwr = (s.correctedW ?? 0).round();
      final cad = s.cadenceRpmCsc ?? s.ftms.cadenceRpm ?? 0;
      final speed = s.ftms.speedKmh ?? 0;
      _peripheral.update(speedKmh: speed, cadenceRpm: cad, powerW: pwr);
      _lastSample.value = s;
    });

    _connStateSub = _central.connState.listen((cs) {
      if (!mounted) return;
      setState(() {
        switch (cs) {
          case BridgeConnState.idle:
            _status = 'Ready';
            _tone = _StatusTone.ready;
          case BridgeConnState.connecting:
            _status = 'Connecting…';
            _tone = _StatusTone.working;
          case BridgeConnState.connected:
            _status = 'Connected, sending power to your training app';
            _tone = _StatusTone.connected;
          case BridgeConnState.reconnecting:
            _status = 'Connection lost, trying to reconnect…';
            _tone = _StatusTone.warning;
          case BridgeConnState.disconnected:
            _status = 'Stopped';
            _tone = _StatusTone.ready;
        }
      });
    });
  }

  /// Open the right Settings page (or trigger first-launch authorize) for
  /// whichever BLE state the bridge is currently stuck in.
  Future<void> _openBleSettings() async {
    bool either(BluetoothLowEnergyState s) =>
        _centralBleState == s || _peripheralBleState == s;
    if (either(BluetoothLowEnergyState.unauthorized)) {
      await AppSettings.openAppSettings();
    } else if (either(BluetoothLowEnergyState.poweredOff)) {
      await AppSettings.openAppSettings(type: AppSettingsType.bluetooth);
    } else if (either(BluetoothLowEnergyState.unknown)) {
      // First run: authorize() actually triggers the OS prompt.
      setState(() {
        _status = 'Asking for Bluetooth permission…';
        _tone = _StatusTone.working;
      });
      await CentralManager().authorize();
      await PeripheralManager().authorize();
    }
  }

  Future<void> _startScan() async {
    setState(() {
      _found.clear();
      _status = 'Searching for your bike…';
      _tone = _StatusTone.working;
      _scanning = true;
    });
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
    setState(() {
      _status = 'Ready';
      _tone = _StatusTone.ready;
      _scanning = false;
    });
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
      setState(() {
        _status = 'Could not connect: $e';
        _tone = _StatusTone.warning;
      });
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
    _lastSample.dispose();
    WakelockPlus.disable();
    _central.dispose();
    super.dispose();
  }

  /// Inspect the central/peripheral BLE states and surface what's wrong, if
  /// anything. Returns null when both sides are powered on. `fixable` is
  /// false only for `unsupported` — every other broken state has a Settings
  /// page or first-run prompt that can resolve it.
  ({String message, bool fixable})? get _bleProblem {
    bool either(BluetoothLowEnergyState s) =>
        _centralBleState == s || _peripheralBleState == s;
    if (either(BluetoothLowEnergyState.unsupported)) {
      return (
        message: 'Bluetooth Low Energy is not supported on this device.',
        fixable: false,
      );
    }
    if (either(BluetoothLowEnergyState.unauthorized)) {
      return (
        message: 'Bluetooth permission is off for IC Bridge. '
            'Open Settings → IC Bridge and turn Bluetooth on.',
        fixable: true,
      );
    }
    if (either(BluetoothLowEnergyState.poweredOff)) {
      return (
        message: 'Bluetooth is turned off on this phone. '
            'Open Settings → Bluetooth and turn it on.',
        fixable: true,
      );
    }
    if (either(BluetoothLowEnergyState.unknown)) {
      return (
        message: 'Bluetooth is not authorized yet. Tap to allow it.',
        fixable: true,
      );
    }
    return null;
  }

  @override
  Widget build(BuildContext context) {
    final ble = _bleProblem;
    final canStartScan = _connected == null && !_scanning;
    // When BLE isn't ready, repurpose "Find bike" as the fix-it button so the
    // user has a primary CTA that actually does something useful — see the
    // banner directly above for the explanation of what it'll do.
    final findOnPressed = canStartScan
        ? (ble == null
            ? _startScan
            : (ble.fixable ? _openBleSettings : null))
        : null;
    final findLabel =
        ble != null && ble.fixable ? 'Open Settings' : 'Find bike';

    return Scaffold(
      appBar: AppBar(title: const Text('IC Bridge'), actions: [
        IconButton(
          onPressed: () async {
            await Navigator.of(context).push(MaterialPageRoute(
              builder: (_) => SettingsPage(
                calibration: widget.calibration,
                prefs: widget.prefs,
                central: _central,
              ),
            ));
            // Settings mutates Calibration in place; rebuild so any displayed
            // derived value (e.g. proxyName future use) reflects the change.
            if (mounted) setState(() {});
          },
          icon: const Icon(Icons.settings),
          tooltip: 'Settings',
        ),
      ]),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          _StatusPill(label: _status, tone: _tone),
          const SizedBox(height: 12),
          if (ble != null) _BleBanner(message: ble.message, fixable: ble.fixable),
          Wrap(spacing: 8, runSpacing: 8, children: [
            ElevatedButton(
                onPressed: findOnPressed,
                child: Text(findLabel)),
            ElevatedButton(
                onPressed: _scanning ? _stopScan : null,
                child: const Text('Stop')),
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
            child: ValueListenableBuilder<IC8Sample?>(
              valueListenable: _lastSample,
              builder: (context, s, _) {
                final ftms = s?.ftms;
                final pwrBroadcast = ftms?.powerW ?? 0;
                final pwrCorrected = (s?.correctedW ?? 0).round();
                final cad = s?.cadenceRpmCsc ?? ftms?.cadenceRpm ?? 0;
                final r = ftms?.resistance ?? 0;
                final hr = ftms?.heartRate ?? 0;
                return GridView.extent(
                  maxCrossAxisExtent: 220,
                  mainAxisSpacing: 12,
                  crossAxisSpacing: 12,
                  childAspectRatio: 1.6,
                  children: [
                    _Tile('Power', '$pwrCorrected W', highlight: true),
                    _Tile('Cadence', '${cad.toStringAsFixed(0)} rpm'),
                    _Tile('Resistance', '$r'),
                    _Tile('Heart rate', '$hr bpm'),
                    _Tile('Bike says', '$pwrBroadcast W'),
                    _Tile('Correction',
                        '${pwrCorrected - pwrBroadcast >= 0 ? '+' : ''}'
                        '${pwrCorrected - pwrBroadcast} W'),
                  ],
                );
              },
            ),
          ),
        ]),
      ),
    );
  }

}

class _BleBanner extends StatelessWidget {
  final String message;
  final bool fixable;
  const _BleBanner({required this.message, required this.fixable});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final bg = fixable ? cs.tertiaryContainer : cs.errorContainer;
    final fg = fixable ? cs.onTertiaryContainer : cs.onErrorContainer;
    return Semantics(
      liveRegion: true,
      container: true,
      child: Container(
        margin: const EdgeInsets.only(bottom: 12),
        padding: const EdgeInsets.all(12),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(12),
        ),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(Icons.bluetooth_disabled, color: fg),
          const SizedBox(width: 12),
          Expanded(child: Text(message, style: TextStyle(color: fg))),
        ]),
      ),
    );
  }
}

class _Tile extends StatelessWidget {
  final String label;
  final String value;
  final bool highlight;
  const _Tile(this.label, this.value, {this.highlight = false});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final bg = highlight ? cs.primaryContainer : cs.surfaceContainerHigh;
    final fg = highlight ? cs.onPrimaryContainer : cs.onSurface;
    return Container(
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(8),
      ),
      padding: const EdgeInsets.all(12),
      child: Semantics(
        label: '$label $value',
        container: true,
        excludeSemantics: true,
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label,
              style: TextStyle(color: fg, fontWeight: FontWeight.bold)),
          const Spacer(),
          Text(value, style: TextStyle(color: fg, fontSize: 24)),
        ]),
      ),
    );
  }
}

enum _StatusTone { ready, working, connected, warning }

class _StatusPill extends StatelessWidget {
  final String label;
  final _StatusTone tone;
  const _StatusPill({required this.label, required this.tone});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final (Color dot, Color bg, Color fg) = switch (tone) {
      _StatusTone.ready =>
        (cs.onSurfaceVariant, cs.surfaceContainerHigh, cs.onSurface),
      _StatusTone.working =>
        (cs.secondary, cs.secondaryContainer, cs.onSecondaryContainer),
      _StatusTone.connected =>
        (cs.primary, cs.primaryContainer, cs.onPrimaryContainer),
      _StatusTone.warning =>
        (cs.tertiary, cs.tertiaryContainer, cs.onTertiaryContainer),
    };
    final spinning = tone == _StatusTone.working;
    return Semantics(
      liveRegion: true,
      container: true,
      label: label,
      child: ExcludeSemantics(
        child: Container(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
          decoration: BoxDecoration(
            color: bg,
            borderRadius: BorderRadius.circular(999),
          ),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            if (spinning)
              ExcludeSemantics(
                child: SizedBox(
                  width: 12, height: 12,
                  child: CircularProgressIndicator(
                    strokeWidth: 2, valueColor: AlwaysStoppedAnimation(dot)),
                ),
              )
            else
              _Dot(color: dot),
            const SizedBox(width: 8),
            Flexible(
              child: Text(
                label,
                style: TextStyle(
                  color: fg,
                  fontWeight: FontWeight.w600,
                  fontSize: 13,
                  letterSpacing: 0.2,
                ),
              ),
            ),
          ]),
        ),
      ),
    );
  }
}

class _Dot extends StatelessWidget {
  final Color color;
  const _Dot({required this.color});
  @override
  Widget build(BuildContext context) {
    return Container(
      width: 10, height: 10,
      decoration: BoxDecoration(color: color, shape: BoxShape.circle),
    );
  }
}
