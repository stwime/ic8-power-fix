import 'dart:typed_data';

/// Port of analysis/decode_csc.py.
///
/// Cycling Speed and Cadence Measurement (0x2A5B) layout:
///   byte 0: flags
///     bit 0: WheelRevolutionDataPresent
///     bit 1: CrankRevolutionDataPresent
///   if bit 0:
///     uint32 cumulative_wheel_revs
///     uint16 last_wheel_event_time   (1/1024 s units, wraps at 65536)
///   if bit 1:
///     uint16 cumulative_crank_revs
///     uint16 last_crank_event_time   (1/1024 s units, wraps at 65536)
class CscMeasurement {
  final int? wheelRevs;
  final double? wheelEventTimeS;
  final int? crankRevs;
  final double? crankEventTimeS;

  CscMeasurement({
    this.wheelRevs,
    this.wheelEventTimeS,
    this.crankRevs,
    this.crankEventTimeS,
  });

  static CscMeasurement decode(Uint8List payload) {
    final ByteData bd = ByteData.sublistView(payload);
    final int flags = payload[0];
    int off = 1;

    int? wheelRevs;
    double? wheelT;
    int? crankRevs;
    double? crankT;

    if ((flags & 0x01) != 0 && off + 6 <= payload.length) {
      wheelRevs = bd.getUint32(off, Endian.little); off += 4;
      wheelT = bd.getUint16(off, Endian.little) / 1024.0; off += 2;
    }
    if ((flags & 0x02) != 0 && off + 4 <= payload.length) {
      crankRevs = bd.getUint16(off, Endian.little); off += 2;
      crankT = bd.getUint16(off, Endian.little) / 1024.0; off += 2;
    }
    return CscMeasurement(
      wheelRevs: wheelRevs, wheelEventTimeS: wheelT,
      crankRevs: crankRevs, crankEventTimeS: crankT,
    );
  }
}

const double _eventTimePeriod = 65536 / 1024.0;   // 64 s

/// Bring [curr] forward by 64 s as needed so it lies at-or-after [prev].
double unwrapEventTime(double prev, double curr) {
  while (curr < prev - _eventTimePeriod / 2) {
    curr += _eventTimePeriod;
  }
  return curr;
}

int unwrapRevs(int prev, int curr, int modulus) {
  while (curr < prev - (modulus ~/ 2)) {
    curr += modulus;
  }
  return curr;
}
