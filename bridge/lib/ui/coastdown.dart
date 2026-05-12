import 'dart:async';

import 'package:flutter/material.dart';

import '../ble/central.dart';
import '../physics/calibration.dart';
import '../physics/coastdown.dart';
import 'tokens.dart';

enum _BannerTone { idle, info, success, warning, error }
enum _Quality { excellent, good, fair }

/// Coastdown calibration screen. Listens to the central's sample stream, runs
/// a streaming detector, and accumulates clean (R, λ) points. Once ≥3 distinct
/// R values are captured, the user can fit a·R + b and apply the result.
class CoastdownPage extends StatefulWidget {
  final Calibration calibration;
  final IC8Central central;
  const CoastdownPage({super.key, required this.calibration, required this.central});

  @override
  State<CoastdownPage> createState() => _CoastdownPageState();
}

class _CoastdownPageState extends State<CoastdownPage> {
  late final CoastdownDetector _detector;
  StreamSubscription? _sampleSub;
  IC8Sample? _last;
  bool _seenAnyCsc = false;
  final ValueNotifier<IC8Sample?> _lastSample = ValueNotifier(null);

  final List<CoastdownPoint> _points = [];

  @override
  void initState() {
    super.initState();
    _detector = CoastdownDetector((p) {
      if (mounted) setState(() => _points.add(p));
    });
    _sampleSub = widget.central.samples.listen((s) {
      _last = s;
      final csc = s.cadenceRpmCsc;
      if (csc != null) _seenAnyCsc = true;
      _detector.push(CoastdownSample(
        timestampS: s.tS,
        resistance: s.ftms.resistance ?? 0,
        cadenceRpmCsc: csc ?? 0.0,
        crankRevs: s.crankRevs,
        crankEventTimeS: s.crankEventTimeS,
      ));
      _lastSample.value = s;
    });
  }

  @override
  void dispose() {
    _sampleSub?.cancel();
    _lastSample.dispose();
    super.dispose();
  }

  Set<int> get _distinctR => _points.map((p) => p.resistance).toSet();
  bool get _canFit => _distinctR.length >= 3;

  Future<void> _fitAndPreview() async {
    final messenger = ScaffoldMessenger.of(context);
    final navigator = Navigator.of(context);
    final fit = fitBrake(_points);
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => _FitPreviewDialog(fit: fit),
    );
    if (ok != true) return;
    await widget.calibration.setBeta(fit.beta);
    if (!mounted) return;
    messenger.showSnackBar(const SnackBar(
      content: Text('Calibration saved'),
    ));
    navigator.pop();
  }

  void _clearPoints() {
    setState(() {
      _detector.discard();
      _points.clear();
    });
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: const Text('Auto-calibrate'),
        actions: [
          if (_points.isNotEmpty)
            IconButton(
              tooltip: 'Clear all measurements',
              icon: const Icon(Icons.delete_sweep_outlined),
              onPressed: _clearPoints,
            ),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.fromLTRB(
            Insets.lg, Insets.md, Insets.lg, Insets.lg),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          const _Instructions(),
          const SizedBox(height: Insets.md),
          ValueListenableBuilder<IC8Sample?>(
            valueListenable: _lastSample,
            builder: (ctx, _, child) => _liveStatus(ctx),
          ),
          const SizedBox(height: Insets.md),
          Expanded(
            child: _PointsTable(
              points: _points,
              onRemove: (i) => setState(() => _points.removeAt(i)),
            ),
          ),
          const SizedBox(height: Insets.md),
          Row(children: [
            Expanded(child: OutlinedButton.icon(
              onPressed: _detector.currentRunLength > 0
                  ? () => setState(_detector.discard)
                  : null,
              icon: const Icon(Icons.cancel_outlined),
              label: const Text('Cancel measurement'),
              style: OutlinedButton.styleFrom(
                padding: const EdgeInsets.symmetric(vertical: Insets.md),
              ),
            )),
            const SizedBox(width: Insets.sm),
            Expanded(child: FilledButton.icon(
              onPressed: _canFit ? _fitAndPreview : null,
              icon: const Icon(Icons.check),
              label: Text(_canFit
                  ? 'Save calibration'
                  : 'Save (${_distinctR.length}/3)'),
              style: FilledButton.styleFrom(
                padding: const EdgeInsets.symmetric(vertical: Insets.md),
              ),
            )),
          ]),
        ]),
      ),
    );
  }

  Widget _liveStatus(BuildContext context) {
    final connected = widget.central.state == BridgeConnState.connected;
    if (!connected) {
      return const _StatusBanner(
        tone: _BannerTone.warning,
        text: 'Not connected to your bike. Go back to the home screen and '
            'tap Find bike first.',
      );
    }
    if (!_seenAnyCsc && _last != null) {
      return const _StatusBanner(
        tone: _BannerTone.error,
        text: 'Your bike is not reporting cadence in real-time, so '
            'calibration cannot run. Make sure the bike is on and the '
            'pedals are turning.',
      );
    }
    final running = _detector.currentRunLength;
    final cad = _last?.cadenceRpmCsc ?? _last?.ftms.cadenceRpm ?? 0;
    final r = _last?.ftms.resistance ?? 0;

    if (running > 0) {
      return _StatusBanner(
        tone: _BannerTone.success,
        text: 'Measuring at resistance ${_detector.currentRunR}. Wait for '
            'the pedals to stop completely. Keep your hands off the dial. '
            'Cadence: '
            '${_detector.currentRunCadence?.toStringAsFixed(0)} rpm.',
      );
    }
    if (cad >= 70) {
      return _StatusBanner(
        tone: _BannerTone.info,
        text: 'Lift both feet off the pedals at the same time to start a '
            'measurement. Cadence: ${cad.toStringAsFixed(0)} rpm at '
            'resistance $r.',
      );
    }
    return _StatusBanner(
      tone: _BannerTone.idle,
      text: 'Pedal up to at least 70 rpm to begin. '
          'Cadence: ${cad.toStringAsFixed(0)} rpm at resistance $r.',
    );
  }
}

class _Instructions extends StatelessWidget {
  const _Instructions();

  static const _steps = [
    'Set the resistance dial to a low number (try 5).',
    'Pedal until your cadence is at least 70 rpm.',
    'Quickly lift both feet off the pedals at the same time so they spin '
        'freely. A slow or one-foot-at-a-time release adds drag and ruins '
        'the measurement. Keep your hands off the dial.',
    'Wait for the pedals to stop spinning completely. The measurement is '
        'not finished until they do.',
    'Change the resistance and repeat — at least 3 different resistance '
        'levels in total. More levels (and more coastdowns per level) give '
        'a tighter fit.',
  ];

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    return Container(
      decoration: BoxDecoration(
        color: cs.secondaryContainer,
        borderRadius: BorderRadius.circular(Radii.card),
      ),
      padding: const EdgeInsets.all(Insets.lg),
      child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Icon(Icons.help_outline, color: cs.onSecondaryContainer, size: 18),
          const SizedBox(width: Insets.sm),
          Text('How it works',
              style: text.titleSmall?.copyWith(
                color: cs.onSecondaryContainer,
                fontWeight: FontWeight.w700,
              )),
        ]),
        const SizedBox(height: Insets.sm),
        Text(
            'You will pedal up and stop several times at different '
            'resistance levels. The app measures how the flywheel slows '
            'down each time. Usually 5–10 minutes.',
            style: text.bodyMedium?.copyWith(
              color: cs.onSecondaryContainer,
              height: 1.35,
            )),
        const SizedBox(height: Insets.md),
        for (int i = 0; i < _steps.length; i++) ...[
          if (i > 0) const SizedBox(height: Insets.sm),
          _NumberedStep(
            number: i + 1,
            text: _steps[i],
            color: cs.onSecondaryContainer,
          ),
        ],
      ]),
    );
  }
}

class _NumberedStep extends StatelessWidget {
  final int number;
  final String text;
  final Color color;
  const _NumberedStep({
    required this.number,
    required this.text,
    required this.color,
  });

  @override
  Widget build(BuildContext context) {
    final body = Theme.of(context).textTheme.bodyMedium;
    return Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
      SizedBox(
        width: 22,
        child: Text('$number.',
            style: body?.copyWith(
              color: color,
              fontWeight: FontWeight.w700,
              height: 1.35,
            )),
      ),
      Expanded(child: Text(text,
          style: body?.copyWith(color: color, height: 1.35))),
    ]);
  }
}

class _StatusBanner extends StatelessWidget {
  final _BannerTone tone;
  final String text;
  const _StatusBanner({required this.tone, required this.text});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final t = Theme.of(context).textTheme;
    final Color bg;
    final Color fg;
    final IconData icon;
    switch (tone) {
      case _BannerTone.idle:
        bg = cs.surfaceContainerLow;
        fg = cs.onSurfaceVariant;
        icon = Icons.directions_bike_outlined;
      case _BannerTone.info:
        bg = cs.secondaryContainer;
        fg = cs.onSecondaryContainer;
        icon = Icons.touch_app_outlined;
      case _BannerTone.success:
        bg = cs.primaryContainer;
        fg = cs.onPrimaryContainer;
        icon = Icons.timer_outlined;
      case _BannerTone.warning:
        bg = cs.tertiaryContainer;
        fg = cs.onTertiaryContainer;
        icon = Icons.warning_amber_rounded;
      case _BannerTone.error:
        bg = cs.errorContainer;
        fg = cs.onErrorContainer;
        icon = Icons.error_outline;
    }
    return Semantics(
      liveRegion: true,
      container: true,
      child: Container(
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(Radii.tile),
        ),
        padding: const EdgeInsets.all(Insets.md),
        child: Row(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Icon(icon, color: fg, size: 20),
          const SizedBox(width: Insets.md),
          Expanded(child: Text(text,
              style: t.bodyMedium?.copyWith(color: fg, height: 1.35))),
        ]),
      ),
    );
  }
}

class _PointsTable extends StatelessWidget {
  final List<CoastdownPoint> points;
  final void Function(int index) onRemove;
  const _PointsTable({required this.points, required this.onRemove});

  // Per-coastdown fit quality from R² of ln(ω) vs. t. Excellent ≥ 0.99 reflects
  // the empirical noise floor of a clean lift-off; below 0.97 usually means a
  // foot-drag or dial-touch contaminated the run.
  static const double _r2Excellent = 0.99;
  static const double _r2Good = 0.97;

  static _Quality _qualityOf(double r2) {
    if (r2 >= _r2Excellent) return _Quality.excellent;
    if (r2 >= _r2Good) return _Quality.good;
    return _Quality.fair;
  }

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    if (points.isEmpty) {
      return Center(
        child: Padding(
          padding: const EdgeInsets.all(Insets.lg),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Icon(Icons.timeline_outlined,
                  size: 40, color: cs.onSurfaceVariant),
              const SizedBox(height: Insets.md),
              Text('No measurements yet',
                  style: text.titleMedium?.copyWith(color: cs.onSurface)),
              const SizedBox(height: Insets.xs),
              Text('Follow the steps above to capture your first coastdown.',
                  textAlign: TextAlign.center,
                  style: text.bodySmall?.copyWith(color: cs.onSurfaceVariant)),
            ],
          ),
        ),
      );
    }
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Semantics(
        header: true,
        container: true,
        child: Padding(
          padding: const EdgeInsets.symmetric(
              horizontal: Insets.sm, vertical: Insets.xs),
          child: Row(children: [
            SizedBox(width: 96,
                child: Text('RESISTANCE',
                    style: text.labelSmall?.copyWith(
                      color: cs.onSurfaceVariant,
                      letterSpacing: 1.0,
                      fontWeight: FontWeight.w600,
                    ))),
            Expanded(
                child: Text('QUALITY',
                    style: text.labelSmall?.copyWith(
                      color: cs.onSurfaceVariant,
                      letterSpacing: 1.0,
                      fontWeight: FontWeight.w600,
                    ))),
            const SizedBox(width: 48),
          ]),
        ),
      ),
      Divider(height: 1, color: cs.outlineVariant),
      Expanded(child: ListView.separated(
        itemCount: points.length,
        separatorBuilder: (_, _) => Divider(height: 1, color: cs.outlineVariant),
        itemBuilder: (ctx, i) {
          final p = points[i];
          return Padding(
            padding: const EdgeInsets.symmetric(
                horizontal: Insets.sm, vertical: Insets.xs),
            child: Row(children: [
              SizedBox(
                width: 96,
                child: Text('${p.resistance}',
                    style: text.titleMedium?.copyWith(
                      color: cs.onSurface,
                      fontWeight: FontWeight.w600,
                    )),
              ),
              Expanded(child: _QualityChip(quality: _qualityOf(p.r2))),
              SizedBox(
                width: 48,
                height: 48,
                child: IconButton(
                  icon: const Icon(Icons.close, size: 18),
                  tooltip: 'Remove',
                  onPressed: () => onRemove(i),
                ),
              ),
            ]),
          );
        },
      )),
    ]);
  }
}

class _QualityChip extends StatelessWidget {
  final _Quality quality;
  const _QualityChip({required this.quality});

  @override
  Widget build(BuildContext context) {
    final cs = Theme.of(context).colorScheme;
    final text = Theme.of(context).textTheme;
    final (Color bg, Color fg, String label) = switch (quality) {
      _Quality.excellent =>
        (cs.primaryContainer, cs.onPrimaryContainer, 'Excellent'),
      _Quality.good =>
        (cs.secondaryContainer, cs.onSecondaryContainer, 'Good'),
      _Quality.fair =>
        (cs.tertiaryContainer, cs.onTertiaryContainer, 'Fair'),
    };
    return Align(
      alignment: Alignment.centerLeft,
      child: Container(
        padding: const EdgeInsets.symmetric(
            horizontal: Insets.sm, vertical: 2),
        decoration: BoxDecoration(
          color: bg,
          borderRadius: BorderRadius.circular(Radii.pill),
        ),
        child: Text(label,
            style: text.labelSmall?.copyWith(
              color: fg,
              fontWeight: FontWeight.w600,
            )),
      ),
    );
  }
}

class _FitPreviewDialog extends StatelessWidget {
  final BrakeFit fit;
  const _FitPreviewDialog({required this.fit});

  @override
  Widget build(BuildContext context) {
    final text = Theme.of(context).textTheme;
    final maxResid = fit.residuals
        .map((r) => (r.measured - r.predicted).abs())
        .reduce((a, b) => a > b ? a : b);
    return AlertDialog(
      title: const Text('Save this calibration?'),
      content: Column(crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min, children: [
        Text(
            'You captured ${fit.residuals.length} measurements across '
            '${fit.residuals.map((r) => r.r).toSet().length} resistance '
            'levels.',
            style: text.bodyMedium),
        const SizedBox(height: Insets.md),
        Text('Fit quality: ${_overall(fit.rms, maxResid)}',
            style: text.bodyMedium),
        const SizedBox(height: Insets.sm),
        Text('You can run the calibration again anytime to improve it.',
            style: text.bodySmall),
      ]),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel')),
        FilledButton(onPressed: () => Navigator.pop(context, true),
            child: const Text('Save')),
      ],
    );
  }

  // Cross-run fit quality of λ vs. R. Thresholds are in λ-units; "Excellent"
  // corresponds to ~±2% power error at typical resistances.
  static const double _rmsExcellent = 0.01;
  static const double _maxResidExcellent = 0.02;
  static const double _rmsGood = 0.02;
  static const double _maxResidGood = 0.04;

  static String _overall(double rms, double maxResid) {
    if (rms < _rmsExcellent && maxResid < _maxResidExcellent) return 'Excellent';
    if (rms < _rmsGood && maxResid < _maxResidGood) return 'Good';
    return 'Fair — consider redoing with steadier coastdowns';
  }
}
