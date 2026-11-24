# Reference session: putting real watts on the IC8

Spin-downs fix the *shape* of the IC8's brake curve but can only ever
measure torque divided by inertia: a stronger brake on a heavier flywheel
slows down identically. One absolute measurement settles the scale. This
page describes three ways to get it, from most to least precise, and how
to feed the result into the app.

A power meter (Option A) also tests the shape. Its outcome is one of:

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

## Option B: hanging mass (no power meter, no cost)

A known mass applying a known torque is the absolute quantity spin-downs
lack. It pins the crank inertia, which is the one unknown that sets the
model's scale. The spin-downs cover riding cadences up to about 100 rpm
for R ≤ 74 and 60 to 90 rpm for R ≥ 81, so with the inertia measured
the output is anchored over that whole range. What it does not test is
the brake shape beyond it, and the warm-disc drift; only Option A does.

### Setup

- A mass you can weigh, 5 to 10 kg (a kettlebell, a bag of books, a
  bucket of water). Weigh it on a bathroom scale.
- A cord or strap, girth-hitched round one crank arm right next to the
  pedal. Do not stand the mass on the pedal: the pedal turns freely on
  its axle and tips it off. Hung from the arm, the loop cannot slide off
  either: as the crank swings down, the pull has an outward component
  along the arm that holds the loop against the pedal.
- The crank length: pedal spindle centre to bottom bracket centre,
  measured with a ruler, about 0.17 m.
- The same camera setup as the spin-down videos, pointed at the crank,
  and `analysis/track_crank.py` to extract ω(t). 60 fps is enough; the
  swing takes one to two seconds.

### Record

1. Set R to 0. Hang the mass from the crank arm with that arm
   horizontal and forward, hold the crank still, start filming, let go.
   The crank swings down half a turn and the mass drops twice the crank
   length, so nothing reaches the floor. Keep feet clear.
2. Three releases from the same position. The spread of the fitted
   inertia is its uncertainty.

### Analyse

```
python analysis/track_crank.py drop1.mov --output drop1.csv
python analysis/falling_mass.py --track drop1.csv --mass 8.0 \
    --radius 0.17 --drum pedal --R 0
```

The script finds the release and the bottom of the swing by itself, fits
a single scale `s` that multiplies the model's torque and inertia
together, and prints the implied crank inertia and the Power scale slider
value. If you released from somewhere other than horizontal, pass
`--start-deg` with the angle past horizontal.

A cord wound round the flywheel rim or a drum on the crank side works
too (`--drum flywheel --radius <rim radius> --ratio 4.5`), but the belt
ratio divides the travel: a 2 m drop turns the crank under half a turn in
about a second and needs 120 or 240 fps video.

Ignored in the analysis: cord mass and the small sideways tilt of the
cord as the pedal moves. The mass's own motion is included.

## Option C: your threshold power (no hardware)

If you know your FTP from a real power meter, usually on your road bike,
your body can carry that number to the IC8. Any gap between your indoor
and outdoor threshold (heat, cooling, position, motivation) and your
form on the day land in the result, and it holds at the dial positions
and cadences you rode; the spin-downs supply the shape elsewhere. It
needs nothing you do not have.

### Record

Ride an all-out 20-minute effort on the IC8, or a hard hour, with your
training app recording, at the dial positions you normally ride. Pair
the app either to the bike itself or to "IC Bike (corrected)"; for the
latter note the Power scale it was set to. Export the `.fit` file. Rides
you already have work if they hold such an effort.

### Analyse

```
python analysis/ftp_anchor.py --indoor ftp_test.fit --ftp 200
```

For a ride recorded through the bridge, add `--source bridge
--scale-used 1.0` (the Power scale during the ride, 1.0 = 100%). The
bike's own files carry power and cadence but not the dial position, so
the script recovers R by inverting the bike's broadcast formula, which
is within 2.5% of the bike's number in every R band and costs up to
about 5% in model power. Add `--outdoor road.fit ...` to print your outdoor
best averages beside the indoor ones; without `--ftp` the outdoor files
supply the threshold, which only means something if they hold an
all-out effort of 20 minutes or more.

The script prints the best averages over 5 to 60 minutes for the bridge
model at 100%, the number the training app showed and the outdoor files,
the indoor threshold max(0.95 × best 20 min, best 60 min), and the Power
scale that makes it equal your FTP.

### Load

Set Settings, Power scale to the printed percentage.

## Where the default comes from: flywheel geometry

The app's default scale is the flywheel's inertia. The crank inertia is
g² times the flywheel's, with g the flywheel turns per crank turn. On
the reference bike the flywheel is a 5 mm aluminium disc of radius
23 cm with a weight ring on each face, ruler-measured at radius 14 to
18 cm by 2.03 cm and 13 to 17 cm by 1.52 cm. The ring material sets the
answer:

| Rings | Flywheel | Crank inertia at g = 4.5 | Power scale |
| --- | --- | --- | --- |
| lead | 18.0 kg | 9.09 kg·m² | 136% |
| steel (shipped) | 13.1 kg | 6.66 kg·m² | 100% |
| cast iron | 12.2 kg | 6.21 kg·m² | 93% |

A fridge magnet sticks to the reference bike's rings, away from the
brake magnets at the rim, so they are not lead. It does not tell steel
from cast iron; the app ships steel, which the threshold check in
Option C matches within 2%. Power scale is the crank inertia divided by
6.66.

To check your own unit, try the magnet, and with a mark on the flywheel
turn the crank 10 times and count flywheel turns to the nearest eighth:
that gives g to about 0.3%, and g enters squared. The ring heights are
ruler upper bounds with chamfered edges, and the hub and pulleys are
left out, so the geometry holds to roughly 10%; A, B and C measure the
scale directly and take precedence.

## What the numbers mean afterwards

| Result | Action |
| --- | --- |
| Ratio flat, mean within 5% of 1 | Nothing to change. |
| Ratio flat, mean elsewhere, or Option B or C gives a different scale | Set the Power scale slider to the ratio, to `s` or to the printed percentage. To change the shipped default instead, multiply the inertia, the brake damping c and τ_c by that factor together in `analysis/bridge_model.py`, `analysis/fit_spindowns.py` and the `Calibration` defaults in `bridge/lib/physics/calibration.dart`; spin-downs fix only their ratios, so changing one alone breaks the fit. |
| Ratio trends with R | Load the table in the app. The spin-down model stays as documentation of the shape it predicted. |
| Warm cells lower than cold | Note the size. A thermal term is the next model change if it exceeds the scatter. |
