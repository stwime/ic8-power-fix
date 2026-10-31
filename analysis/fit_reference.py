"""Compare the bridge model against a reference power meter and build a
measured power table for the app.

Inputs
  --bike   CSV from analysis/ic8_logger.py (t_unix, resistance, cadence_rpm,
           power_w_ic8, ...), recorded on a laptop during the session.
  --ref    The power meter's recording: a .fit file (pedals paired to a head
           unit or phone app) or a CSV with columns t_unix, power_w and
           optionally cadence_rpm.

What it does
  1. Aligns the two clocks. If the reference carries cadence, the offset is
     found by cross-correlating cadence; pass --offset to force one.
  2. Keeps steady samples: R stable, cadence stable, no acceleration.
  3. Bins them on an (R, cadence) grid and averages reference power, bike
     power and bridge-model power per cell.
  4. Reports the ratio reference/model per cell and a linear trend in R.
     A flat ratio means the physics shape is right and only the scale was
     off (set the Power scale slider to the ratio). A ratio that grows or
     shrinks with R means the shape is wrong and the table should replace
     the model.
  5. Writes power_table.json in the format bridge/lib/physics/power_table.dart
     reads. Cells the session did not cover are filled from the model times
     the nearest measured ratio and listed under "filled" so you know.

Usage
  python analysis/fit_reference.py --bike session.csv --ref ride.fit \
      --out data/calibration/reference/2026-11-14

Run from the repo root. See docs/reference-session.md for the protocol.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from analysis.bridge_model import I_CRANK, brake_power  # noqa: E402


# ---------------------------------------------------------------------------
# Loading.
# ---------------------------------------------------------------------------

def load_bike(path: Path) -> dict[str, np.ndarray]:
    rows = list(csv.DictReader(path.open()))
    if not rows:
        sys.exit(f"{path}: empty")
    def col(name, default=np.nan):
        out = []
        for r in rows:
            v = r.get(name, "")
            out.append(float(v) if v not in ("", "None") else default)
        return np.array(out, dtype=float)
    return {
        "t": col("t_unix"),
        "r": col("resistance"),
        "cad": col("cadence_rpm"),
        "p_bike": col("power_w_ic8"),
    }


def load_ref(path: Path) -> dict[str, np.ndarray]:
    if path.suffix.lower() == ".fit":
        return _load_fit(path)
    rows = list(csv.DictReader(path.open()))
    if not rows:
        sys.exit(f"{path}: empty")
    t = np.array([float(r["t_unix"]) for r in rows])
    p = np.array([float(r["power_w"]) for r in rows])
    cad = None
    if "cadence_rpm" in rows[0]:
        cad = np.array([float(r["cadence_rpm"] or "nan") for r in rows])
    return {"t": t, "p": p, "cad": cad}


def _load_fit(path: Path) -> dict[str, np.ndarray]:
    try:
        from fitparse import FitFile
    except ImportError:
        sys.exit("Reading .fit needs fitparse: pip install fitparse")
    t, p, cad = [], [], []
    for rec in FitFile(str(path)).get_messages("record"):
        d = {f.name: f.value for f in rec.fields}
        ts = d.get("timestamp")
        if ts is None or d.get("power") is None:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        t.append(ts.timestamp())
        p.append(float(d["power"]))
        c = d.get("cadence")
        cad.append(float(c) if c is not None else np.nan)
    if not t:
        sys.exit(f"{path}: no power records")
    cad_arr = np.array(cad)
    return {"t": np.array(t), "p": np.array(p),
            "cad": cad_arr if np.isfinite(cad_arr).any() else None}


# ---------------------------------------------------------------------------
# Alignment.
# ---------------------------------------------------------------------------

def resample(t_src, y_src, t_dst):
    """Nearest-sample lookup onto t_dst, NaN where no source sample within 2 s."""
    order = np.argsort(t_src)
    t_src, y_src = t_src[order], y_src[order]
    idx = np.searchsorted(t_src, t_dst)
    idx = np.clip(idx, 1, len(t_src) - 1)
    left, right = idx - 1, idx
    pick = np.where(np.abs(t_src[left] - t_dst) <= np.abs(t_src[right] - t_dst),
                    left, right)
    out = y_src[pick].astype(float)
    out[np.abs(t_src[pick] - t_dst) > 2.0] = np.nan
    return out


def find_offset(bike, ref, search_s=120.0):
    """Offset to ADD to the reference clock so it matches the bike clock,
    from cross-correlating 1 Hz cadence. Returns None if the reference has
    no cadence."""
    if ref["cad"] is None or not np.isfinite(ref["cad"]).any():
        return None
    t0 = max(bike["t"].min(), ref["t"].min() - search_s)
    t1 = min(bike["t"].max(), ref["t"].max() + search_s)
    grid = np.arange(t0, t1, 1.0)
    b = resample(bike["t"], bike["cad"], grid)
    best, best_off = -np.inf, 0.0
    for off in np.arange(-search_s, search_s + 1, 1.0):
        r = resample(ref["t"] + off, ref["cad"], grid)
        m = np.isfinite(b) & np.isfinite(r)
        if m.sum() < 60:
            continue
        bb, rr = b[m] - b[m].mean(), r[m] - r[m].mean()
        denom = np.sqrt((bb * bb).sum() * (rr * rr).sum())
        if denom == 0:
            continue
        c = (bb * rr).sum() / denom
        if c > best:
            best, best_off = c, off
    if best < 0.5:
        print(f"warning: cadence correlation only {best:.2f} at offset "
              f"{best_off:+.0f} s; check the clocks or pass --offset",
              file=sys.stderr)
    return best_off


# ---------------------------------------------------------------------------
# Steady-state selection and binning.
# ---------------------------------------------------------------------------

def median_filter(x, win=5):
    """Rolling median over the trailing `win` samples, as the app does for R."""
    out = np.array(x, dtype=float)
    for i in range(len(x)):
        w = x[max(0, i - win + 1):i + 1]
        w = w[np.isfinite(w)]
        out[i] = np.median(w) if len(w) else np.nan
    return out


def steady_mask(r, cad, win=5, r_tol=1, cad_tol=4.0, accel_tol=3.0):
    """True where R has been constant (within the dial's ±1 jitter) and
    cadence stable over `win` samples, and cadence changes by less than
    accel_tol rpm per sample."""
    n = len(r)
    ok = np.zeros(n, dtype=bool)
    for i in range(win, n):
        rw = r[i - win:i + 1]
        cw = cad[i - win:i + 1]
        if not (np.isfinite(rw).all() and np.isfinite(cw).all()):
            continue
        if rw.max() - rw.min() > r_tol:
            continue
        if cw.max() - cw.min() > cad_tol:
            continue
        if abs(cw[-1] - cw[-2]) > accel_tol:
            continue
        ok[i] = True
    return ok


def bin_cells(r, cad, p_ref, p_bike, p_model, r_step, cad_step, min_n):
    r_bin = np.round(r / r_step) * r_step
    cad_bin = np.round(cad / cad_step) * cad_step
    cells = {}
    for rb, cb, pr, pb, pm in zip(r_bin, cad_bin, p_ref, p_bike, p_model):
        if not (np.isfinite(pr) and np.isfinite(pm)):
            continue
        cells.setdefault((rb, cb), []).append((pr, pb, pm))
    out = []
    for (rb, cb), rows in sorted(cells.items()):
        a = np.array(rows)
        if len(a) < min_n:
            continue
        out.append({
            "r": float(rb), "cadence_rpm": float(cb), "n": int(len(a)),
            "ref_w": float(a[:, 0].mean()), "ref_sd": float(a[:, 0].std()),
            "bike_w": float(np.nanmean(a[:, 1])) if np.isfinite(a[:, 1]).any() else None,
            "model_w": float(a[:, 2].mean()),
        })
    return out


# ---------------------------------------------------------------------------
# Reporting and table.
# ---------------------------------------------------------------------------

def report(cells):
    print(f"{'R':>4} {'rpm':>4} {'n':>4} {'ref W':>7} {'±':>5} {'model W':>8} "
          f"{'bike W':>7} {'ref/model':>9} {'ref/bike':>8}")
    for c in cells:
        ratio_m = c["ref_w"] / c["model_w"] if c["model_w"] > 0 else float("nan")
        ratio_b = (c["ref_w"] / c["bike_w"]
                   if c["bike_w"] not in (None, 0) else float("nan"))
        print(f"{c['r']:4.0f} {c['cadence_rpm']:4.0f} {c['n']:4d} "
              f"{c['ref_w']:7.1f} {c['ref_sd']:5.1f} {c['model_w']:8.1f} "
              f"{(c['bike_w'] or float('nan')):7.1f} {ratio_m:9.3f} {ratio_b:8.3f}")

    r = np.array([c["r"] for c in cells])
    ratio = np.array([c["ref_w"] / c["model_w"] for c in cells])
    w = np.array([c["n"] for c in cells], dtype=float)
    good = np.isfinite(ratio) & (ratio > 0)
    if good.sum() < 3:
        print("\nToo few cells for a trend.")
        return None
    r, ratio, w = r[good], ratio[good], w[good]
    mean = float(np.average(ratio, weights=w))
    A = np.vstack([np.ones_like(r), r - r.mean()]).T
    sw = np.sqrt(w)
    coef, *_ = np.linalg.lstsq(A * sw[:, None], ratio * sw, rcond=None)
    a, b = coef
    span = b * (r.max() - r.min())
    scatter = float(np.sqrt(np.average((ratio - (a + b * (r - r.mean()))) ** 2,
                                       weights=w)))
    print(f"\nWeighted mean ref/model = {mean:.3f}")
    print(f"Trend in R: {b * 10:+.3f} per 10 dial steps, "
          f"{span:+.3f} across R {r.min():.0f}–{r.max():.0f}; "
          f"scatter about the trend {scatter:.3f}")
    if abs(span) < 0.10 and abs(span) < 2 * scatter:
        print("Verdict: ratio is flat in R. The spin-down shape holds; set the "
              f"Power scale slider to {mean * 100:.0f}% or load the table.")
    else:
        print("Verdict: ratio depends on R. The physics shape is wrong for this "
              "bike; load the table in the app.")
    return {"mean_ratio": mean, "trend_per_10R": float(b * 10),
            "span": float(span), "scatter": scatter}


def build_table(cells, source):
    r_grid = sorted({c["r"] for c in cells})
    cad_grid = sorted({c["cadence_rpm"] for c in cells})
    if len(r_grid) < 2 or len(cad_grid) < 2:
        sys.exit("Need at least two R and two cadence values with enough "
                 "steady samples to build a table.")
    measured = {(c["r"], c["cadence_rpm"]): c for c in cells}
    watts, filled = [], []
    for rv in r_grid:
        row = []
        for cv in cad_grid:
            c = measured.get((rv, cv))
            if c is not None:
                row.append(round(c["ref_w"], 1))
                continue
            # Nearest measured cell's ratio applied to the model here.
            near = min(cells, key=lambda k: (abs(k["r"] - rv) / 10.0) ** 2
                       + (abs(k["cadence_rpm"] - cv) / 20.0) ** 2)
            ratio = near["ref_w"] / near["model_w"] if near["model_w"] > 0 else 1.0
            row.append(round(max(0.0, brake_power(rv, cv) * ratio), 1))
            filled.append([rv, cv])
        watts.append(row)
    return {
        "version": 1,
        "source": source,
        "r": r_grid,
        "cadence_rpm": cad_grid,
        "watts": watts,
        "i_crank": I_CRANK,
        "filled": filled,
    }


def table_json(table):
    """One grid row per line so the file is short enough to paste by hand."""
    rows = ",\n".join("    " + json.dumps(row) for row in table["watts"])
    return (
        "{\n"
        f'  "version": {table["version"]},\n'
        f'  "source": {json.dumps(table["source"])},\n'
        f'  "r": {json.dumps(table["r"])},\n'
        f'  "cadence_rpm": {json.dumps(table["cadence_rpm"])},\n'
        f'  "watts": [\n{rows}\n  ],\n'
        f'  "i_crank": {table["i_crank"]},\n'
        f'  "filled": {json.dumps(table["filled"])}\n'
        "}\n"
    )


def plot(cells, out_path):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return
    cads = sorted({c["cadence_rpm"] for c in cells})
    fig, axes = plt.subplots(1, len(cads), figsize=(4.5 * len(cads), 4),
                             sharey=True, squeeze=False)
    for ax, cv in zip(axes[0], cads):
        cs = [c for c in cells if c["cadence_rpm"] == cv]
        r = [c["r"] for c in cs]
        ax.errorbar(r, [c["ref_w"] for c in cs], yerr=[c["ref_sd"] for c in cs],
                    fmt="o-", label="power meter")
        ax.plot(r, [c["model_w"] for c in cs], "s--", label="bridge model")
        if all(c["bike_w"] is not None for c in cs):
            ax.plot(r, [c["bike_w"] for c in cs], "^:", label="bike display")
        ax.set_title(f"{cv:.0f} rpm")
        ax.set_xlabel("R")
        ax.grid(alpha=0.3)
    axes[0][0].set_ylabel("W")
    axes[0][0].legend()
    fig.tight_layout()
    fig.savefig(out_path, dpi=120)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bike", required=True, type=Path)
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--offset", type=float,
                    help="seconds to add to the reference clock (skips auto-alignment)")
    ap.add_argument("--r-step", type=float, default=10.0)
    ap.add_argument("--cad-step", type=float, default=20.0)
    ap.add_argument("--min-samples", type=int, default=30,
                    help="steady samples a cell needs to count")
    ap.add_argument("--source", default=None, help="label stored in the table")
    args = ap.parse_args()

    bike = load_bike(args.bike)
    ref = load_ref(args.ref)
    offset = args.offset
    if offset is None:
        offset = find_offset(bike, ref)
        if offset is None:
            print("Reference has no cadence; assuming clocks agree. "
                  "Pass --offset if they do not.")
            offset = 0.0
        else:
            print(f"Clock offset (reference + {offset:+.0f} s = bike)")

    p_ref = resample(ref["t"] + offset, ref["p"], bike["t"])
    mask = steady_mask(bike["r"], bike["cad"]) & np.isfinite(p_ref) & (bike["cad"] > 20)
    print(f"{mask.sum()} steady samples of {len(mask)}")
    if mask.sum() == 0:
        sys.exit("No steady overlap between the two recordings. Check --offset.")

    r, cad = median_filter(bike["r"])[mask], bike["cad"][mask]
    p_model = np.array([brake_power(a, b) for a, b in zip(r, cad)])
    cells = bin_cells(r, cad, p_ref[mask], bike["p_bike"][mask], p_model,
                      args.r_step, args.cad_step, args.min_samples)
    if not cells:
        sys.exit("No cell reached --min-samples; hold each grid point longer.")

    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out / "cells.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(cells[0].keys()))
        w.writeheader()
        w.writerows(cells)

    stats = report(cells)
    source = args.source or f"{args.ref.name}, {datetime.now():%Y-%m-%d}"
    table = build_table(cells, source)
    (args.out / "power_table.json").write_text(table_json(table))
    if stats:
        (args.out / "summary.json").write_text(json.dumps(stats, indent=1))
    plot(cells, args.out / "reference_vs_model.png")
    if table["filled"]:
        print(f"\n{len(table['filled'])} grid cells were not measured and were "
              f"filled from the model: {table['filled']}")
    print(f"\nWrote {args.out / 'power_table.json'}. Paste its contents into "
          "Settings > Measured power in the app.")


if __name__ == "__main__":
    main()
