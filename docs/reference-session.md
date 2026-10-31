# Reference session: putting real watts on the IC8

Spin-downs fix the *shape* of the IC8's brake curve but can only ever
measure torque divided by inertia. One absolute measurement on this bike
settles the scale. This page describes the two ways to get it and how to
feed the result into the app.

Either way the outcome is one of:

- **The ratio reference/model is flat across the dial.** The physics shape
  is right. Set the Power scale slider to the ratio, or load the table.
- **The ratio changes with the dial.** The shape is wrong for this bike.
  Load the measured table; it replaces the physics model entirely.

## Option A: pedal power meter (preferred)

Any pedal-based meter fits the IC8's standard 9/16" pedal threads. A
single-sided unit is fine, the comparison is against the same rider on
the same bike. Pair it to a head unit or phone app that exports a `.fit`
file with power and cadence.

### Record

1. On a laptop next to the bike, log the bike's own stream:

   ```
   pip install -r analysis/requirements.txt
   python analysis/ic8_logger.py --output data/calibration/reference/session.csv
   ```

   The logger takes the bike's single BLE connection, so the bridge app
   is not used during the session. Model power is recomputed offline from
   the logged resistance and cadence.
2. Start the power meter recording. Both clocks are aligned afterwards by
   cross-correlating cadence, so exact synchronisation is not needed.
3. Ride the grid. For each dial position **R = 10, 20, … 90** hold
   **60, 80 and 100 rpm** for 90 s each. Keep cadence within ±4 rpm; the
   first 15 s of each block are discarded anyway. Skip cells you cannot
   hold; the script fills them from the nearest measured ratio and lists
   them.
4. If you have the legs, repeat three or four cells at the end of the
   session. The brake disc warms up during a ride and a drop of several
   percent at the same R and cadence is plausible. The difference between
   the cold and warm readings is the thermal effect the model ignores.
5. Stop both recordings and export the `.fit` file.

### Analyse

```
python analysis/fit_reference.py \
    --bike data/calibration/reference/session.csv \
    --ref data/calibration/reference/ride.fit \
    --out data/calibration/reference/2026-11-14
```

The script prints one line per grid cell with reference, model and bike
display watts, the weighted mean ratio, its trend in R, and a verdict.
It writes `cells.csv`, `reference_vs_model.png` and `power_table.json`.

### Load

Open the app, Settings, Measured power, Paste table, and paste the
contents of `power_table.json`. The card confirms the number of points
and the covered range. The Power scale slider is hidden while a table is
active because the table is already in measured watts. Remove the table
to return to the physics model.

## Option B: falling mass (no power meter)

A known mass on a cord wound round a drum of known radius applies a known
torque to the drivetrain. That is the absolute quantity spin-downs lack.
It pins the crank inertia, which is the one unknown that sets the model's
scale. It does not test the brake shape at high R; only Option A does.

### Setup

- A cord, a mass you can weigh (a filled bottle or a kettlebell, 3 to
  8 kg), and a drum of known radius. The flywheel rim is the natural drum
  if enough of it is reachable to wind a few turns; otherwise fix a short
  length of pipe to the crank side. Measure the radius where the cord
  leaves the drum.
- The same camera setup as the spin-down videos, pointed at the crank,
  and `analysis/track_crank.py` to extract ω(t). Shoot at 120 or 240 fps:
  the belt ratio divides the cord travel, so a 2 m drop turns the crank
  less than half a turn and is over in about a second.
- Count the belt ratio once: film the flywheel and crank together and
  count flywheel turns per crank turn. The repo uses 4.5.

### Record

1. Set R to 0. Wind the cord so the mass hangs just off the floor at
   full height, hold the crank, start filming, release. Let the mass run
   the full drop. Stop it on a cushion, not the floor, and keep hands and
   feet clear of the cord.
2. Three drops; the spread of the fitted inertia is its uncertainty.
3. Optionally one or two drops at a higher R as a consistency check. The
   brake torque at the low speeds a short drop reaches is small next to
   the applied torque, so these constrain the scale only weakly.

### Analyse

```
python analysis/track_crank.py drop_R0.mov --output drop_R0.csv
python analysis/falling_mass.py --track drop_R0.csv --mass 5.0 \
    --radius 0.19 --drum flywheel --ratio 4.5 --R 0
```

The script fits a single scale `s` that multiplies the model's torque and
inertia together and prints the implied crank inertia and the Power scale
slider value.

Ignored in the analysis: cord mass, pulley friction if a pulley is used,
and the drum radius uncertainty, which enters linearly in the applied
torque and squared in the mass's own contribution to the inertia. With
5 kg on the flywheel rim that contribution is about half the crank
inertia, so measure the radius carefully or use a lighter mass.

## What the numbers mean afterwards

| Result | Action |
| --- | --- |
| Ratio flat, mean within 5% of 1 | Nothing to change. |
| Ratio flat, mean elsewhere | Set the Power scale slider to the mean, or set `I_CRANK` in `analysis/fit_wouterse.py` and `defaultICrank` in `bridge/lib/physics/calibration.dart` to the implied inertia. |
| Ratio trends with R | Load the table in the app. The spin-down model stays as documentation of the shape it predicted. |
| Warm cells lower than cold | Note the size. A thermal term is the next model change if it exceeds the scatter. |
