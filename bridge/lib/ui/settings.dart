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
  final ValueNotifier<IC8Sample?> _lastSample = ValueNotifier(null);
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
      _lastSample.value = s;
    });
  }

  @override
  void dispose() {
    _sampleSub?.cancel();
    _lastSample.dispose();
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
    final v = double.tryParse(_alphaCtrl.text.replaceAll(',', '.'));
    if (v == null) {
      messenger.showSnackBar(const SnackBar(
          content: Text('That is not a valid number')));
      return;
    }
    await widget.calibration.setAlpha(v);
    if (!mounted) return;
    // Calibration is mutable; rebuild so isAtDefaults (and the reset action's
    // enable state) reflects the new value.
    setState(() {});
    messenger.showSnackBar(const SnackBar(content: Text('Saved')));
  }

  Future<void> _saveBeta() async {
    final messenger = ScaffoldMessenger.of(context);
    final v = double.tryParse(_betaCtrl.text.replaceAll(',', '.'));
    if (v == null) {
      messenger.showSnackBar(const SnackBar(
          content: Text('That is not a valid number')));
      return;
    }
    await widget.calibration.setBeta(v);
    if (!mounted) return;
    // See _saveAlpha — rebuild for isAtDefaults.
    setState(() {});
    messenger.showSnackBar(const SnackBar(content: Text('Saved')));
  }

  @override
  Widget build(BuildContext context) {
    final cal = widget.calibration;
    final textTheme = Theme.of(context).textTheme;

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
          const _Section('Power preview'),
          ValueListenableBuilder<IC8Sample?>(
            valueListenable: _lastSample,
            builder: (context, s, _) {
              final pwr = (s?.correctedW ?? 0).round();
              return Card(child: Padding(
                padding: const EdgeInsets.all(16),
                child: Row(children: [
                  const Icon(Icons.bolt, size: 32),
                  const SizedBox(width: 12),
                  Expanded(child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text('$pwr W', style: textTheme.headlineMedium),
                      Text(s == null
                          ? 'Connect to your bike to see your live power here'
                          : 'Live power, updates as you adjust the slider',
                          style: textTheme.bodySmall),
                    ],
                  )),
                ]),
              ));
            },
          ),

          const SizedBox(height: 16),
          const _Section('Power scale'),
          Text('Use this if your power numbers feel too high or too low '
              'compared to another power meter you trust. Slide right to '
              'increase your power, left to decrease. Scales steady-state '
              'and acceleration response by the same factor.',
              style: textTheme.bodySmall),
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
                // In-memory only — onChangeEnd persists. Cheap rebuilds drive
                // the live-preview tile and the % label below.
                setState(() => cal.powerScale = v);
              },
              onChangeEnd: (v) async {
                await cal.setPowerScale(v);
                // Calibration is mutable; rebuild so isAtDefaults (and the
                // reset-action enable state) reflects the persisted value.
                if (mounted) setState(() {});
              },
            )),
            SizedBox(
              width: 64,
              child: Text('${(cal.powerScale * 100).round()}%',
                  textAlign: TextAlign.right,
                  style: textTheme.titleMedium),
            ),
          ]),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 16),
            child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
              Text('Lower', style: textTheme.bodySmall),
              Text('Default', style: textTheme.bodySmall),
              Text('Higher', style: textTheme.bodySmall),
            ]),
          ),

          const SizedBox(height: 24),
          const _Section('Calibrate to your bike'),
          Text('Each bike is slightly different. The auto-calibration takes '
              'a few minutes and measures how your bike\'s flywheel slows '
              'down at different resistance levels. This makes the power '
              'numbers match your bike more accurately.',
              style: textTheme.bodySmall),
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
          _AdvancedSection(
            alphaController: _alphaCtrl,
            betaController: _betaCtrl,
            currentAlpha: cal.alpha,
            currentBeta: cal.beta,
            onSaveAlpha: _saveAlpha,
            onSaveBeta: _saveBeta,
          ),

          const SizedBox(height: 24),
          const _Section('Bike name in training apps'),
          Text('How your bike appears when Zwift, Rouvy, or MyWhoosh look for '
              'a power meter. The change takes effect the next time you '
              'connect the bike.',
              style: textTheme.bodySmall),
          const SizedBox(height: 8),
          TextField(
            controller: _nameCtrl,
            maxLength: AppPrefs.proxyNameMaxLen,
            onSubmitted: (_) => _saveProxyName(),
            onTapOutside: (_) => FocusManager.instance.primaryFocus?.unfocus(),
            decoration: const InputDecoration(
              border: OutlineInputBorder(),
              labelText: 'Bike name',
              hintText: AppPrefs.defaultProxyName,
            ),
          ),
        ],
      ),
    );
  }
}

class _Section extends StatelessWidget {
  final String label;
  const _Section(this.label);

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Text(label,
          style: Theme.of(context).textTheme.titleMedium?.copyWith(
                fontWeight: FontWeight.bold,
              )),
    );
  }
}

class _AdvancedSection extends StatelessWidget {
  final TextEditingController alphaController;
  final TextEditingController betaController;
  final double currentAlpha;
  final double currentBeta;
  final Future<void> Function() onSaveAlpha;
  final Future<void> Function() onSaveBeta;

  const _AdvancedSection({
    required this.alphaController,
    required this.betaController,
    required this.currentAlpha,
    required this.currentBeta,
    required this.onSaveAlpha,
    required this.onSaveBeta,
  });

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    return ExpansionTile(
      tilePadding: EdgeInsets.zero,
      childrenPadding: EdgeInsets.zero,
      title: Text('Advanced',
          style: textTheme.titleMedium?.copyWith(
                fontWeight: FontWeight.bold,
              )),
      subtitle: Text('Manually edit the resistance curve',
          style: textTheme.bodySmall),
      children: [
        const SizedBox(height: 8),
        Text(
            'These two numbers describe how each resistance level affects '
            'power. Most people should use Auto-calibrate above instead of '
            'editing them by hand.',
            style: textTheme.bodySmall),
        const SizedBox(height: 12),
        _EditableNumberRow(
          label: 'Brake',
          controller: alphaController,
          defaultValue: Calibration.defaultAlpha,
          currentValue: currentAlpha,
          frac: 4,
          onSave: onSaveAlpha,
        ),
        const SizedBox(height: 8),
        _EditableNumberRow(
          label: 'Friction',
          controller: betaController,
          defaultValue: Calibration.defaultBeta,
          currentValue: currentBeta,
          frac: 4,
          onSave: onSaveBeta,
        ),
      ],
    );
  }
}

class _EditableNumberRow extends StatelessWidget {
  final String label;
  final TextEditingController controller;
  final double defaultValue;
  final double currentValue;
  final int frac;
  final Future<void> Function() onSave;

  const _EditableNumberRow({
    required this.label,
    required this.controller,
    required this.defaultValue,
    required this.currentValue,
    required this.frac,
    required this.onSave,
  });

  @override
  Widget build(BuildContext context) {
    final isDefault = currentValue == defaultValue;
    return Row(crossAxisAlignment: CrossAxisAlignment.center, children: [
      SizedBox(
        width: 96,
        child: ExcludeSemantics(child: Text(label)),
      ),
      Expanded(child: Semantics(
        label: label,
        textField: true,
        child: TextField(
          controller: controller,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          onSubmitted: (_) => onSave(),
          onTapOutside: (_) => FocusManager.instance.primaryFocus?.unfocus(),
          decoration: InputDecoration(
            isDense: true,
            contentPadding: const EdgeInsets.symmetric(horizontal: 10, vertical: 10),
            border: const OutlineInputBorder(),
            helperText: isDefault
                ? 'default'
                : 'default ${defaultValue.toStringAsFixed(frac)}',
          ),
        ),
      )),
    ]);
  }
}
