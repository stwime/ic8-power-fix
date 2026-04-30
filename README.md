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
  eddy-current brake dynamics: `P = λ(R)·I·ω² + I·ω·dω/dt`. It responds
  correctly to transients — sprints, coastdowns, low-cadence grinding —
  instead of just shifting every number by a percentage.
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

```
P_IC8 ≈ 0.019 · R^0.83 · cad^1.5
```

Two issues:

1. **The cadence exponent is wrong.** Real eddy-current physics gives
   `P ∝ ω²`, not `cad^1.5`.
2. **The absolute scale isn't fixed.** Whether the bike reads high, low,
   or on the money depends on the unit, the dial calibration, and the
   operating point. That's why forums are confusing — there's no single
   offset that fits every rider's experience.

The *shape* of the gap is consistent though, even when the magnitude
isn't. Dashed lines are what the bike broadcasts; solid lines are what
the bridge re-broadcasts:

![IC8 vs corrected power curves](docs/figures/power_curves.png)

The gap is largest at low cadence (where `cad^1.5` overshoots `cad²`)
and at high R. Above cad ≈ 100 the bridge can read slightly higher than
the bike — see the structural-limit note below.

## The fix

For an eddy-current brake the steady-state dissipation has a clean form:

```
P_steady = λ(R) · I · ω²
```

`λ(R)` is the per-radian dissipation rate at dial setting `R`, `I` is
the flywheel's effective rotational inertia at the crank, `ω` is crank
angular velocity in rad/s. There's also a kinetic-energy term that
matters during accelerations and decelerations:

```
P_KE = I · ω · dω/dt
```

Total rider input is the sum:

```
P_corrected = λ(R) · I · ω²  +  I · ω · dω/dt
```

At steady cadence the second term is zero. During a sprint launch it
adds the work to spin up the flywheel; during a coastdown it subtracts
and the total goes to zero (the rider isn't doing work).

### Where the constants come from

**`λ(R)` from spin-downs.** With no rider input, the flywheel decelerates
as `ω(t) = ω₀·exp(-λ(R)·t)`. Each coastdown gives one λ at one R.

![Spin-down calibration](docs/figures/spindown_fit.png)

The dashed grey line is a linear `λ(R) = a·R + b` — it matches at low to
mid R but diverges sharply above R ≈ 45. The brake response saturates,
which makes physical sense: the dial moves a permanent magnet toward the
flywheel, and magnetic coupling is nonlinear with diminishing returns
once the magnet is close. So we fit a saturating form:

```
λ(R) = α · (1 − exp(−R / R_c)) + β
```

Pooled fit on 31 coastdowns spanning R = 1…80:

```
α = 0.320 / s    (saturating brake amplitude)
R_c = 41.2       (dial saturation knee)
β = 0.032 / s    (residual drag at R = 0)
```

**`I` from outdoor anchors.** With λ(R) known, the only unknown is `I`.
Matching outdoor 4iiii crank-meter sessions to indoor sessions in HR +
cadence bins back-solves `I ≈ 14 kg·m²` near typical riding cadence.
That sits in the middle of what a ~6:1-belted ~18 kg ring-loaded flywheel
would have on physics alone (10–18 kg·m²), so the number is consistent
with the hardware. The in-app **Power scale** slider absorbs leftover
offset against an external reference.

## Reality check: the model decomposes a sprint cleanly

A BLE-logged spin-up at R = 28 — cadence 0 → 67 rpm in 8 seconds, then
held steady for 8 more:

![Indoor surge-and-hold](docs/figures/indoor_surge.png)

Blue area is the steady term `λ(R)·I·ω²`, red area is the KE term
`I·ω·dω/dt`. KE adds 50–80 W during the spin-up, then collapses to ≈ 0
within 1–2 seconds of cadence holding, settling at the steady-state
dissipation at cad 67. The same shape shows up on a 4iiii crank meter
during an outdoor acceleration — different sensor, different system,
same physics.

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
  (cad², saturating λ(R)) is physics-derived and solid. The
  multiplicative offset depends on your bike's dial calibration and on
  the inertia anchor — Auto-calibrate fits the first; the Power scale
  slider absorbs the second.
- **High-cadence cap.** The IC8 saturates broadcast cadence at 125 rpm.
  Above the cap, the bridge falls back to CSC-derived cadence if the
  bike exposes CSC; otherwise it clamps and slightly underestimates
  sprint power.
- **Structural limit at high cadence.** Because `cad² / cad^1.586 ∝
  cad^0.414`, the correction shrinks as cadence grows. If the firmware
  overstates power *more* at high cadence on your bike, this model can't
  fully represent that — a sub-quadratic cadence exponent would be
  needed, which requires bilateral indoor truth across a cadence sweep.

## Repository layout

```
bridge/                          Flutter app — the bridge itself
bridge/lib/ble/                  BLE central + peripheral
bridge/lib/physics/              corrector + coastdown fit (Dart port of
                                 spindown_fit.py — what Auto-calibrate runs)
analysis/parse_nrf_log.py        nRF Connect log -> CSV (FTMS + CSC joined)
analysis/spindown_fit.py         CSV of coastdowns -> α, R_c, β
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
