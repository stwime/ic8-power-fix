import 'dart:async';

import 'package:flutter/material.dart';

import '../ble/central.dart';
import '../physics/calibration.dart';
import '../prefs.dart';
import 'coastdown.dart';
import 'tokens.dart';

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
      content: Text('Saved: “${widget.prefs.proxyName}”'),
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

  Future<void> _resetPowerScale() async {
    await widget.calibration.setPowerScale(Calibration.defaultPowerScale);
    if (mounted) setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    final cal = widget.calibration;

    return Scaffold(
      appBar: AppBar(
        title: const Text('Settings'),
        actions: [
          IconButton(
            tooltip: 'Reset all to defaults',
            icon: const Icon(Icons.restore),
            onPressed: cal.isAtDefaults ? null : _resetConfirm,
          ),
        ],
      ),
      body: ListView(
        padding: const EdgeInsets.fromLTRB(
            Insets.lg, Insets.md, Insets.lg, Insets.xl),
        children: [
          const _SectionHeader('Power preview'),
          ValueListenableBuilder<IC8Sample?>(
            valueListenable: _lastSample,
            builder: (context, s, _) => _PowerPreviewCard(sample: s),
          ),

          const SizedBox(height: Insets.xl),
          _SectionHeader(
            'Power scale',
            trailing: cal.powerScale == Calibration.defaultPowerScale
                ? null
                : TextButton(
                    onPressed: _resetPowerScale,
                    style: TextButton.styleFrom(
                      padding: const EdgeInsets.symmetric(
                          horizontal: Insets.sm, vertical: Insets.xs),
                      minimumSize: Size.zero,
                      tapTargetSize: MaterialTapTargetSize.shrinkWrap,
                    ),
                    child: const Text('Reset'),
                  ),
          ),
          _SectionHint(
              'Use this if your power numbers feel high or low compared to '
              'another power meter you trust. Scales steady-state and '
              'acceleration response together.'),
          const SizedBox(height: Insets.sm),
          _PowerScaleSlider(
            value: cal.powerScale,
            onChanged: (v) => setState(() => cal.powerScale = v),
            onChangeEnd: (v) async {
              await cal.setPowerScale(v);
              if (mounted) setState(() {});
            },
          ),

          const SizedBox(height: Insets.xl),
          const _SectionHeader('Calibrate to your bike'),
          _SectionHint(
              'Each bike’s flywheel is slightly different. Auto-calibration '
              'measures how yours slows down at different resistance levels '
              'so the power numbers match your bike more accurately. Takes '
              '5–10 minutes.'),
          const SizedBox(height: Insets.md),
          FilledButton.tonalIcon(
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
            icon: const Icon(Icons.science_outlined),
            label: const Text('Auto-calibrate'),
          ),

          const SizedBox(height: Insets.xl),
          _AdvancedSection(
            alphaController: _alphaCtrl,
            betaController: _betaCtrl,
            currentAlpha: cal.alpha,
            currentBeta: cal.beta,
            onSaveAlpha: _saveAlpha,
            onSaveBeta: _saveBeta,
          ),

          const SizedBox(height: Insets.xl),
          const _SectionHeader('Bike name in training apps'),
          _SectionHint(
              'How your bike appears when Zwift, Rouvy, or MyWhoosh look '
              'for a power meter. Takes effect the next time you connect.'),
          const SizedBox(height: Insets.sm),
          TextField(
            controller: _nameCtrl,
            maxLength: AppPrefs.proxyNameMaxLen,
            textInputAction: TextInputAction.done,
            onSubmitted: (_) => _saveProxyName(),
            onTapOutside: (_) => FocusManager.instance.primaryFocus?.unfocus(),
            decoration: const InputDecoration(
              border: OutlineInputBorder(),
              labelText: 'Bike name',
              hintText: AppPrefs.defaultProxyName,
              prefixIcon: Icon(Icons.bluetooth_outlined),
            ),
          ),
        ],
      ),
    );
  }
}

class _PowerPreviewCard extends StatelessWidget {
  final IC8Sample? sample;
  const _PowerPreviewCard({required this.sample});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final pwr = (sample?.correctedW ?? 0).round();
    final hasSignal = sample != null;
    return Container(
      decoration: BoxDecoration(
        color: cs.surfaceContainerHigh,
        borderRadius: BorderRadius.circular(Radii.card),
      ),
      padding: const EdgeInsets.all(Insets.lg),
      child: Row(children: [
        Container(
          width: 48, height: 48,
          decoration: BoxDecoration(
            color: cs.primaryContainer,
            borderRadius: BorderRadius.circular(Radii.tile),
          ),
          child: Icon(Icons.bolt, color: cs.onPrimaryContainer, size: 28),
        ),
        const SizedBox(width: Insets.md),
        Expanded(child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            AnimatedSwitcher(
              duration: Motion.fast,
              transitionBuilder: (c, a) => FadeTransition(opacity: a, child: c),
              child: Text(
                hasSignal ? '$pwr W' : '— W',
                key: ValueKey(hasSignal ? pwr : -1),
                style: text.headlineMedium?.copyWith(
                  color: cs.onSurface,
                  fontWeight: FontWeight.w700,
                ),
              ),
            ),
            const SizedBox(height: Insets.xs),
            Text(
              hasSignal
                  ? 'Live, updates as you adjust the slider'
                  : 'Connect to your bike to see live power',
              style: text.bodySmall?.copyWith(color: cs.onSurfaceVariant),
            ),
          ],
        )),
      ]),
    );
  }
}

class _PowerScaleSlider extends StatelessWidget {
  final double value;
  final ValueChanged<double> onChanged;
  final ValueChanged<double> onChangeEnd;
  const _PowerScaleSlider({
    required this.value,
    required this.onChanged,
    required this.onChangeEnd,
  });

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final clamped = value.clamp(
        Calibration.powerScaleMin, Calibration.powerScaleMax);
    final percent = (clamped * 100).round();
    return Column(children: [
      Row(children: [
        Expanded(child: Slider(
          min: Calibration.powerScaleMin,
          max: Calibration.powerScaleMax,
          // 0.01 step — fine enough to tune against an external power meter
          // without big jumps in absolute output.
          divisions: ((Calibration.powerScaleMax - Calibration.powerScaleMin) * 100).round(),
          value: clamped,
          label: '$percent%',
          onChanged: onChanged,
          onChangeEnd: onChangeEnd,
        )),
        SizedBox(
          width: 56,
          child: Text('$percent%',
              textAlign: TextAlign.right,
              style: text.titleMedium?.copyWith(
                color: cs.onSurface,
                fontWeight: FontWeight.w600,
              )),
        ),
      ]),
      Padding(
        padding: const EdgeInsets.symmetric(horizontal: Insets.md),
        child: Row(mainAxisAlignment: MainAxisAlignment.spaceBetween, children: [
          Text('${(Calibration.powerScaleMin * 100).round()}%',
              style: text.bodySmall?.copyWith(color: cs.onSurfaceVariant)),
          Text('${(Calibration.powerScaleMax * 100).round()}%',
              style: text.bodySmall?.copyWith(color: cs.onSurfaceVariant)),
        ]),
      ),
    ]);
  }
}

class _SectionHeader extends StatelessWidget {
  final String label;
  final Widget? trailing;
  const _SectionHeader(this.label, {this.trailing});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    return Padding(
      padding: const EdgeInsets.only(bottom: Insets.sm),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.center,
        children: [
          Expanded(
            child: Text(label,
                style: text.titleSmall?.copyWith(
                  color: cs.onSurface,
                  fontWeight: FontWeight.w700,
                  letterSpacing: 0.1,
                )),
          ),
          ?trailing,
        ],
      ),
    );
  }
}

class _SectionHint extends StatelessWidget {
  final String text;
  const _SectionHint(this.text);

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = Theme.of(context).textTheme;
    return Text(text,
        style: t.bodySmall?.copyWith(
          color: cs.onSurfaceVariant,
          height: 1.4,
        ));
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
    final cs = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    return Theme(
      // ExpansionTile inherits dividerColor; default to none for a flatter
      // look that fits the rest of the screen's sectioned hint blocks.
      data: Theme.of(context).copyWith(dividerColor: Colors.transparent),
      child: ExpansionTile(
        tilePadding: EdgeInsets.zero,
        childrenPadding: EdgeInsets.zero,
        title: Text('Advanced',
            style: text.titleSmall?.copyWith(
              color: cs.onSurface,
              fontWeight: FontWeight.w700,
              letterSpacing: 0.1,
            )),
        subtitle: Text('Manually edit the resistance curve',
            style: text.bodySmall?.copyWith(color: cs.onSurfaceVariant)),
        children: [
          const SizedBox(height: Insets.sm),
          _SectionHint(
              'These two numbers describe how each resistance level affects '
              'power. Most people should use Auto-calibrate above instead '
              'of editing them by hand.'),
          const SizedBox(height: Insets.md),
          _EditableNumberRow(
            label: 'Brake',
            controller: alphaController,
            defaultValue: Calibration.defaultAlpha,
            currentValue: currentAlpha,
            frac: 4,
            onSave: onSaveAlpha,
          ),
          const SizedBox(height: Insets.md),
          _EditableNumberRow(
            label: 'Friction',
            controller: betaController,
            defaultValue: Calibration.defaultBeta,
            currentValue: currentBeta,
            frac: 4,
            onSave: onSaveBeta,
          ),
        ],
      ),
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
        width: 88,
        child: ExcludeSemantics(child: Text(label)),
      ),
      Expanded(child: Semantics(
        label: label,
        textField: true,
        child: TextField(
          controller: controller,
          keyboardType: const TextInputType.numberWithOptions(decimal: true),
          textInputAction: TextInputAction.done,
          onSubmitted: (_) => onSave(),
          onTapOutside: (_) => FocusManager.instance.primaryFocus?.unfocus(),
          decoration: InputDecoration(
            isDense: true,
            contentPadding: const EdgeInsets.symmetric(
                horizontal: Insets.md, vertical: Insets.md),
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
