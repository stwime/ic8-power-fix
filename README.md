# IC Bridge

If you ride a Schwinn IC8/IC4 (or rebadged Bowflex C6/C7) and pair it to
Rouvy, MyWhoosh, Zwift, or Garmin, the broadcast power numbers can be way
off — and inconsistently so. Some riders see an exact match against a
crank meter or pedal-based meter; others see 50–100 W gaps in the same
zones. IC Bridge is a small Flutter app that reads the bike's BLE output,
applies a physics-based correction, and re-broadcasts the result as a
virtual FTMS power meter your training apps can pair to.

## Why use this

- **Physics-derived correction, not a flat scale factor.** The model is
  eddy-current brake dynamics: $P = \lambda(R)\,I\,\omega^2 + I\,\omega\,\frac{d\omega}{dt}$.
  It responds correctly to transients — sprints, coastdowns, low-cadence
  grinding — instead of just shifting every number by a percentage.
- **Calibrates to your specific bike.** Auto-calibrate (Settings →
  Auto-calibrate) fits the brake curve on-device in 5–10 minutes. If you
  have an outdoor power meter, the Power scale slider pins the absolute
  scale against ground truth.
- **No firmware mods, standard FTMS out.** The bike doesn't change. The
  bridge re-broadcasts as a standard FTMS power meter, so any training
  app that pairs to FTMS works.
- **Production-grade plumbing.** Auto-reconnect with backoff if the BLE
  link drops, wakelock so the bridge phone stays awake, and an FTMS
  Control Point stub that politely tells apps "manual brake — no ERG/sim"
  so they fall back to power-only mode cleanly.

## Supported models

The Schwinn IC8 (UK/EU), IC4 (US), and Bowflex C6/C7 are the same
underlying hardware. The defaults shipped in the app were fitted on an
IC8 and apply directly.

| Model                    | Status                                          |
|--------------------------|-------------------------------------------------|
| **Schwinn IC8 / IC4**    | Reference platform — ships calibrated           |
| **Bowflex C6 / C7**      | Same hardware — ships calibrated                |
| Other FTMS indoor bikes  | Should work if they broadcast resistance over FTMS — run Auto-calibrate first, then verify scale against an outdoor power meter if you have one |

## Why the bike's numbers can't be trusted

The IC8 broadcasts power as a function of cadence and the resistance dial:

$$P_{\text{IC8}} \approx 0.019 \cdot R^{0.83} \cdot \text{cad}^{1.5}$$

Two issues:

1. **The cadence exponent is wrong.** Real eddy-current physics gives
   $P \propto \omega^2$, not $\text{cad}^{1.5}$.
2. **The absolute scale isn't fixed.** Whether the bike reads high, low,
   or on the money depends on the unit, the dial calibration, and the
   operating point. That's why forums are confusing — there's no single
   offset that fits every rider's experience.

The *shape* of the gap is consistent though, even when the magnitude
isn't. Dashed lines are what the bike broadcasts; solid lines are what
the bridge re-broadcasts:

![IC8 vs corrected power curves](docs/figures/power_curves.png)

The two curves disagree both on cadence-scaling (IC8 uses
$\text{cad}^{1.5}$, the physics gives $\text{cad}^2$) and on
$R$-scaling (the IC8's $R^{0.83}$ is a soft sub-linear growth, while
the real eddy-brake $\lambda(R)$ rises sharply through the middle of
the dial before saturating at the high end — see the spin-down plot
below). They cross near $R \approx 25$, $\text{cad} \approx 60$; below
that the bridge reads lower than the bike, above it the bridge reads
higher. The exact crossover depends on the absolute scale of your
unit, which the **Power scale** slider lets you pin against an
external reference.

## The fix

For an eddy-current brake the steady-state dissipation has a clean form:

$$P_{\text{steady}} = \lambda(R)\,I\,\omega^2$$

$\lambda(R)$ is the per-radian dissipation rate at dial setting $R$,
$I$ is the flywheel's effective rotational inertia at the crank, $\omega$
is crank angular velocity in rad/s. There's also a kinetic-energy term
that matters during accelerations and decelerations:

$$P_{\text{KE}} = I\,\omega\,\frac{d\omega}{dt}$$

Total rider input is the sum:

$$P_{\text{corrected}} = \lambda(R)\,I\,\omega^2 + I\,\omega\,\frac{d\omega}{dt}$$

At steady cadence the second term is zero. During a sprint launch it
adds the work to spin up the flywheel; during a coastdown it subtracts
and the total goes to zero (the rider isn't doing work).

### Where the constants come from

**$\lambda(R)$ from spin-downs.** With no rider input, the flywheel
decelerates as $\omega(t) = \omega_0\,e^{-\lambda(R)\,t}$. The brake is
a permanent magnet that the dial moves toward the flywheel: eddy-current
torque scales with $B^2(d)$ where $B$ is field strength and $d$ is the
magnet-flywheel gap. As $d \to 0$ the field of any finite magnet is
bounded above (it can't exceed the magnet's surface field), so
$\lambda(R)$ doesn't keep climbing — it saturates. The Hill form
captures the steep mid-dial transition and the high-$R$ asymptote:

$$\lambda(R) = \beta + \alpha \cdot \frac{R^p}{R^p + R_c^p}$$

Fit on per-revolution $\omega(t)$ trajectories from a hand-curated set
of 42 video-tracked spindowns spanning $R = 0$ to 93
(`analysis/fit_hill.py`). $\beta$ is pinned to the directly-measured
median per-segment $\hat{\lambda}$ at $R = 0$ rather than left to
float, since the Hill shape can't simultaneously hit both the $R = 0$
anchor and the steep $R = 10\text{–}30$ rise; $R < 10$ isn't a
practical riding region so pinning $\beta$ makes the low-$R$ end
physically honest without measurably hurting RSS at $R \geq 15$.

- $\alpha = 2.36\ \text{s}^{-1}$ — saturation amplitude (per-bike)
- $\beta = 0.0396\ \text{s}^{-1}$ — residual drag at $R = 0$ (per-bike)
- $R_c = 54.6$ — half-saturation dial position (geometry, fixed across bikes)
- $p = 3.41$ — transition sharpness (geometry, fixed across bikes)
- asymptote $\alpha + \beta \approx 2.40\ \text{s}^{-1}$

![Spin-down calibration](docs/figures/spindown_fit.png)

The Hill curve passes cleanly through the per-segment $\hat{\lambda}$
cloud across the full dial range. Fit quality on the same dataset is
9.2× lower weighted RSS than a power-law $\alpha R^p + \beta$ — the
saturation at high $R$ is a real feature of the data, not just a fit
preference. The previous power-law fit underestimated $\lambda$ at
$R \geq 30$ by 2–4× because its constant log-log slope can't bend
over, and earlier mixed-source fits got dragged low by BLE/CSC
high-$R$ coastdowns where the rider was lightly pedaling during the
"decay" tail. The video-only curated set sidesteps both problems.

Auto-calibrate fits $\alpha$ and $\beta$ per-bike against the same
saturating design. $R_c$ and $p$ are held fixed because they
parameterize the brake's gap-vs-dial geometry, which is identical
across units of the same model.

**$I$ from outdoor anchors.** With $\lambda(R)$ known, the only unknown
is $I$. Matching outdoor 4iiii crank-meter sessions to indoor sessions
in HR + cadence bins back-solves $I \approx 9.14\ \text{kg}\,\text{m}^2$
(`analysis/pin_inertia.py`). Sanity check: assuming $I_{\text{flywheel}}
\approx 0.29\ \text{kg}\,\text{m}^2$ for a typical IC8 flywheel
(~18 kg, ~0.18 m radius), the implied flywheel-to-crank gear ratio is
$\sqrt{9.14/0.29} \approx 5.6{:}1$, in the ballpark of the IC8's
reported ~6:1 gearing. The in-app **Power scale** slider absorbs any
leftover offset against an external reference.

## Reality check: the model decomposes a sprint cleanly

A BLE-logged spin-up at $R = 28$ — cadence 0 → 67 rpm in 8 seconds,
then held steady for 8 more:

![Indoor surge-and-hold](docs/figures/indoor_surge.png)

Blue area is the steady term $\lambda(R)\,I\,\omega^2$, red area is
the KE term $I\,\omega\,\frac{d\omega}{dt}$. KE adds 50–80 W during the
spin-up, then collapses to ≈ 0 within 1–2 seconds of cadence holding,
settling at the steady-state dissipation at cad 67. The same shape
shows up on a 4iiii crank meter during an outdoor acceleration —
different sensor, different system, same physics.

## What the bridge does

```
  ┌──────────────┐         ┌──────────────────────────┐         ┌──────────────┐
  │  indoor bike │   BLE   │       bridge phone       │   BLE   │ training app │
  │              ├────────▶│                          ├────────▶│              │
  │  FTMS 0x1826 │ R, cad, │  P = λ(R)·I·ω²           │  FTMS + │  Rouvy       │
  │              │ power,  │      + I·ω·dω/dt         │  Power  │  MyWhoosh    │
  │              │  HR     │                          │  0x1818 │  Zwift       │
  │              │         │                          │         │  Garmin      │
  └──────────────┘         └──────────────────────────┘         └──────────────┘
```

The phone running the bridge connects to your bike over BLE (it shows up
as "Nautilus,Inc - IC Bike" or similar), reads the FTMS Indoor Bike Data
characteristic, runs the correction on every sample, and presents itself
as a virtual FTMS bike + cycling power meter named **"IC Bike
(corrected)"** by default (configurable in Settings). Your training app
pairs to the bridge instead of the bike.

There's no resistance control — the bike has a manual dial, so ERG mode
isn't possible regardless of what you pair it to.

## Limitations

- **Absolute scale depends on your unit.** The *shape* of the correction
  ($\text{cad}^2$, saturating $\lambda(R)$) is physics-derived and solid.
  The multiplicative offset depends on your bike's dial calibration and
  on the inertia anchor — Auto-calibrate fits the first; the Power scale
  slider absorbs the second.
- **High-cadence cap.** The IC8 saturates broadcast cadence at 125 rpm.
  Above the cap, the bridge falls back to CSC-derived cadence if the
  bike exposes CSC; otherwise it clamps and slightly underestimates
  sprint power.
- **Cadence exponent locked at $\omega^2$.** The eddy-brake low-speed
  limit gives $\tau \propto \omega$ and so $P \propto \omega^2$, and the
  joint trajectory fit on our spindowns is consistent with that across
  the observed $\omega$ range. If your bike's brake actually transitions
  out of the low-speed regime at a cadence inside your riding envelope
  (saturation, where $P \propto \omega$ at high $\omega$), the bridge
  will overshoot at high cadence. Disambiguating that needs bilateral
  indoor truth — a power meter on the cranks during an indoor cadence
  sweep at a couple of $R$ values.

## Repository layout

```
bridge/                          Flutter app — the bridge itself
bridge/lib/ble/                  BLE central + peripheral
bridge/lib/physics/              corrector + coastdown fit (Dart port of
                                 spindown_fit.py — what Auto-calibrate runs)
analysis/parse_nrf_log.py        nRF Connect log -> CSV (FTMS + CSC joined)
analysis/curate_spindowns.py     interactive in/out-marker tool over BLE +
                                 video coastdown candidates
analysis/aggregate_spindowns.py  merges curated bounds into one per-rev ω(t)
                                 dataset (data/calibration/all_spindowns.csv)
analysis/fit_hill.py             Hill-form λ(R) fit on the curated dataset
                                 (video-only, β pinned to R=0 measurement)
analysis/pin_inertia.py          outdoor 4iiii FIT files -> I_crank
analysis/correct_power.py        offline reprocessor (Python mirror of the
                                 Dart corrector)
analysis/plot_surge_examples.py  generates the README figures
data/calibration/                BLE logs + crank videos used to fit defaults
docs/figures/                    README plots
```

## Build and run

```
cd bridge
flutter pub get
flutter run                      # connect a phone first
```

In the app: if a Bluetooth icon appears in the top bar, tap it to grant
permissions; then tap **Find bike**, tap your bike, and the bridge starts.
From your training app on a separate device, pair to **"IC Bike
(corrected)"** as a power meter and as an FTMS bike.

If your numbers feel off, open Settings → **Auto-calibrate** to fit the
brake curve to your bike (5–10 minutes, on-device). If you have an
external power meter, use the **Power scale** slider on the same screen
to pin the absolute scale.

Tests live in `bridge/test/` — `flutter test` should pass after any
default changes.
