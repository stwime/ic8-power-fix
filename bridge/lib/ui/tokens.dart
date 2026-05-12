import 'package:flutter/widgets.dart';

/// Shared layout tokens. Keeps all three screens on the same 4 px grid so
/// section gaps, padding, and gutters line up across the app.
class Insets {
  static const double xs = 4;
  static const double sm = 8;
  static const double md = 12;
  static const double lg = 16;
  static const double xl = 24;
  static const double xxl = 32;
}

/// Card / tile / banner corner radii. Picked once so containers don't drift
/// from 8 to 12 to 16 across screens.
class Radii {
  static const double tile = 12;
  static const double card = 16;
  static const double pill = 999;
}

/// Motion durations and curves. Default to ease-out for natural deceleration;
/// keep durations short so live-data screens don't feel sluggish.
class Motion {
  static const Duration fast = Duration(milliseconds: 150);
  static const Duration normal = Duration(milliseconds: 220);
  static const Curve curve = Curves.easeOutCubic;
}
