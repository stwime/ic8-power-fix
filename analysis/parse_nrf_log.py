"""Parse an nRF Connect log of FTMS (0x2AD2) and CSC (0x2A5B) notifications.

Notifications appear like:
    I  HH:MM:SS.mmm  Notification received from 00002ad2-..., value: (0x) NN-NN-...
    I  HH:MM:SS.mmm  Notification received from 00002a5b-..., value: (0x) NN-NN-...

The IC8 emits both characteristics at ~1 Hz, paired within the same millisecond.
FTMS gives quantized cadence (0.5 rpm steps) once per second; CSC gives raw
crank revolution counts and event times to ~1 ms precision, so we can derive
a much higher-precision instantaneous cadence by differencing across two CSC
samples.

Output: one row per FTMS notification, with the most-recent CSC measurement
joined in. Columns:
    timestamp_s        — wall-clock seconds since first notification
    speed_kmh, cadence_rpm, distance_m, resistance, power_w,
    energy_kcal, hr_bpm                                          (from FTMS)
    crank_revs, crank_event_time_s, wheel_revs, wheel_event_time_s
                                                                 (from CSC, unwrapped)
    cadence_rpm_csc    — instantaneous cadence from delta crank revs / event time
                          (None if not derivable yet, e.g. first sample)
"""

import csv
import re
import sys
from pathlib import Path

from decode_ftms import decode_indoor_bike_data
from decode_csc import (decode_csc_measurement,
                        unwrap_event_time, unwrap_revs)

NOTIF_RE = re.compile(
    r"^I\s+(\d{2}):(\d{2}):(\d{2})\.(\d{3})\s+Notification received from "
    r"0000(2ad2|2a5b)-0000-1000-8000-00805f9b34fb.*\(0x\)\s+([0-9A-Fa-f-]+)"
)


def parse_log(path: Path):
    rows = []
    t0 = None
    last_csc = None              # latest CSC fields (unwrapped)
    prev_csc_for_deriv = None    # the CSC reading we last differenced against

    for line in path.read_text().splitlines():
        m = NOTIF_RE.match(line)
        if not m:
            continue
        h, mn, s, ms, uuid_short, hexstr = m.groups()
        t = int(h) * 3600 + int(mn) * 60 + int(s) + int(ms) / 1000.0
        if t0 is None:
            t0 = t
        ts = round(t - t0, 3)
        payload = bytes.fromhex(hexstr.replace("-", ""))

        if uuid_short == "2a5b":
            csc = decode_csc_measurement(payload)
            # Unwrap counters and event times against the running latest_csc.
            if last_csc is not None:
                if csc.crank_event_time_s is not None and last_csc["crank_event_time_s"] is not None:
                    csc.crank_event_time_s = unwrap_event_time(
                        last_csc["crank_event_time_s"], csc.crank_event_time_s)
                if csc.crank_revs is not None and last_csc["crank_revs"] is not None:
                    csc.crank_revs = unwrap_revs(
                        last_csc["crank_revs"], csc.crank_revs, 65536)
                if csc.wheel_event_time_s is not None and last_csc["wheel_event_time_s"] is not None:
                    csc.wheel_event_time_s = unwrap_event_time(
                        last_csc["wheel_event_time_s"], csc.wheel_event_time_s)
                # wheel_revs is uint32 — no realistic wrap concern.
            last_csc = {
                "crank_revs": csc.crank_revs,
                "crank_event_time_s": csc.crank_event_time_s,
                "wheel_revs": csc.wheel_revs,
                "wheel_event_time_s": csc.wheel_event_time_s,
            }
            continue

        # uuid_short == "2ad2"  (FTMS Indoor Bike Data)
        d = decode_indoor_bike_data(payload)

        # Compute instantaneous cadence from CSC if we have a previous sample.
        cad_csc = None
        if (last_csc is not None and prev_csc_for_deriv is not None
                and last_csc["crank_revs"] is not None
                and prev_csc_for_deriv["crank_revs"] is not None):
            d_revs = last_csc["crank_revs"] - prev_csc_for_deriv["crank_revs"]
            d_t = (last_csc["crank_event_time_s"]
                   - prev_csc_for_deriv["crank_event_time_s"])
            if d_revs > 0 and d_t > 0:
                cad_csc = round((d_revs / d_t) * 60.0, 3)

        rows.append({
            "timestamp_s": ts,
            "speed_kmh": d.speed_kmh,
            "cadence_rpm": d.cadence_rpm,
            "distance_m": d.distance_m,
            "resistance": d.resistance,
            "power_w": d.power_w,
            "energy_kcal": d.energy_total_kcal,
            "hr_bpm": d.heart_rate,
            "crank_revs": last_csc["crank_revs"] if last_csc else None,
            "crank_event_time_s": (
                round(last_csc["crank_event_time_s"], 6)
                if last_csc and last_csc["crank_event_time_s"] is not None else None),
            "wheel_revs": last_csc["wheel_revs"] if last_csc else None,
            "wheel_event_time_s": (
                round(last_csc["wheel_event_time_s"], 6)
                if last_csc and last_csc["wheel_event_time_s"] is not None else None),
            "cadence_rpm_csc": cad_csc,
        })

        # Step the differencing baseline only when crank_event_time advanced
        # (i.e. a new crank rev actually happened between this and the prev
        # reference). Otherwise keep prev_csc_for_deriv unchanged so we keep
        # accumulating until a rev arrives — that's how we get sub-rpm
        # resolution at low cadences.
        if last_csc is not None and last_csc["crank_event_time_s"] is not None:
            if (prev_csc_for_deriv is None
                    or (last_csc["crank_event_time_s"]
                        > prev_csc_for_deriv["crank_event_time_s"] + 1e-6)):
                prev_csc_for_deriv = dict(last_csc)
    return rows


def main():
    if len(sys.argv) != 3:
        print("usage: parse_nrf_log.py <input.txt> <output.csv>")
        sys.exit(1)
    rows = parse_log(Path(sys.argv[1]))
    if not rows:
        print("no notifications matched")
        sys.exit(1)
    with open(sys.argv[2], "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    n_with_csc = sum(1 for r in rows if r["cadence_rpm_csc"] is not None)
    print(f"wrote {len(rows)} rows to {sys.argv[2]} "
          f"({n_with_csc} with CSC-derived cadence)")


if __name__ == "__main__":
    main()
