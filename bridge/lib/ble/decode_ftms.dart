import 'dart:typed_data';

/// Port of analysis/decode_ftms.py — IC8 only writes a subset of fields.
///
/// FTMS Indoor Bike Data (0x2AD2) layout, in order, gated by flag bits:
///   uint16 flags
///   uint16 inst_speed       (bit 0 OFF means present, in 0.01 km/h)
///   uint16 avg_speed        (bit 0 ON  → present; off otherwise)
///   uint16 inst_cadence     (bit 1     0.5 rpm)
///   uint16 avg_cadence      (bit 2)
///   uint24 total_distance   (bit 3, 1 m units, little-endian 24-bit)
///   sint16 resistance       (bit 5, 0.1 unit; IC8 uses integer R)
///   sint16 inst_power       (bit 6, watts)
///   sint16 avg_power        (bit 7)
///   uint16 total_energy     (bit 8, kcal)
///   uint16 energy_per_hour  (bit 8)
///   uint8  energy_per_min   (bit 8)
///   uint8  heart_rate       (bit 9)
///   ...
class FtmsIndoorBikeData {
  final double? speedKmh;
  final double? cadenceRpm;
  final int? distanceM;
  final int? resistance;
  final int? powerW;
  final int? energyKcal;
  final int? heartRate;

  FtmsIndoorBikeData({
    this.speedKmh,
    this.cadenceRpm,
    this.distanceM,
    this.resistance,
    this.powerW,
    this.energyKcal,
    this.heartRate,
  });

  static FtmsIndoorBikeData decode(Uint8List payload) {
    final ByteData bd = ByteData.sublistView(payload);
    int off = 0;
    final int flags = bd.getUint16(off, Endian.little); off += 2;
    bool flag(int bit) => (flags & (1 << bit)) != 0;

    double? speed;
    double? cad;
    int? dist;
    int? res;
    int? pwr;
    int? energy;
    int? hr;

    if (!flag(0) && off + 2 <= payload.length) {       // inst_speed present
      speed = bd.getUint16(off, Endian.little) / 100.0; off += 2;
    }
    if (flag(1) && off + 2 <= payload.length) {        // avg_speed
      off += 2;
    }
    if (flag(2) && off + 2 <= payload.length) {        // inst_cadence
      cad = bd.getUint16(off, Endian.little) / 2.0; off += 2;
    }
    if (flag(3) && off + 2 <= payload.length) {        // avg_cadence
      off += 2;
    }
    if (flag(4) && off + 3 <= payload.length) {        // total_distance (uint24)
      dist = payload[off] | (payload[off + 1] << 8) | (payload[off + 2] << 16);
      off += 3;
    }
    if (flag(5) && off + 2 <= payload.length) {        // resistance (sint16)
      res = bd.getInt16(off, Endian.little); off += 2;
    }
    if (flag(6) && off + 2 <= payload.length) {        // inst_power (sint16)
      pwr = bd.getInt16(off, Endian.little); off += 2;
    }
    if (flag(7) && off + 2 <= payload.length) {        // avg_power
      off += 2;
    }
    if (flag(8) && off + 5 <= payload.length) {        // energy block (2+2+1)
      energy = bd.getUint16(off, Endian.little); off += 5;
    }
    if (flag(9) && off + 1 <= payload.length) {        // hr (uint8)
      hr = payload[off]; off += 1;
    }

    return FtmsIndoorBikeData(
      speedKmh: speed, cadenceRpm: cad, distanceM: dist,
      resistance: res, powerW: pwr, energyKcal: energy, heartRate: hr,
    );
  }
}

/// Encode an Indoor Bike Data notification with speed + cadence + power.
/// Flags: bit 2 (cadence), bit 6 (power). bit 0 OFF → speed present.
Uint8List encodeIndoorBikeData({
  required double speedKmh,
  required double cadenceRpm,
  required int powerW,
}) {
  // 2 bytes flags + 2 speed + 2 cadence + 2 power = 8 bytes
  final ByteData bd = ByteData(8);
  final int flags = (1 << 2) | (1 << 6);
  bd.setUint16(0, flags, Endian.little);
  bd.setUint16(2, (speedKmh * 100).round().clamp(0, 0xFFFF), Endian.little);
  bd.setUint16(4, (cadenceRpm * 2).round().clamp(0, 0xFFFF), Endian.little);
  bd.setInt16(6, powerW.clamp(-32768, 32767), Endian.little);
  return bd.buffer.asUint8List();
}

/// Encode a Cycling Power Measurement (0x2A63): flags=0, just sint16 power.
Uint8List encodeCyclingPowerMeasurement(int powerW) {
  final ByteData bd = ByteData(4);
  bd.setUint16(0, 0, Endian.little);             // flags
  bd.setInt16(2, powerW.clamp(-32768, 32767), Endian.little);
  return bd.buffer.asUint8List();
}
