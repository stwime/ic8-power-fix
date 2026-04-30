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
      theme: ThemeData(
        colorScheme: ColorScheme.fromSeed(seedColor: Colors.green),
        useMaterial3: true,
      ),
      home: HomePage(calibration: calibration, prefs: prefs),
    );
  }
}
