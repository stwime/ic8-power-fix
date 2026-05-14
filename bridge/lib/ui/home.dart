import 'dart:async';

import 'package:app_settings/app_settings.dart';
import 'package:bluetooth_low_energy/bluetooth_low_energy.dart';
import 'package:flutter/foundation.dart' show ValueListenable, debugPrint;
import 'package:flutter/material.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../ble/central.dart';
import '../ble/peripheral.dart';
import '../physics/calibration.dart';
import '../prefs.dart';
import 'settings.dart';
import 'tokens.dart';

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

  // Scan results live in a ValueNotifier so RSSI churn during discovery
  // (10+ packets/sec/device) only rebuilds the device list, not the whole
  // Scaffold. RSSI deltas below _rssiThresh are also suppressed since the
  // value is shown rounded.
  final ValueNotifier<List<({Peripheral peripheral, String name, int rssi})>>
      _found = ValueNotifier(const []);
  static const int _rssiThresh = 3;
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
            _status = 'Sending power to your training app';
            _tone = _StatusTone.connected;
          case BridgeConnState.reconnecting:
            _status = 'Connection lost, reconnecting…';
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
    _found.value = const [];
    setState(() {
      _status = 'Searching for your bike…';
      _tone = _StatusTone.working;
      _scanning = true;
    });
    _scanSub?.cancel();
    _scanSub = _central.scanForBikes().listen((d) {
      // BLE often emits multiple advertisement events per device — the first
      // packet may carry only service UUIDs while the scan response carries
      // the local name. Merge so we keep the best name we've seen. Suppress
      // small RSSI flickers — without the threshold, packet-rate updates
      // (~10 Hz/device) would rebuild the list pointlessly.
      final list = _found.value;
      final i = list.indexWhere((e) => e.peripheral.uuid == d.peripheral.uuid);
      if (i < 0) {
        _found.value = [...list, d];
        return;
      }
      final existing = list[i];
      final mergedName = d.name.isNotEmpty ? d.name : existing.name;
      final nameChanged = mergedName != existing.name;
      final rssiChanged = (d.rssi - existing.rssi).abs() >= _rssiThresh;
      if (!nameChanged && !rssiChanged) return;
      final next = List.of(list);
      next[i] = (peripheral: existing.peripheral,
          name: mergedName, rssi: d.rssi);
      _found.value = next;
    }, onError: (e) {
      // startDiscovery throws when BLE flips off mid-scan or permission is
      // revoked. Without this handler the error escapes the zone, the
      // spinner stays, and Stop is the only way out.
      if (!mounted) return;
      setState(() {
        _status = 'Could not scan. Check Bluetooth and try again.';
        _tone = _StatusTone.warning;
        _scanning = false;
      });
    });
  }

  Future<void> _stopScan() async {
    // Flip the UI synchronously: cancelling the async* generator's `await for`
    // and the platform stopDiscovery call can each block for a noticeable time,
    // and if either throws we still want the spinner to go away.
    setState(() {
      _status = 'Ready';
      _tone = _StatusTone.ready;
      _scanning = false;
    });
    final sub = _scanSub;
    _scanSub = null;
    try { await sub?.cancel(); } catch (_) {}
    try { await _central.stopScan(); } catch (_) {}
  }

  Future<void> _connect(Peripheral p) async {
    setState(() => _scanning = false);
    final sub = _scanSub;
    _scanSub = null;
    try { await sub?.cancel(); } catch (_) {}
    try { await _central.stopScan(); } catch (_) {}
    // Keep the device awake while we're bridging — Rouvy/MyWhoosh runs on a
    // separate device, so this phone's job is to stay foregrounded with BLE
    // alive end-to-end of the ride. iOS bluetooth-central/peripheral
    // background modes (Info.plist) cover the case where the screen sleeps.
    await WakelockPlus.enable();
    try {
      await _central.connect(p);
    } catch (e, st) {
      // Central never came up — that's the actual "could not connect" case.
      debugPrint('central.connect failed: $e\n$st');
      await WakelockPlus.disable();
      setState(() {
        _status = 'Could not connect. Try again, or pick another bike.';
        _tone = _StatusTone.warning;
      });
      return;
    }
    setState(() => _connected = p);
    try {
      await _peripheral.start(name: widget.prefs.proxyName);
    } catch (e, st) {
      // Bike is connected and producing samples; only the rebroadcast failed.
      // Don't overwrite the "connected" status with a misleading error — but
      // do log: silent failures here used to mask an iOS-only serviceData bug.
      debugPrint('peripheral.start failed: $e\n$st');
      setState(() {
        _status = 'Connected, but rebroadcast did not start. '
            'Training apps won\'t see corrected power — try reconnecting.';
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
    _found.dispose();
    _lastSample.dispose();
    WakelockPlus.disable();
    _central.dispose();
    super.dispose();
  }

  /// Inspect the central/peripheral BLE states and surface what's wrong, if
  /// anything. Returns null when both sides are powered on. `fixable` is
  /// false only for `unsupported` — every other broken state has a Settings
  /// page or first-run prompt that can resolve it.
  ({String message, bool fixable, IconData icon})? get _bleProblem {
    bool either(BluetoothLowEnergyState s) =>
        _centralBleState == s || _peripheralBleState == s;
    if (either(BluetoothLowEnergyState.unsupported)) {
      return (
        message: 'Bluetooth Low Energy is not supported on this device.',
        fixable: false,
        icon: Icons.error_outline,
      );
    }
    if (either(BluetoothLowEnergyState.unauthorized)) {
      return (
        message: 'Bluetooth permission is off for IC Bridge. '
            'Open Settings → IC Bridge and turn Bluetooth on.',
        fixable: true,
        icon: Icons.lock_outline,
      );
    }
    if (either(BluetoothLowEnergyState.poweredOff)) {
      return (
        message: 'Bluetooth is turned off on this phone. '
            'Open Settings → Bluetooth and turn it on.',
        fixable: true,
        icon: Icons.bluetooth_disabled,
      );
    }
    if (either(BluetoothLowEnergyState.unknown)) {
      return (
        message: 'Bluetooth is not authorized yet. Tap to allow it.',
        fixable: true,
        icon: Icons.bluetooth_searching,
      );
    }
    return null;
  }

  /// Single contextual primary action. Only one of Find / Stop / Disconnect /
  /// Open Settings is meaningful in any given state, so we render exactly one
  /// button — clearer hierarchy than three same-weight buttons in a row.
  _PrimaryAction get _primaryAction {
    if (_connected != null) {
      return _PrimaryAction(
        label: 'Disconnect',
        icon: Icons.link_off,
        onPressed: _disconnect,
      );
    }
    if (_scanning) {
      return _PrimaryAction(
        label: 'Stop',
        icon: Icons.close,
        onPressed: _stopScan,
      );
    }
    final ble = _bleProblem;
    if (ble != null && ble.fixable) {
      return _PrimaryAction(
        label: 'Open Settings',
        icon: Icons.settings_outlined,
        onPressed: _openBleSettings,
      );
    }
    return _PrimaryAction(
      label: 'Find bike',
      icon: Icons.bluetooth_searching,
      onPressed: ble == null ? _startScan : null,
    );
  }

  @override
  Widget build(BuildContext context) {
    final ble = _bleProblem;
    final action = _primaryAction;

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
          icon: const Icon(Icons.settings_outlined),
          tooltip: 'Settings',
        ),
      ]),
      body: Padding(
        padding: const EdgeInsets.fromLTRB(
            Insets.lg, Insets.md, Insets.lg, Insets.lg),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          _StatusPill(label: _status, tone: _tone),
          const SizedBox(height: Insets.md),
          if (ble != null) ...[
            _BleBanner(
              message: ble.message,
              fixable: ble.fixable,
              icon: ble.icon,
            ),
            const SizedBox(height: Insets.md),
          ],
          SizedBox(
            width: double.infinity,
            child: FilledButton.icon(
              onPressed: action.onPressed,
              icon: Icon(action.icon),
              label: Text(action.label),
              style: FilledButton.styleFrom(
                padding: const EdgeInsets.symmetric(vertical: Insets.md),
              ),
            ),
          ),
          const SizedBox(height: Insets.lg),
          Expanded(
            child: AnimatedSwitcher(
              duration: Motion.normal,
              switchInCurve: Motion.curve,
              switchOutCurve: Motion.curve,
              child: _connected == null
                  ? _ScanArea(
                      key: const ValueKey('scan'),
                      found: _found,
                      onPick: _connect,
                      scanning: _scanning,
                      blocked: ble != null,
                    )
                  : _LiveData(
                      key: const ValueKey('live'),
                      sample: _lastSample,
                    ),
            ),
          ),
        ]),
      ),
    );
  }
}

class _PrimaryAction {
  final String label;
  final IconData icon;
  final VoidCallback? onPressed;
  _PrimaryAction({
    required this.label,
    required this.icon,
    required this.onPressed,
  });
}

class _BleBanner extends StatelessWidget {
  final String message;
  final bool fixable;
  final IconData icon;
  const _BleBanner({
    required this.message,
    required this.fixable,
    required this.icon,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final bg = fixable ? cs.tertiaryContainer : cs.errorContainer;
    final fg = fixable ? cs.onTertiaryContainer : cs.onErrorContainer;
    return Semantics(
      liveRegion: true,
      container: true,
      child: Container(
        padding: const EdgeInsets.all(Insets.md),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(Radii.tile),
        ),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(icon, color: fg),
          const SizedBox(width: Insets.md),
          Expanded(child: Text(message, style: TextStyle(color: fg))),
        ]),
      ),
    );
  }
}

/// Scan list area. Shows three states: blocked-by-BLE (nothing to show),
/// empty (just started or no bikes nearby), and populated (tappable list).
class _ScanArea extends StatelessWidget {
  final ValueListenable<List<({Peripheral peripheral, String name, int rssi})>>
      found;
  final void Function(Peripheral) onPick;
  final bool scanning;
  final bool blocked;
  const _ScanArea({
    super.key,
    required this.found,
    required this.onPick,
    required this.scanning,
    required this.blocked,
  });

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<
        List<({Peripheral peripheral, String name, int rssi})>>(
      valueListenable: found,
      builder: (context, list, _) {
        if (list.isEmpty) {
          return _ScanEmpty(scanning: scanning, blocked: blocked);
        }
        return ListView.separated(
          itemCount: list.length,
          separatorBuilder: (_, _) => const Divider(height: 1),
          itemBuilder: (ctx, i) {
            final d = list[i];
            return ListTile(
              contentPadding: EdgeInsets.zero,
              leading: const Icon(Icons.directions_bike_outlined),
              title: Text(d.name.isEmpty ? 'Unknown device' : d.name),
              subtitle: Text('Signal ${d.rssi} dBm'),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => onPick(d.peripheral),
            );
          },
        );
      },
    );
  }
}

class _ScanEmpty extends StatelessWidget {
  final bool scanning;
  final bool blocked;
  const _ScanEmpty({required this.scanning, required this.blocked});

  @override
  Widget build(BuildContext context) {
    // BLE banner above already explains what's wrong, so no need to repeat.
    if (blocked) return const SizedBox.shrink();
    final cs = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final (String title, String hint, IconData icon) = scanning
        ? (
            'Looking for bikes nearby',
            'Wake the bike if it has gone to sleep.\n'
                'IC8 and IC4 advertise as “IC Bike”.',
            Icons.bluetooth_searching,
          )
        : (
            'Ready to pair',
            'Tap Find bike to start scanning.',
            Icons.directions_bike_outlined,
          );
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(Insets.lg),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.center,
          children: [
            Icon(icon, size: 48, color: cs.onSurfaceVariant),
            const SizedBox(height: Insets.md),
            Text(title,
                textAlign: TextAlign.center,
                style: text.titleMedium?.copyWith(color: cs.onSurface)),
            const SizedBox(height: Insets.xs),
            Text(hint,
                textAlign: TextAlign.center,
                style: text.bodyMedium?.copyWith(color: cs.onSurfaceVariant)),
          ],
        ),
      ),
    );
  }
}

/// Live data view: hero corrected-power, secondary metrics, and the
/// bike-vs-bridge comparison at the bottom. The hero treatment makes the
/// number you actually care about dominant — the bike's raw value is just
/// reference, not headline.
class _LiveData extends StatelessWidget {
  final ValueListenable<IC8Sample?> sample;
  const _LiveData({super.key, required this.sample});

  @override
  Widget build(BuildContext context) {
    return ValueListenableBuilder<IC8Sample?>(
      valueListenable: sample,
      builder: (context, s, _) {
        final ftms = s?.ftms;
        final pwrBroadcast = ftms?.powerW ?? 0;
        final pwrCorrected = (s?.correctedW ?? 0).round();
        final cad = s?.cadenceRpmCsc ?? ftms?.cadenceRpm ?? 0;
        final r = ftms?.resistance ?? 0;
        final hr = ftms?.heartRate ?? 0;
        final delta = pwrCorrected - pwrBroadcast;
        return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          _HeroTile(value: pwrCorrected, unit: 'W', label: 'Corrected power'),
          const SizedBox(height: Insets.md),
          Row(children: [
            Expanded(child: _MetricTile(
                label: 'Cadence',
                value: cad.toStringAsFixed(0),
                unit: 'rpm')),
            const SizedBox(width: Insets.md),
            Expanded(child: _MetricTile(
                label: 'Resistance',
                value: '$r',
                unit: '')),
            const SizedBox(width: Insets.md),
            Expanded(child: _MetricTile(
                label: 'Heart rate',
                value: '$hr',
                unit: 'bpm')),
          ]),
          const SizedBox(height: Insets.md),
          _DeltaTile(bikeSays: pwrBroadcast, delta: delta),
        ]);
      },
    );
  }
}

class _HeroTile extends StatelessWidget {
  final int value;
  final String unit;
  final String label;
  const _HeroTile({
    required this.value,
    required this.unit,
    required this.label,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    return Semantics(
      container: true,
      label: '$label $value $unit',
      excludeSemantics: true,
      child: Container(
        decoration: BoxDecoration(
          color: cs.primaryContainer,
          borderRadius: BorderRadius.circular(Radii.card),
        ),
        padding: const EdgeInsets.fromLTRB(
            Insets.lg, Insets.md, Insets.lg, Insets.md),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label.toUpperCase(),
              style: text.labelMedium?.copyWith(
                color: cs.onPrimaryContainer.withValues(alpha: 0.75),
                letterSpacing: 1.2,
                fontWeight: FontWeight.w600,
              )),
          const SizedBox(height: Insets.xs),
          Row(
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              AnimatedSwitcher(
                duration: Motion.fast,
                switchInCurve: Motion.curve,
                transitionBuilder: (c, a) =>
                    FadeTransition(opacity: a, child: c),
                child: Text(
                  '$value',
                  key: ValueKey(value),
                  style: text.displayMedium?.copyWith(
                    color: cs.onPrimaryContainer,
                    fontWeight: FontWeight.w700,
                    height: 1.0,
                  ),
                ),
              ),
              const SizedBox(width: Insets.sm),
              Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Text(unit,
                    style: text.titleMedium?.copyWith(
                      color: cs.onPrimaryContainer.withValues(alpha: 0.75),
                      fontWeight: FontWeight.w500,
                    )),
              ),
            ],
          ),
        ]),
      ),
    );
  }
}

class _MetricTile extends StatelessWidget {
  final String label;
  final String value;
  final String unit;
  const _MetricTile({
    required this.label,
    required this.value,
    required this.unit,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    return Semantics(
      container: true,
      label: '$label $value $unit',
      excludeSemantics: true,
      child: Container(
        decoration: BoxDecoration(
          color: cs.surfaceContainerHigh,
          borderRadius: BorderRadius.circular(Radii.tile),
        ),
        padding: const EdgeInsets.symmetric(
            horizontal: Insets.md, vertical: Insets.md),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text(label.toUpperCase(),
              style: text.labelSmall?.copyWith(
                color: cs.onSurfaceVariant,
                letterSpacing: 1.0,
                fontWeight: FontWeight.w600,
              )),
          const SizedBox(height: Insets.xs),
          Row(
            crossAxisAlignment: CrossAxisAlignment.baseline,
            textBaseline: TextBaseline.alphabetic,
            children: [
              Text(value,
                  style: text.headlineSmall?.copyWith(
                    color: cs.onSurface,
                    fontWeight: FontWeight.w600,
                    height: 1.0,
                  )),
              if (unit.isNotEmpty) ...[
                const SizedBox(width: Insets.xs),
                Padding(
                  padding: const EdgeInsets.only(bottom: 3),
                  child: Text(unit,
                      style: text.bodySmall
                          ?.copyWith(color: cs.onSurfaceVariant)),
                ),
              ],
            ],
          ),
        ]),
      ),
    );
  }
}

/// Footer comparison row: the bike's raw broadcast vs. what we re-broadcast.
/// Two columns so the user can sanity-check the correction at a glance.
class _DeltaTile extends StatelessWidget {
  final int bikeSays;
  final int delta;
  const _DeltaTile({required this.bikeSays, required this.delta});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final sign = delta >= 0 ? '+' : '';
    return Container(
      decoration: BoxDecoration(
        color: cs.surfaceContainerLow,
        borderRadius: BorderRadius.circular(Radii.tile),
        border: Border.all(color: cs.outlineVariant, width: 1),
      ),
      padding: const EdgeInsets.symmetric(
          horizontal: Insets.lg, vertical: Insets.md),
      child: Row(children: [
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('BIKE BROADCASTS',
                style: text.labelSmall?.copyWith(
                  color: cs.onSurfaceVariant,
                  letterSpacing: 1.0,
                  fontWeight: FontWeight.w600,
                )),
            const SizedBox(height: Insets.xs),
            Text('$bikeSays W',
                style: text.titleMedium?.copyWith(
                  color: cs.onSurface,
                  fontWeight: FontWeight.w600,
                )),
          ]),
        ),
        Container(
          width: 1,
          height: 32,
          color: cs.outlineVariant,
          margin: const EdgeInsets.symmetric(horizontal: Insets.md),
        ),
        Expanded(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            Text('CORRECTION',
                style: text.labelSmall?.copyWith(
                  color: cs.onSurfaceVariant,
                  letterSpacing: 1.0,
                  fontWeight: FontWeight.w600,
                )),
            const SizedBox(height: Insets.xs),
            Text('$sign$delta W',
                style: text.titleMedium?.copyWith(
                  color: cs.onSurface,
                  fontWeight: FontWeight.w600,
                )),
          ]),
        ),
      ]),
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
    final text = Theme.of(context).textTheme;
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
        child: AnimatedContainer(
          duration: Motion.normal,
          curve: Motion.curve,
          padding: const EdgeInsets.symmetric(
              horizontal: Insets.md, vertical: Insets.sm),
          decoration: BoxDecoration(
            color: bg,
            borderRadius: BorderRadius.circular(Radii.pill),
          ),
          child: Row(mainAxisSize: MainAxisSize.min, children: [
            if (spinning)
              SizedBox(
                width: 12,
                height: 12,
                child: CircularProgressIndicator(
                    strokeWidth: 2, valueColor: AlwaysStoppedAnimation(dot)),
              )
            else
              _Dot(color: dot),
            const SizedBox(width: Insets.sm),
            Flexible(
              child: Text(
                label,
                style: text.labelLarge?.copyWith(
                  color: fg,
                  fontWeight: FontWeight.w600,
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
