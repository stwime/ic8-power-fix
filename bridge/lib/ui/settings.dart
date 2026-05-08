import 'dart:async';

import 'package:flutter/material.dart';

import '../ble/central.dart';
import '../physics/calibration.dart';
import '../prefs.dart';
import 'coastdown.dart';

/// Settings screen for the calibration constants. Live preview of corrected
/// power means a slider move retunes the home tile and the bridged peripheral
/// in real time — useful for nudging I_crank against an external power meter.
class SettingsPage extends StatefulWidget {
  final Calibration calibration;
  final AppPrefs prefs;
  final IC8Central central;
  const SettingsPage({
    super.key,
    required this.calibration,
    required this.prefs,
    required this.central,
  });

  @override
  State<SettingsPage> createState() => _SettingsPageState();
}

class _SettingsPageState extends State<SettingsPage> {
  StreamSubscription? _sampleSub;
  IC8Sample? _last;
  late final TextEditingController _nameCtrl;
  late final TextEditingController _alphaCtrl;
  late final TextEditingController _betaCtrl;

  @override
  void initState() {
    super.initState();
    final cal = widget.calibration;
    _nameCtrl = TextEditingController(text: widget.prefs.proxyName);
    _alphaCtrl = TextEditingController(text: cal.alpha.toStringAsFixed(4));
    _betaCtrl = TextEditingController(text: cal.beta.toStringAsFixed(4));
    _sampleSub = widget.central.samples.listen((s) {
      if (mounted) setState(() => _last = s);
    });
  }

  @override
  void dispose() {
    _sampleSub?.cancel();
    _nameCtrl.dispose();
    _alphaCtrl.dispose();
    _betaCtrl.dispose();
    super.dispose();
  }

  Future<void> _resetConfirm() async {
    final messenger = ScaffoldMessenger.of(context);
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Reset calibration?'),
        content: const Text(
            'This undoes any calibration changes you have made and goes back '
            'to the values that ship with the app.'),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false),
              child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true),
              child: const Text('Reset')),
        ],
      ),
    );
    if (ok != true) return;
    await widget.calibration.resetToDefaults();
    if (!mounted) return;
    setState(() {
      _alphaCtrl.text = widget.calibration.alpha.toStringAsFixed(4);
      _betaCtrl.text = widget.calibration.beta.toStringAsFixed(4);
    });
    messenger.showSnackBar(const SnackBar(content: Text('Reset to defaults')));
  }

  Future<void> _saveProxyName() async {
    final messenger = ScaffoldMessenger.of(context);
    await widget.prefs.setProxyName(_nameCtrl.text);
    if (!mounted) return;
    setState(() => _nameCtrl.text = widget.prefs.proxyName);
    messenger.showSnackBar(SnackBar(
      content: Text('Saved: "${widget.prefs.proxyName}"'),
    ));
  }

  Future<void> _saveAlpha() async {
    final messenger = ScaffoldMessenger.of(context);
    final v = double.tryParse(_alphaCtrl.text);
    if (v == null) {
      messenger.showSnackBar(const SnackBar(
          content: Text('That is not a valid number')));
      return;
    }
    await widget.calibration.setAlpha(v);
    if (!mounted) return;
    setState(() {});
    messenger.showSnackBar(const SnackBar(content: Text('Saved')));
  }

  Future<void> _saveBeta() async {
    final messenger = ScaffoldMessenger.of(context);
    final v = double.tryParse(_betaCtrl.text);
    if (v == null) {
      messenger.showSnackBar(const SnackBar(
          content: Text('That is not a valid number')));
      return;
    }
    await widget.calibration.setBeta(v);
    if (!mounted) return;
    setState(() {});
    messenger.showSnackBar(const SnackBar(content: Text('Saved')));
  }

  @override
  Widget build(BuildContext context) {
    final cal = widget.calibration;
    final pwr = (_last?.correctedW ?? 0).round();

    return Scaffold(
      appBar: AppBar(
        title: const Text('Settings'),
        actions: [
          IconButton(
            tooltip: 'Reset to defaults',
            icon: const Icon(Icons.restore),
            onPressed: cal.isAtDefaults ? null : _resetConfirm,
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.all(16),
        children: [
          _section('Power preview'),
          Card(child: Padding(
            padding: const EdgeInsets.all(16),
            child: Row(children: [
              const Icon(Icons.bolt, size: 32),
              const SizedBox(width: 12),
              Expanded(child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text('$pwr W',
                      style: Theme.of(context).textTheme.headlineMedium),
                  Text(_last == null
                      ? 'Connect to your bike to see your live power here'
                      : 'Live power — updates as you adjust the slider',
                      style: Theme.of(context).textTheme.bodySmall),
                ],
              )),
            ]),
          )),

          const SizedBox(height: 16),
          _section('Power scale'),
          Text('Use this if your power numbers feel too high or too low '
              'compared to another power meter you trust. Slide right to '
              'increase your power, left to decrease. Scales steady-state '
              'and acceleration response by the same factor.',
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 8),
          Row(children: [
            Expanded(child: Slider(
              min: Calibration.powerScaleMin,
              max: Calibration.powerScaleMax,
              // 0.01 step — fine enough to tune against an external
              // power meter without big jumps in absolute output.
              divisions: ((Calibration.powerScaleMax - Calibration.powerScaleMin) * 100).round(),
              value: cal.powerScale.clamp(
                  Calibration.powerScaleMin, Calibration.powerScaleMax),
              label: '${(cal.powerScale * 100).round()}%',
              onChanged: (v) {
                setState(() => cal.powerScale = v);
              },
              onChangeEnd: (v) async {
                await cal.setPowerScale(v);
                if (mounted) setState(() {});
              },
            )),
            SizedBox(
              width: 64,
              child: Text('${(cal.powerScale * 100).round()}%',
                  textAlign: TextAlign.right,
                  style: Theme.of(context).textTheme.titleMedium),
            ),
          ]),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
              Text('Lower', style: Theme.of(context).textTheme.bodySmall),
              Text('Default', style: Theme.of(context).textTheme.bodySmall),
              Text('Higher', style: Theme.of(context).textTheme.bodySmall),
            ]),
          ),

          const SizedBox(height: 24),
          _section('Calibrate to your bike'),
          Text('Each bike is slightly different. The auto-calibration takes '
              'a few minutes and measures how your bike\'s flywheel slows '
              'down at different resistance levels. This makes the power '
              'numbers match your bike more accurately.',
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 12),
          FilledButton.icon(
            onPressed: () async {
              await Navigator.of(context).push(MaterialPageRoute(
                builder: (_) => CoastdownPage(
                  calibration: cal,
                  central: widget.central,
                ),
              ));
              if (mounted) {
                setState(() {
                  _alphaCtrl.text = widget.calibration.alpha.toStringAsFixed(4);
                  _betaCtrl.text = widget.calibration.beta.toStringAsFixed(4);
                });
              }
            },
            icon: const Icon(Icons.science),
            label: const Text('Auto-calibrate'),
          ),

          const SizedBox(height: 24),
          _advancedSection(cal),

          const SizedBox(height: 24),
          _section('Bike name in training apps'),
          Text('How your bike appears when Zwift, Rouvy, or MyWhoosh look for '
              'a power meter. The change takes effect the next time you '
              'connect the bike.',
              style: Theme.of(context).textTheme.bodySmall),
          const SizedBox(height: 8),
          TextField(
            controller: _nameCtrl,
            maxLength: AppPrefs.proxyNameMaxLen,
            decoration: InputDecoration(
              border: const OutlineInputBorder(),
              hintText: AppPrefs.defaultProxyName,
              suffixIcon: IconButton(
                tooltip: 'Save',
                icon: const Icon(Icons.save),
                onPressed: _saveProxyName,
              ),
            ),
          ),
        ],
      ),
    );
  }

  Widget _advancedSection(Calibration cal) {
    return ExpansionTile(
      tilePadding: EdgeInsets.zero,
      childrenPadding: EdgeInsets.zero,
      title: Text('Advanced',
          style: Theme.of(context).textTheme.titleMedium?.copyWith(
                fontWeight: FontWeight.bold,
              )),
      subtitle: Text('Manually edit the resistance curve',
          style: Theme.of(context).textTheme.bodySmall),
      children: [
        const SizedBox(height: 8),
        Text(
            'These two numbers describe how each resistance level affects '
            'power. Most people should use Auto-calibrate above instead of '
            'editing them by hand.',
            style: Theme.of(context).textTheme.bodySmall),
        const SizedBox(height: 12),
        _editableNumberRow(
          label: 'Brake',
          controller: _alphaCtrl,
          defaultValue: Calibration.defaultAlpha,
          currentValue: cal.alpha,
          frac: 4,
          onSave: _saveAlpha,
        ),
        const SizedBox(height: 8),
        _editableNumberRow(
          label: 'Friction',
          controller: _betaCtrl,
          defaultValue: Calibration.defaultBeta,
          currentValue: cal.beta,
          frac: 4,
          onSave: _saveBeta,
        ),
      ],
    );
  }

  Widget _section(String label) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Text(label,
            style: Theme.of(context).textTheme.titleMedium?.copyWith(
                  fontWeight: FontWeight.bold,
                )),
      );

  Widget _editableNumberRow({
    required String label,
    required TextEditingController controller,
    required double defaultValue,
    required double currentValue,
    required int frac,
    required Future<void> Function() onSave,
  }) {
    final isDefault = currentValue == defaultValue;
    return Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
      SizedBox(
        width: 96,
        child: Text(label,
            style: const TextStyle(fontFamily: 'monospace')),
      ),
      Expanded(child: TextField(
        controller: controller,
        keyboardType: const TextInputType.numberWithOptions(decimal: true),
        style: const TextStyle(fontFamily: 'monospace'),
        decoration: InputDecoration(
          isDense: true,
          contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 10),
          border: const OutlineInputBorder(),
          helperText: isDefault
              ? 'default'
              : 'default ${defaultValue.toStringAsFixed(frac)}',
          suffixIcon: IconButton(
            tooltip: 'Save',
            icon: const Icon(Icons.save),
            onPressed: onSave,
          ),
        ),
      )),
    ]);
  }
}
