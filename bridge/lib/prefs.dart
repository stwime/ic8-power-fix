import 'package:shared_preferences/shared_preferences.dart';

/// Non-physics user preferences (proxy device name, etc.). Persisted via
/// SharedPreferences alongside [Calibration].
class AppPrefs {
  static const String defaultProxyName = 'IC Bike (corrected)';

  /// Practical upper bound on a BLE local name. iOS truncates the advertised
  /// name in scan results around 26 chars depending on what other AD fields
  /// share the packet, so we keep it short to avoid truncation surprises.
  static const int proxyNameMaxLen = 24;

  static const String _keyProxyName = 'prefs.proxyName';

  String proxyName;

  AppPrefs._({required this.proxyName});

  AppPrefs.defaults() : proxyName = defaultProxyName;

  static Future<AppPrefs> load() async {
    final prefs = await SharedPreferences.getInstance();
    return AppPrefs._(
      proxyName: prefs.getString(_keyProxyName) ?? defaultProxyName,
    );
  }

  Future<void> setProxyName(String v) async {
    final trimmed = v.trim();
    proxyName = trimmed.isEmpty ? defaultProxyName : trimmed;
    final prefs = await SharedPreferences.getInstance();
    if (proxyName == defaultProxyName) {
      await prefs.remove(_keyProxyName);
    } else {
      await prefs.setString(_keyProxyName, proxyName);
    }
  }
}
