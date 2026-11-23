"""Absolute scale from your own threshold power, no extra hardware.

Spin-downs fix how the IC8's brake power depends on the dial and cadence,
but only up to one overall factor: a brake twice as strong on a flywheel
twice as heavy slows down identically. This script takes that factor from
a number you already have, your threshold power (FTP) measured by a real
power meter, usually on your road bike.

Inputs
  --indoor   .fit files a training app recorded while you rode the IC8
             (Rouvy, MyWhoosh, Zwift, Garmin). Include an all-out
             20-minute effort, or a hard hour.
  --source   what the training app was paired to during those rides:
               bike    the IC8 itself (default). The file holds the bike's
                       power and cadence but not the dial position. R is
                       recovered per second by inverting the bike's
                       broadcast formula (analysis/bridge_model.py), and
                       the bridge model is evaluated at that R and cadence.
               bridge  "IC Bike (corrected)". The file holds bridge power;
                       pass --scale-used with the Power scale that was set.
  --ftp      your threshold power in W from a real power meter.
  --outdoor  .fit files from that power meter, compared over the same
             durations. Without --ftp, the outdoor files supply it.

Output
  Best average power over 5 to 60 minutes indoors (bridge model at Power
  scale 100%) and outdoors, and the Power scale that makes the indoor
  threshold match: FTP / max(0.95 x best 20 min, best 60 min).

What the anchor is worth
  It carries a power-meter number through your body, so any gap between
  your indoor and outdoor threshold (heat, cooling, position, motivation)
  and day-to-day form land in the scale. It holds at the dial positions
  and cadences of the effort it came from; the spin-downs supply the
  shape elsewhere. On the bike source, R comes from inverting the bike's
  broadcast formula, which is within 2.5% of the bike's number in every
  R band; model power is steep in R, so that is up to about 5% in model
  power.

The flywheel term I·ω·dω/dt is left out: over a window of minutes it
averages to the change in flywheel kinetic energy over that window,
under 0.5 W.

Usage
  python analysis/ftp_anchor.py --indoor ftp_test.fit --ftp 200
  python analysis/ftp_anchor.py --indoor a.fit b.fit --outdoor road.fit
  python analysis/ftp_anchor.py --indoor bridge_ride.fit --source bridge \
      --scale-used 1.0 --ftp 200
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.bridge_model import (  # noqa: E402
    brake_power,
    resistance_from_broadcast,
)

DURATIONS_MIN = (5, 10, 20, 30, 40, 60)
MIN_CADENCE = 30.0     # below this the bike's numbers are start/stop noise
R_MAX = 99.0           # R = 100 locks the crank; the app outputs nothing
SCALE_MIN, SCALE_MAX = 0.5, 2.0   # Calibration.powerScaleMin/Max


def load_fit(path: Path) -> dict[str, np.ndarray]:
    """Per-second power and cadence on a 1 s grid from the first record.
    Gaps (pauses, dropouts) are zero, so they count against an average."""
    try:
        import fitparse
    except ImportError:
        sys.exit("needs fitparse: pip install -r analysis/requirements.txt")
    recs = []
    for msg in fitparse.FitFile(str(path)).get_messages("record"):
        d = {f.name: f.value for f in msg}
        if d.get("timestamp") is not None:
            recs.append(d)
    if not recs:
        sys.exit(f"{path}: no records")
    t0 = recs[0]["timestamp"]
    idx = np.array([round((r["timestamp"] - t0).total_seconds()) for r in recs])
    n = int(idx.max()) + 1
    power = np.zeros(n)
    cad = np.zeros(n)
    for i, r in zip(idx, recs):
        power[i] = r.get("power") or 0.0
        cad[i] = r.get("cadence") or 0.0
    return {"power": power, "cadence": cad}


def model_power(ride: dict[str, np.ndarray], source: str,
                scale_used: float) -> tuple[np.ndarray, np.ndarray]:
    """Bridge-model power at Power scale 1.0 per second, and the dial
    position used (NaN where unknown or not pedalling)."""
    p, cad = ride["power"], ride["cadence"]
    r = np.full_like(p, np.nan)
    if source == "bridge":
        return p / scale_used, r
    ok = (cad >= MIN_CADENCE) & (p > 0)
    r[ok] = np.clip(resistance_from_broadcast(p[ok], cad[ok]), 0.0, R_MAX)
    out = np.zeros_like(p)
    out[ok] = brake_power(r[ok], cad[ok])
    return out, r


def best_window(x: np.ndarray, n: int) -> tuple[float, int]:
    """Highest n-second average and where it starts; (nan, -1) if shorter."""
    if len(x) < n:
        return float("nan"), -1
    c = np.concatenate(([0.0], np.cumsum(x)))
    avg = (c[n:] - c[:-n]) / n
    i = int(np.argmax(avg))
    return float(avg[i]), i


def threshold(best: dict[int, float]) -> float:
    """FTP from a power-duration record: the larger of 0.95 x best 20 min
    and best 60 min, so whichever effort was closer to all-out counts."""
    cands = [0.95 * best.get(20, np.nan), best.get(60, np.nan)]
    cands = [c for c in cands if np.isfinite(c)]
    return max(cands) if cands else float("nan")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--indoor", nargs="+", required=True, type=Path)
    ap.add_argument("--source", choices=["bike", "bridge"], default="bike")
    ap.add_argument("--scale-used", type=float, default=1.0,
                    help="bridge source: Power scale set during the rides (1.0 = 100%%)")
    ap.add_argument("--ftp", type=float, help="threshold power in W from a real power meter")
    ap.add_argument("--outdoor", nargs="*", default=[], type=Path)
    args = ap.parse_args()
    if args.ftp is None and not args.outdoor:
        ap.error("give --ftp, --outdoor, or both")

    model_best: dict[int, float] = {}
    shown_best: dict[int, float] = {}
    where: dict[int, tuple[str, float, float]] = {}
    for path in args.indoor:
        ride = load_fit(path)
        mp, r = model_power(ride, args.source, args.scale_used)
        for d in DURATIONS_MIN:
            v, i = best_window(mp, d * 60)
            if np.isfinite(v) and v > model_best.get(d, -1.0):
                model_best[d] = v
                seg = slice(i, i + d * 60)
                cad = ride["cadence"][seg]
                pedalling = cad >= MIN_CADENCE
                med_r = (float(np.nanmedian(r[seg])) if np.isfinite(r[seg]).any()
                         else float("nan"))
                med_cad = float(np.median(cad[pedalling])) if pedalling.any() else 0.0
                where[d] = (path.name, med_r, med_cad)
            s, _ = best_window(ride["power"], d * 60)
            if np.isfinite(s):
                shown_best[d] = max(shown_best.get(d, -1.0), s)

    out_best: dict[int, float] = {}
    for path in args.outdoor:
        p = load_fit(path)["power"]
        for d in DURATIONS_MIN:
            v, _ = best_window(p, d * 60)
            if np.isfinite(v):
                out_best[d] = max(out_best.get(d, -1.0), v)

    shown = "bike shows" if args.source == "bike" else "app showed"
    print(f"Best average power (W). 'bridge' is the model at Power scale 100%.")
    out_hdr = f" {'outdoor':>8} {'out/bridge':>11}" if out_best else ""
    print(f"{'min':>4} {'bridge':>7} {shown:>11}{out_hdr}   window")
    for d in DURATIONS_MIN:
        if d not in model_best:
            continue
        name, med_r, med_cad = where[d]
        loc = f"{name}, {med_cad:.0f} rpm" + (f", R≈{med_r:.0f}" if np.isfinite(med_r) else "")
        out_col = ""
        if out_best:
            ob = out_best.get(d, float("nan"))
            out_col = f" {ob:8.0f} {ob / model_best[d]:11.2f}"
        print(f"{d:4d} {model_best[d]:7.0f} {shown_best.get(d, float('nan')):11.0f}"
              f"{out_col}   {loc}")

    model_ftp = threshold(model_best)
    if not np.isfinite(model_ftp):
        sys.exit("No 20- or 60-minute window in the indoor files.")
    print(f"\nIndoor threshold, bridge model at 100%: {model_ftp:.0f} W")

    if args.ftp is not None:
        ref, what = args.ftp, "--ftp"
    else:
        ref, what = threshold(out_best), "outdoor files"
        if not np.isfinite(ref):
            sys.exit("No 20- or 60-minute window in the outdoor files.")
    scale = ref / model_ftp
    print(f"Reference threshold ({what}): {ref:.0f} W")
    print(f"Power scale: {scale * 100:.0f}%")
    if not SCALE_MIN <= scale <= SCALE_MAX:
        print(f"Outside the slider's {SCALE_MIN * 100:.0f}–{SCALE_MAX * 100:.0f}% range: "
              "check that the indoor files hold a real threshold effort.")


if __name__ == "__main__":
    main()
