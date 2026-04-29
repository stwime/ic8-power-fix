"""Decode FTMS Indoor Bike Data (0x2AD2) packets.

The flags field (uint16 LE) signals which optional fields follow.
Field order in the packet is fixed; only included if their flag bit is set.

Bit layout (FTMS Indoor Bike Data flags):
    bit 0: more data — when 0, Instantaneous Speed IS present (inverted!)
    bit 1: Average Speed
    bit 2: Instantaneous Cadence
    bit 3: Average Cadence
    bit 4: Total Distance
    bit 5: Resistance Level
    bit 6: Instantaneous Power
    bit 7: Average Power
    bit 8: Expended Energy (Total + Per Hour + Per Min)
    bit 9: Heart Rate
    bit 10: Metabolic Equivalent
    bit 11: Elapsed Time
    bit 12: Remaining Time

Field sizes (when present):
    Instantaneous Speed: uint16, 0.01 km/h
    Average Speed: uint16, 0.01 km/h
    Instantaneous Cadence: uint16, 0.5 RPM
    Average Cadence: uint16, 0.5 RPM
    Total Distance: uint24, 1 m
    Resistance Level: sint16, unitless
    Instantaneous Power: sint16, W
    Average Power: sint16, W
    Total Energy: uint16, kcal
    Energy Per Hour: uint16, kcal
    Energy Per Minute: uint8, kcal
    Heart Rate: uint8, bpm
    Metabolic Equivalent: uint8, 0.1
    Elapsed Time: uint16, s
    Remaining Time: uint16, s
"""

from dataclasses import dataclass


@dataclass
class IndoorBikeData:
    speed_kmh: float | None = None
    avg_speed_kmh: float | None = None
    cadence_rpm: float | None = None
    avg_cadence_rpm: float | None = None
    distance_m: int | None = None
    resistance: int | None = None
    power_w: int | None = None
    avg_power_w: int | None = None
    energy_total_kcal: int | None = None
    energy_per_hour_kcal: int | None = None
    energy_per_min_kcal: int | None = None
    heart_rate: int | None = None
    elapsed_s: int | None = None


def _u16(b: bytes, off: int) -> int:
    return int.from_bytes(b[off:off + 2], "little", signed=False)

def _s16(b: bytes, off: int) -> int:
    return int.from_bytes(b[off:off + 2], "little", signed=True)

def _u24(b: bytes, off: int) -> int:
    return int.from_bytes(b[off:off + 3], "little", signed=False)


def decode_indoor_bike_data(payload: bytes) -> IndoorBikeData:
    """Parse a FTMS 0x2AD2 notification payload."""
    flags = _u16(payload, 0)
    off = 2
    out = IndoorBikeData()

    if (flags & 0x0001) == 0:  # bit 0 INVERTED: 0 means present
        out.speed_kmh = _u16(payload, off) / 100.0
        off += 2
    if flags & 0x0002:
        out.avg_speed_kmh = _u16(payload, off) / 100.0
        off += 2
    if flags & 0x0004:
        out.cadence_rpm = _u16(payload, off) / 2.0
        off += 2
    if flags & 0x0008:
        out.avg_cadence_rpm = _u16(payload, off) / 2.0
        off += 2
    if flags & 0x0010:
        out.distance_m = _u24(payload, off)
        off += 3
    if flags & 0x0020:
        out.resistance = _s16(payload, off)
        off += 2
    if flags & 0x0040:
        out.power_w = _s16(payload, off)
        off += 2
    if flags & 0x0080:
        out.avg_power_w = _s16(payload, off)
        off += 2
    if flags & 0x0100:
        out.energy_total_kcal = _u16(payload, off)
        off += 2
        out.energy_per_hour_kcal = _u16(payload, off)
        off += 2
        out.energy_per_min_kcal = payload[off]
        off += 1
    if flags & 0x0200:
        out.heart_rate = payload[off]
        off += 1
    # bit 10 (metabolic equivalent), bit 11 (elapsed), bit 12 (remaining)
    # not parsed yet; add if we encounter them.
    return out


if __name__ == "__main__":
    # Smoke-test against the 4 packets we captured.
    samples = [
        bytes.fromhex("7403DC0A8800BA00000F00690002000000000000".replace(" ", "")[:38]),
        bytes.fromhex("74037A0D8C00FC00001D00B500040000000000".replace(" ", "")[:38]),
        bytes.fromhex("7403640FB60035010001D000D01050000000000".replace(" ", "")[:38]),
        bytes.fromhex("7403F20D9A00C601001D00CF00080000000000".replace(" ", "")[:38]),
    ]
    # Cleaner reconstruction from the captured strings:
    captured = [
        "7403 DC0A 8800 BA00 000F 0069 0002 0000 0000 00",
        "7403 7A0D 8C00 FC00 001D 00B5 0004 0000 0000 00",
        "7403 640F B600 3501 001D 000D 0105 0000 0000 00",
        "7403 F20D 9A00 C601 001D 00CF 0008 0000 0000 00",
    ]
    for hex_s in captured:
        b = bytes.fromhex(hex_s.replace(" ", ""))
        d = decode_indoor_bike_data(b)
        print(f"R={d.resistance:>3}  cad={d.cadence_rpm:>5.1f}  "
              f"P={d.power_w:>3}W  speed={d.speed_kmh:>5.2f}  "
              f"dist={d.distance_m}m  HR={d.heart_rate}")
