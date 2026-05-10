import 'package:flutter/material.dart';

import 'physics/calibration.dart';
import 'prefs.dart';
import 'ui/home.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final calibration = await Calibration.load();
  final prefs = await AppPrefs.load();
  runApp(App(calibration: calibration, prefs: prefs));
}

class App extends StatelessWidget {
  final Calibration calibration;
  final AppPrefs prefs;
  const App({super.key, required this.calibration, required this.prefs});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'IC Bridge',
      theme: _theme(Brightness.light),
      darkTheme: _theme(Brightness.dark),
      themeMode: ThemeMode.system,
      home: HomePage(calibration: calibration, prefs: prefs),
    );
  }
}

ThemeData _theme(Brightness brightness) {
  final base = ThemeData(
    useMaterial3: true,
    brightness: brightness,
    colorScheme: ColorScheme.fromSeed(
      seedColor: Colors.green,
      brightness: brightness,
    ),
  );
  // Tabular figures on numeric/display styles so power, cadence, and HR
  // digits don't shift sideways at 4–8 Hz update rate.
  const tabular = [FontFeature.tabularFigures()];
  return base.copyWith(
    textTheme: base.textTheme.copyWith(
      displayLarge:
          base.textTheme.displayLarge?.copyWith(fontFeatures: tabular),
      displayMedium:
          base.textTheme.displayMedium?.copyWith(fontFeatures: tabular),
      displaySmall:
          base.textTheme.displaySmall?.copyWith(fontFeatures: tabular),
      headlineLarge:
          base.textTheme.headlineLarge?.copyWith(fontFeatures: tabular),
      headlineMedium:
          base.textTheme.headlineMedium?.copyWith(fontFeatures: tabular),
      headlineSmall:
          base.textTheme.headlineSmall?.copyWith(fontFeatures: tabular),
      titleLarge:
          base.textTheme.titleLarge?.copyWith(fontFeatures: tabular),
    ),
  );
}
