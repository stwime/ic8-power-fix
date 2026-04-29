"""Decode Cycling Speed and Cadence (CSC) Measurement (0x2A5B) packets.

Format:
    byte 0: flags
        bit 0: WheelRevolutionDataPresent
        bit 1: CrankRevolutionDataPresent
    if bit 0:
        uint32 cumulative_wheel_revs
        uint16 last_wheel_event_time   (unit: 1/1024 s, wraps at 65536)
    if bit 1:
        uint16 cumulative_crank_revs
        uint16 last_crank_event_time   (unit: 1/1024 s, wraps at 65536)

Event times wrap every ~64 s. Use unwrap() across consecutive samples
to get continuous time. Cumulative wheel revs are uint32 so wrap is
practically irrelevant. Crank revs are uint16 → wrap every ~3.6 hours
at 300 rpm; safe to ignore for our session lengths but unwrap anyway.
"""

from dataclasses import dataclass


EVENT_TIME_RES_HZ = 1024


@dataclass
class CSCMeasurement:
    wheel_revs: int | None = None
    wheel_event_time_s: float | None = None
    crank_revs: int | None = None
    crank_event_time_s: float | None = None


def _u16(b: bytes, off: int) -> int:
    return int.from_bytes(b[off:off + 2], "little", signed=False)


def _u32(b: bytes, off: int) -> int:
    return int.from_bytes(b[off:off + 4], "little", signed=False)


def decode_csc_measurement(payload: bytes) -> CSCMeasurement:
    flags = payload[0]
    off = 1
    out = CSCMeasurement()
    if flags & 0x01:
        out.wheel_revs = _u32(payload, off)
        off += 4
        out.wheel_event_time_s = _u16(payload, off) / EVENT_TIME_RES_HZ
        off += 2
    if flags & 0x02:
        out.crank_revs = _u16(payload, off)
        off += 2
        out.crank_event_time_s = _u16(payload, off) / EVENT_TIME_RES_HZ
        off += 2
    return out


def unwrap_event_time(prev_s: float, curr_s: float) -> float:
    """Add 64s if curr appears to have wrapped past prev. Caller is responsible
    for accumulating: pass prev = previous unwrapped value, get back current
    unwrapped value. Wrap period is 65536/1024 = 64 s.
    """
    period = 65536 / EVENT_TIME_RES_HZ
    # Bring curr to the same "epoch" as prev by adding multiples of period
    # while curr < prev (since event_time is monotonic in real time).
    while curr_s < prev_s - period / 2:
        curr_s += period
    return curr_s


def unwrap_revs(prev: int, curr: int, modulus: int) -> int:
    """Same idea for revolutions counter (crank uses uint16)."""
    while curr < prev - modulus / 2:
        curr += modulus
    return curr


if __name__ == "__main__":
    # Smoke test from observed log:
    #   value: (0x) 03-63-02-00-00-8C-4D-6B-02-FF-61
    # nRF Connect parsed: wheel=611, wheel_t=19852 ms, crank=619, crank_t=25087 ms
    sample = bytes.fromhex("0363020000" "8C4D" "6B02" "FF61")
    d = decode_csc_measurement(sample)
    print(d)
    # Expected: wheel=611, wheel_t=19852/1024=19.387s, crank=619, crank_t=25087/1024=24.499s
    assert d.wheel_revs == 611, d.wheel_revs
    assert d.crank_revs == 619, d.crank_revs
    assert abs(d.wheel_event_time_s - 19852/1024) < 1e-6
    assert abs(d.crank_event_time_s - 25087/1024) < 1e-6
    print("OK")
