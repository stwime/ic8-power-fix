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

The gap is largest at low cadence (where $\text{cad}^{1.5}$ overshoots
$\text{cad}^2$) and at high $R$. Above cad ≈ 100 the bridge can read
slightly higher than the bike — see the structural-limit note below.

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
decelerates as $\omega(t) = \omega_0\,e^{-\lambda(R)\,t}$. Each coastdown
gives one $\lambda$ at one $R$, fit on the per-revolution CSC event
timestamps (1/1024 s precision) so high-$R$ / short coastdowns aren't
dominated by ~0.5 s of BLE-arrival jitter.

![Spin-down calibration](docs/figures/spindown_fit.png)

The dashed grey line is a linear $\lambda(R) = a\,R + b$ — it matches at
low to mid $R$ but undershoots high $R$ systematically. The brake
response is nonlinear: the dial moves a permanent magnet toward the
flywheel, and the eddy-current torque scales with $B^2(d)$ where $B$ is
field strength and $d$ is the magnet-flywheel gap. Far-field
$B \propto 1/d^k$ with $k \approx 3\text{–}6$, so $B^2$ is a power-law
in gap and $\lambda(R)$ follows a Hill form:

$$\lambda(R) = \alpha \cdot \frac{R^p}{R^p + R_c^p} + \beta$$

$p$ is the effective power-law exponent and $R_c$ is the half-max knee
(the dial position where $\lambda - \beta$ reaches $\alpha/2$). Pooled
fit on 31 coastdowns spanning $R = 1\ldots 80$:

- $\alpha = 0.207\ \text{s}^{-1}$ — Hill brake amplitude
- $\beta = 0.034\ \text{s}^{-1}$ — residual drag at $R = 0$
- $R_c = 38.5$ — dial half-max knee
- $p = 1.90$ — Hill exponent (held fixed across bikes)

The Hill form cuts weighted RSS 24% over the saturating
$\alpha\,(1 - e^{-R/R_c}) + \beta$ and 27% over linear, with bucket
residuals flat across all $R$ buckets to within $\pm 0.005\ \text{s}^{-1}$.
Auto-calibrate fits $\alpha$, $\beta$, and (when the user's coastdowns
span enough $R$) $R_c$ per-bike; $p$ is held fixed at 1.90 since it
reflects the brake-mechanism geometry, not per-unit calibration
variation.

**$I$ from outdoor anchors.** With $\lambda(R)$ known, the only unknown
is $I$. Matching outdoor 4iiii crank-meter sessions to indoor sessions
in HR + cadence bins back-solves $I \approx 24.5\ \text{kg}\,\text{m}^2$
near typical riding cadence. That implies a flywheel-to-crank gear ratio
of ~9 (with an $\sim 0.29\ \text{kg}\,\text{m}^2$ flywheel), a bit
higher than the documented 6:1 — likely because the back-solve absorbs
unmodelled rolling losses and $\omega$-vs-time shape mismatch. The
in-app **Power scale** slider absorbs leftover offset against an
external reference.

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
  ($\text{cad}^2$, Hill $\lambda(R)$) is physics-derived and solid. The
  multiplicative offset depends on your bike's dial calibration and on
  the inertia anchor — Auto-calibrate fits the first; the Power scale
  slider absorbs the second.
- **High-cadence cap.** The IC8 saturates broadcast cadence at 125 rpm.
  Above the cap, the bridge falls back to CSC-derived cadence if the
  bike exposes CSC; otherwise it clamps and slightly underestimates
  sprint power.
- **Structural limit at high cadence.** Because
  $\text{cad}^2 / \text{cad}^{1.586} \propto \text{cad}^{0.414}$, the
  correction shrinks as cadence grows. If the firmware overstates power
  *more* at high cadence on your bike, this model can't fully represent
  that — a sub-quadratic cadence exponent would be needed, which
  requires bilateral indoor truth across a cadence sweep.

## Repository layout

```
bridge/                          Flutter app — the bridge itself
bridge/lib/ble/                  BLE central + peripheral
bridge/lib/physics/              corrector + coastdown fit (Dart port of
                                 spindown_fit.py — what Auto-calibrate runs)
analysis/parse_nrf_log.py        nRF Connect log -> CSV (FTMS + CSC joined)
analysis/spindown_fit.py         CSV of coastdowns -> α, β, R_c, p (Hill fit
                                 on per-rev CSC event timestamps)
analysis/pin_inertia.py          outdoor 4iiii FIT files -> I_crank
analysis/correct_power.py        offline reprocessor (Python mirror of the
                                 Dart corrector)
analysis/plot_surge_examples.py  generates the README figures
data/calibration/                BLE logs used to fit the defaults
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
