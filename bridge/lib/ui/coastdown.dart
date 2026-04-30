import 'dart:async';

import 'package:flutter/material.dart';

import '../ble/central.dart';
import '../physics/calibration.dart';
import '../physics/coastdown.dart';

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
      ));
      if (mounted) setState(() {});
    });
  }

  @override
  void dispose() {
    _sampleSub?.cancel();
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
    await widget.calibration.setBrakeFit(
      aBrake: fit.aBrake,
      bFriction: fit.bFriction,
    );
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
              icon: const Icon(Icons.delete_sweep),
              onPressed: _clearPoints,
            ),
        ],
      ),
      body: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          _instructions(context),
          const SizedBox(height: 12),
          _liveStatus(context),
          const SizedBox(height: 12),
          Expanded(child: _pointsTable(context)),
          const SizedBox(height: 8),
          Row(children: [
            Expanded(child: OutlinedButton.icon(
              onPressed: _detector.currentRunLength > 0
                  ? () => setState(_detector.discard)
                  : null,
              icon: const Icon(Icons.cancel),
              label: const Text('Cancel measurement'),
            )),
            const SizedBox(width: 8),
            Expanded(child: FilledButton.icon(
              onPressed: _canFit ? _fitAndPreview : null,
              icon: const Icon(Icons.save),
              label: Text(_canFit
                  ? 'Save calibration'
                  : 'Save (${_distinctR.length}/3)'),
            )),
          ]),
        ]),
      ),
    );
  }

  Widget _instructions(BuildContext context) {
    return Card(
      color: Colors.blue.shade50,
      child: Padding(
        padding: const EdgeInsets.all(12),
        child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
          Text('How it works',
              style: Theme.of(context).textTheme.titleSmall),
          const SizedBox(height: 4),
          const Text(
              'You will pedal up and then stop, several times at different '
              'resistance levels. The app measures how the flywheel slows '
              'down each time. This usually takes 5–10 minutes.\n\n'
              '1. Set the resistance dial to a low number (try 5).\n'
              '2. Pedal until your cadence is at least 70 rpm.\n'
              '3. Quickly lift both feet off the pedals at the same time so '
              'they spin freely — a slow or one-foot-at-a-time release adds '
              'drag and ruins the measurement. Keep your hands off the dial.\n'
              '4. Wait for the pedals to stop spinning completely — the '
              'measurement is not finished until they do.\n'
              '5. Change the resistance and repeat — at least 3 different '
              'resistance levels in total. More levels (and more coastdowns '
              'per level) give a tighter fit.'),
        ]),
      ),
    );
  }

  Widget _liveStatus(BuildContext context) {
    final connected = widget.central.state == BridgeConnState.connected;
    if (!connected) {
      return _statusBanner(Colors.orange.shade100,
          'Not connected to your bike. Go back to the home screen and '
          'tap Find bike first.');
    }
    if (!_seenAnyCsc && _last != null) {
      return _statusBanner(Colors.red.shade100,
          'Your bike is not reporting cadence in real-time, so calibration '
          'cannot run. Make sure the bike is on and the pedals are turning.');
    }
    final running = _detector.currentRunLength;
    final cad = _last?.cadenceRpmCsc ?? _last?.ftms.cadenceRpm ?? 0;
    final r = _last?.ftms.resistance ?? 0;

    if (running > 0) {
      return _statusBanner(Colors.green.shade100,
          'Measuring at resistance ${_detector.currentRunR} — wait for the '
          'pedals to stop completely. Keep your hands off the dial. '
          'Cadence: '
          '${_detector.currentRunCadence?.toStringAsFixed(0)} rpm.');
    }
    if (cad >= 70) {
      return _statusBanner(Colors.grey.shade200,
          'Lift both feet off the pedals at the same time to start a '
          'measurement. Cadence: ${cad.toStringAsFixed(0)} rpm at '
          'resistance $r.');
    }
    return _statusBanner(Colors.grey.shade200,
        'Pedal up to at least 70 rpm to begin. '
        'Cadence: ${cad.toStringAsFixed(0)} rpm at resistance $r.');
  }

  Widget _statusBanner(Color bg, String text) {
    return Container(
      decoration: BoxDecoration(
        color: bg,
        borderRadius: BorderRadius.circular(8),
      ),
      padding: const EdgeInsets.all(12),
      child: Text(text),
    );
  }

  Widget _pointsTable(BuildContext context) {
    if (_points.isEmpty) {
      return Center(child: Text('No measurements yet',
          style: Theme.of(context).textTheme.bodyMedium));
    }
    final body = Theme.of(context).textTheme.bodyMedium;
    return Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
      Padding(
        padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
        child: Row(children: [
          SizedBox(width: 110, child: Text('Resistance', style: body)),
          Expanded(child: Text('Quality', style: body)),
          SizedBox(width: 28, child: Text('', style: body)),
        ]),
      ),
      const Divider(height: 1),
      Expanded(child: ListView.builder(
        itemCount: _points.length,
        itemBuilder: (ctx, i) {
          final p = _points[i];
          return Padding(
            padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 4),
            child: Row(children: [
              SizedBox(width: 110, child: Text('${p.resistance}', style: body)),
              Expanded(child: Text(_qualityLabel(p.r2), style: body)),
              IconButton(
                icon: const Icon(Icons.close, size: 16),
                tooltip: 'Remove',
                onPressed: () => setState(() => _points.removeAt(i)),
              ),
            ]),
          );
        },
      )),
    ]);
  }

  static String _qualityLabel(double r2) {
    if (r2 >= 0.99) return 'Excellent';
    if (r2 >= 0.97) return 'Good';
    return 'Fair';
  }
}

class _FitPreviewDialog extends StatelessWidget {
  final BrakeFit fit;
  const _FitPreviewDialog({required this.fit});

  @override
  Widget build(BuildContext context) {
    final body = Theme.of(context).textTheme.bodyMedium;
    final maxResid = fit.residuals
        .map((r) => (r.measured - r.predicted).abs())
        .reduce((a, b) => a > b ? a : b);
    return AlertDialog(
      title: const Text('Save this calibration?'),
      content: Column(crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min, children: [
        Text(
            'You captured ${fit.residuals.length} measurements across '
            '${fit.residuals.map((r) => r.r).toSet().length} resistance levels.',
            style: body),
        const SizedBox(height: 12),
        Text('Fit quality: ${_overall(fit.rms, maxResid)}', style: body),
        const SizedBox(height: 8),
        Text('You can run the calibration again anytime if you want to '
            'improve it.', style: Theme.of(context).textTheme.bodySmall),
      ]),
      actions: [
        TextButton(onPressed: () => Navigator.pop(context, false),
            child: const Text('Cancel')),
        FilledButton(onPressed: () => Navigator.pop(context, true),
            child: const Text('Save')),
      ],
    );
  }

  static String _overall(double rms, double maxResid) {
    if (rms < 0.01 && maxResid < 0.02) return 'Excellent';
    if (rms < 0.02 && maxResid < 0.04) return 'Good';
    return 'Fair — consider redoing with steadier coastdowns';
  }
}
