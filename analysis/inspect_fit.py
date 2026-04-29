"""Quick inspection of a FIT file: device sources, fields present, summary stats."""
import sys
from collections import Counter
from pathlib import Path

import fitdecode


def inspect(path: Path):
    print(f"\n=== {path.name} ===")
    field_counter = Counter()
    devices = []
    sport = None
    n_records = 0
    pwr, hr, cad, spd = [], [], [], []

    with fitdecode.FitReader(str(path)) as fit:
        for frame in fit:
            if not isinstance(frame, fitdecode.FitDataMessage):
                continue
            if frame.name == "device_info":
                d = {f.name: f.value for f in frame.fields}
                # Capture meaningful device entries only
                if d.get("manufacturer") or d.get("product_name") or d.get("source_type"):
                    devices.append({k: v for k, v in d.items()
                                    if k in ("manufacturer", "product_name",
                                             "source_type", "device_type",
                                             "ant_device_type", "battery_status")})
            elif frame.name == "sport":
                sport = {f.name: f.value for f in frame.fields}
            elif frame.name == "record":
                n_records += 1
                fields = {f.name: f.value for f in frame.fields}
                for k in fields:
                    field_counter[k] += 1
                if "power" in fields and fields["power"] is not None:
                    pwr.append(fields["power"])
                if "heart_rate" in fields and fields["heart_rate"] is not None:
                    hr.append(fields["heart_rate"])
                if "cadence" in fields and fields["cadence"] is not None:
                    cad.append(fields["cadence"])
                if "speed" in fields and fields["speed"] is not None:
                    spd.append(fields["speed"])

    print(f"records: {n_records}")
    print(f"sport: {sport}")
    print(f"devices ({len(devices)} entries):")
    seen = set()
    for d in devices:
        key = tuple(sorted(d.items()))
        if key in seen:
            continue
        seen.add(key)
        print(f"  {d}")

    print(f"top fields seen: {field_counter.most_common(12)}")

    def stats(name, vals):
        if not vals:
            print(f"  {name}: none")
            return
        import statistics
        print(f"  {name}: n={len(vals)} min={min(vals)} max={max(vals)} "
              f"mean={statistics.mean(vals):.1f} median={statistics.median(vals):.1f}")

    print("summary:")
    stats("power (W)", pwr)
    stats("HR (bpm)", hr)
    stats("cadence (rpm)", cad)
    stats("speed (m/s)", spd)


if __name__ == "__main__":
    for path in sys.argv[1:]:
        inspect(Path(path))
