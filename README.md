# IC8 Power Fix

If you ride a **Schwinn 800IC / IC8 / IC4 / Bowflex C6** indoors and pair it
to Rouvy, MyWhoosh, Zwift, or Garmin, your power numbers are roughly **15–20%
high**. This project is a small Flutter bridge that reads the bike's BLE
output, applies a physics-based correction, and re-broadcasts the corrected
number as a virtual cycling power meter — so the indoor TSS your training app
records lines up with what an outdoor power meter would read.

It's specific to the IC8 family, but the same approach works for any
mechanical eddy-current brake bike that broadcasts resistance over FTMS.

---

## The problem

The IC8 broadcasts power as a function of cadence and the resistance dial:

    P_IC8 ≈ 0.019 · R^0.83 · cad^1.5

(This formula was reverse-engineered from a handful of BLE packets and is
within ±2% of what the bike actually broadcasts. R is the dial setting 0–100,
cad is rpm.)

Two problems with that:

1. **The exponents are off.** Real eddy-current physics gives `P ∝ ω²`, not
   `cad^1.5`. The IC8 firmware undershoots cadence sensitivity at low cad
   and overshoots at high cad.
2. **Absolute scale is off.** Comparing intensity-matched outdoor rides
   (4iiii crank meter) to indoor IC8 sessions at matched HR + cadence, the
   bike reads roughly 15–20% high overall, more in some zones.

For one rider here, indoor sessions that the IC8 reported at NP ≈ 258 W
correspond to roughly NP ≈ 218 W of real rider work — about a 40 W gap, and
enough to skew weeks of TSS in Garmin Connect.

Here's the gap across the operating envelope. Dashed lines are what your
training app sees from the bike; solid lines are what the bridge re-broadcasts:

![IC8 vs corrected power curves](docs/figures/power_curves.png)

The gap is largest at low cadence (where the IC8's `cad^1.5` overshoots
real `cad²` physics) and at high R (where the absolute scale is most off).
At a typical hard zone of R ≈ 30 / cad ≈ 90, the bike reads ~273 W and the
bridge says ~229 W — about 16% lower, in line with the NP gap above.

## The fix: physics-based correction

For a mechanical eddy-current brake the dissipation has a clean form:

    P_steady = (a·R + b) · I · ω²

where `a` and `b` are properties of the brake/friction system, `I` is the
flywheel's effective rotational inertia at the crank, and `ω` is crank
angular velocity in rad/s. There's also a kinetic-energy term that matters
during accelerations and decelerations:

    P_KE = I · ω · dω/dt

Total rider input is the sum:

    P_corrected = (a·R + b) · I · ω²  +  I · ω · dω/dt

If `dω/dt = 0` (steady cadence) the second term vanishes and you get pure
steady-state power. During a sprint launch the second term adds the work
needed to accelerate the flywheel; during a coastdown it subtracts and the
total goes to zero (because the rider is no longer doing work).

### Where the constants come from

**`a` and `b` from spin-downs.** During a coastdown (rider stops pedaling,
flywheel decelerates by itself), the equation of motion is `I·dω/dt = -(a·R
+ b)·ω`, which gives `ω(t) = ω₀·exp(-λ(R)·t)` with `λ(R) = (a·R + b)/I`. So
each coastdown gives one λ value at one R. Plot λ vs R, fit a line, and
you've separated brake from friction — independent of `I`:

![Spin-down calibration](docs/figures/spindown_fit.png)

That's seven clean coastdowns at R = 5, 14, 17, 24, 25, 32, 33. The line
fit gives **a = 0.00673 / (s·R-unit)** and **b = 0.0320 / s**. Friction
alone would let the flywheel decay with τ = 1/b ≈ 31 s; at R = 50 the brake
adds about 10× more decay than friction.

**`I` from one outdoor anchor.** With λ(R) known, the only remaining
unknown is `I`. We pin it from a single matched-effort outdoor reference:
real power from the 4iiii at known HR + cadence pegs the absolute scale,
and `I = 11.0 kg·m²` (effective at the crank) makes it self-consistent.
This is the weakest link in the pipeline — it's a single anchor — but
order-of-magnitude it's right (a 9 kg flywheel geared up at the crank
gives an effective inertia in this range).

## Does it actually behave correctly? Two reality checks

### Indoor: the model decomposes a sprint cleanly

Here's a real BLE-logged spin-up at R = 28 from the calibration ride —
cadence 0 → 67 rpm in about 8 seconds, then held steady for 8 more:

![Indoor surge-and-hold](docs/figures/indoor_surge.png)

The blue area is the steady term `(aR+b)·I·ω²`, the red area is the
positive KE term `I·ω·dω/dt`. The solid red line (their sum) is what we
re-broadcast. The dashed line is the IC8's own broadcast.

What you're looking at:

- **During the spin-up:** KE adds 50–80 W on top of the steady term while
  the rider is accelerating the flywheel. That's real work being done.
- **Once cadence holds:** KE collapses to ≈ 0 within 1–2 seconds, and the
  corrected power settles at ≈ 120 W — the new (higher) steady-state
  dissipation at cad 67.
- **The IC8 broadcast** (dashed) tracks the same shape but settles ~30%
  high during the hold, exactly the inflation we're correcting for.

### Outdoor: a 4iiii crank meter shows the same shape

The same physics governs an outdoor bike — bike + rider mass is the
"flywheel," air drag and rolling resistance are the "brake." A 4iiii
crank-arm meter records what the rider's legs actually produce. Here's a
short surge from a snow ride:

![Outdoor surge-and-hold](docs/figures/outdoor_surge.png)

Speed goes from 22 to 33 km/h over 7 seconds. Power peaks near 390 W
during the acceleration, then settles at ~157 W to hold the new pace. Same
bump-during-spin-up, settle-on-hold pattern as the indoor plot — measured
by a completely different sensor on a completely different system. The
physics is the same; the model is just one scaled instance of it.

## What the bridge does

```
   IC8 bike                   bridge phone                 training app
 ─────────────              ──────────────────             ──────────────
  FTMS 0x1826 ──BLE──▶  Read R, cad, power, HR
                        Correct: P_real = (aR+b)·I·ω² + I·ω·dω/dt
                        Re-broadcast as ────FTMS + Cycling────▶ Rouvy /
                                              Power 0x1818      MyWhoosh /
                                                                Garmin /
                                                                Zwift
```

The phone running the bridge connects to your IC8 over BLE (it shows up as
"Nautilus,Inc - IC Bike" or similar, depending on firmware), reads the
FTMS Indoor Bike Data characteristic, runs the correction at every sample,
and presents itself to your iPad/Apple TV/computer as a virtual FTMS bike
+ cycling power meter named **"IC Bike (corrected)"**. Your training app
pairs to the bridge instead of the bike.

The bridge is a Flutter app and ships with:

- Auto-reconnect with backoff if the BLE link drops mid-ride.
- Wakelock so the bridge phone stays awake (iOS background-BLE modes are
  declared in `Info.plist`; on Android, keep the phone on the bridge UI).
- An FTMS Control Point stub that politely tells apps "this is a manual
  brake, ERG/sim is not supported," so they fall back to power-only mode
  cleanly instead of nagging.

There's no resistance control — the IC8 has a manual dial. ERG mode isn't
possible regardless of what you pair it to.

## Limitations and honest caveats

- **The inertia anchor is one rider, one outdoor session.** If your IC8
  flywheel mass differs (different model year, different generation), the
  scale could be off by a few percent. Re-pinning `I` against your own
  outdoor power meter is the right move if you have one.
- **High-cadence cap.** FTMS broadcasts cadence at 0.5 rpm resolution but
  the IC8 saturates at 125 rpm. Above the cap, the bridge falls back to
  CSC-derived cadence if the bike exposes the CSC service; otherwise it
  clamps to the cap (and slightly underestimates real sprint power).
- **No outdoor power meter? Then the absolute scale is approximate.** The
  *shape* of the correction (cad², R-linear λ) is physics-derived and
  solid; the multiplicative offset depends on the inertia anchor.

## Repository layout

    bridge/                    Flutter app — runs on iOS/Android, the bridge itself
    bridge/lib/ble/            BLE central + peripheral
    bridge/lib/physics/        the corrector (mirrors analysis/correct_power.py)
    analysis/                  Python scripts: parsing BLE logs, fitting constants
    analysis/spindown_fit.py   produces a, b
    analysis/correct_power.py  applies the correction to a parsed BLE log
    analysis/plot_surge_examples.py  generates the figures above
    data/calibration/          BLE logs from a calibration ride (used to fit a, b)
    data/                      outdoor FIT files (used as anchors / validation)
    docs/figures/              README plots

## How to build and run

    cd bridge
    flutter pub get
    flutter run                      # connect a phone first

In the app: tap the shield icon to authorize BLE permissions, then **Scan**,
tap your bike when it appears, and the bridge starts. From your training
app on a separate device, pair to **"IC Bike (corrected)"** as a power
meter (and FTMS bike if your app supports it). Done.

## Data flow for the curious

If you want to redo the calibration from scratch:

1. Capture a BLE log of a coastdown ride with nRF Connect (~5 spin-downs
   from cad ≥ 80 at different R values).
2. `python3 analysis/parse_nrf_log.py raw.txt > spin_downs.csv`
3. `python3 analysis/spindown_fit.py` → emits `λ(R) = a·R + b`
4. Pin `I` against one outdoor session at matched intensity.
5. Update `bridge/lib/physics/constants.dart` with the new values.

Tests live in `bridge/test/corrector_test.dart` — `flutter test` should
pass after any constants change.
