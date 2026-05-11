"""BLE logger for the Schwinn 800IC / IC4 / IC8 / Bowflex C6 family.

Subscribes to FTMS Indoor Bike Data (0x2AD2) notifications and writes
one CSV row per packet. Optionally pairs to an HR strap (0x180D / 0x2A37).

Usage:
    pip install bleak
    python analysis/ic8_logger.py --output session.csv
    # ride / sweep R+cadence
    # Ctrl+C to stop

The logger discovers devices advertising the FTMS service. If multiple are
nearby, pass --device-name "IC Bike" or --device-address <BLE MAC>.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import time
from pathlib import Path

# Allow running as `python analysis/ic8_logger.py` from project root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.decode_ftms import decode_indoor_bike_data  # noqa: E402

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    print("Need bleak: pip install bleak", file=sys.stderr)
    raise

FTMS_SERVICE = "00001826-0000-1000-8000-00805f9b34fb"
INDOOR_BIKE_DATA = "00002ad2-0000-1000-8000-00805f9b34fb"
HR_MEASUREMENT = "00002a37-0000-1000-8000-00805f9b34fb"


async def find_bike(name_substr: str | None, address: str | None, scan_time: float):
    if address:
        print(f"Connecting to {address} directly...")
        return address
    print(f"Scanning {scan_time}s for FTMS devices...")
    devs = await BleakScanner.discover(timeout=scan_time, return_adv=True)
    candidates = []
    for dev, adv in devs.values():
        services = (adv.service_uuids or [])
        if FTMS_SERVICE in services:
            candidates.append((dev, adv))
        elif name_substr and dev.name and name_substr.lower() in dev.name.lower():
            candidates.append((dev, adv))
    if not candidates:
        print("No FTMS devices found. Pass --device-name or --device-address.", file=sys.stderr)
        for dev, adv in devs.values():
            if dev.name:
                print(f"  seen: {dev.name}  {dev.address}", file=sys.stderr)
        return None
    if len(candidates) > 1:
        print("Multiple FTMS devices — pick one with --device-name or --device-address:", file=sys.stderr)
        for dev, _ in candidates:
            print(f"  {dev.name}  {dev.address}", file=sys.stderr)
        return None
    dev, _ = candidates[0]
    print(f"Found {dev.name} ({dev.address})")
    return dev.address


async def run(args):
    address = await find_bike(args.device_name, args.device_address, args.scan_time)
    if not address:
        sys.exit(2)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    csv_file = out_path.open("w", newline="")
    writer = csv.writer(csv_file)
    writer.writerow([
        "t_unix", "t_rel", "resistance", "cadence_rpm", "power_w_ic8",
        "speed_kmh", "distance_m", "heart_rate", "raw_hex",
    ])

    t0 = time.time()
    last_hr: int | None = None

    def on_ftms(_h, payload: bytearray):
        nonlocal last_hr
        try:
            d = decode_indoor_bike_data(bytes(payload))
        except Exception as e:
            print(f"decode error: {e}", file=sys.stderr)
            return
        now = time.time()
        # FTMS HR is whatever the bike has; prefer external strap if newer.
        hr = last_hr if last_hr is not None else d.heart_rate
        writer.writerow([
            f"{now:.3f}", f"{now - t0:.2f}",
            d.resistance, d.cadence_rpm, d.power_w,
            d.speed_kmh, d.distance_m, hr,
            payload.hex(),
        ])
        csv_file.flush()
        print(f"\rt={now - t0:6.1f}  R={d.resistance}  cad={d.cadence_rpm:>5.1f}  "
              f"P={d.power_w:>3}W  HR={hr}    ", end="")

    def on_hr(_h, payload: bytearray):
        nonlocal last_hr
        # HR Measurement: byte 0 flags, byte 1 = uint8 HR (or 2-byte if flag bit 0 set)
        if not payload:
            return
        flags = payload[0]
        if flags & 0x01:
            last_hr = int.from_bytes(payload[1:3], "little")
        else:
            last_hr = payload[1]

    print(f"Connecting to {address}…")
    async with BleakClient(address) as client:
        print("Connected. Subscribing to FTMS Indoor Bike Data (0x2AD2)…")
        await client.start_notify(INDOOR_BIKE_DATA, on_ftms)
        try:
            await client.start_notify(HR_MEASUREMENT, on_hr)
            print("Subscribed to HR (0x2A37) too.")
        except Exception:
            print("No HR characteristic on this device (use a separate HR strap if you want HR).")
        print(f"Logging to {out_path}. Ctrl+C to stop.\n")
        try:
            while True:
                await asyncio.sleep(1)
        except (KeyboardInterrupt, asyncio.CancelledError):
            pass
        finally:
            print("\nStopping…")
            try:
                await client.stop_notify(INDOOR_BIKE_DATA)
            except Exception:
                pass
            csv_file.close()
            print(f"Wrote {out_path}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", "-o", default="session.csv")
    p.add_argument("--device-name", help="Substring to match in advertising name (e.g. 'IC Bike')")
    p.add_argument("--device-address", help="Direct BLE address / UUID")
    p.add_argument("--scan-time", type=float, default=8.0)
    args = p.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
