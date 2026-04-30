# IC Bridge

If you ride one of the indoor bikes listed below and pair it to Rouvy,
MyWhoosh, Zwift, or Garmin, your power numbers are roughly **15–20% high**.
IC Bridge is a small Flutter app that reads the bike's BLE output, applies a
physics-based correction, and re-broadcasts the corrected number as a virtual
cycling power meter your training apps can pair to.

## Supported models

The calibration was fitted on a Schwinn IC8. The bikes below share the same
mechanical platform (eddy-current brake, manual resistance dial, FTMS over
BLE), so the same correction shape applies. The absolute scale may be a few
percent off on bikes other than the IC8 — see "Calibrate to your bike" in the
app's Settings to dial it in.

| Model                    | Notes                                |
|--------------------------|--------------------------------------|
| **Schwinn IC8 / 800IC**  | Reference platform — calibration fit on this |
| **Schwinn IC4**          | Same mechanical platform; recalibrate for best accuracy |
| **Bowflex C6**           | Same hardware as the IC4 under a different brand |
| Other FTMS indoor bikes  | Will work if they broadcast resistance over FTMS; expect to recalibrate |

---

## The problem

The IC8 broadcasts power as a function of cadence and the resistance dial:

```
P_IC8 ≈ 0.019 · R^0.83 · cad^1.5
```

`R` is the dial setting 0–100, `cad` is rpm. The fit is within ±2% of what
the bike actually broadcasts.

Two problems with that:

1. **The exponents are off.** Real eddy-current physics gives `P ∝ ω²`, not
   `cad^1.5`. The IC8 firmware undershoots cadence sensitivity at low cad
   and overshoots at high cad.
2. **Absolute scale is off.** Comparing intensity-matched outdoor rides
   (4iiii crank meter) to indoor IC8 sessions at matched HR + cadence, the
   bike reads roughly 15–20% high overall, more in some zones.

Here's the gap across the operating envelope. Dashed lines are what your
training app sees from the bike; solid lines are what the bridge re-broadcasts:

![IC8 vs corrected power curves](docs/figures/power_curves.png)

The gap is largest at low cadence (where the IC8's `cad^1.5` overshoots
real `cad²` physics) and at high R (where the absolute scale is most off).
At a typical hard zone of R ≈ 40 / cad ≈ 90, the bike reads ~348 W and the
bridge says ~292 W.

## The fix: physics-based correction

For a mechanical eddy-current brake the dissipation has a clean form:

```
P_steady = (a·R + b) · I · ω²
```

where `a` and `b` are properties of the brake/friction system, `I` is the
flywheel's effective rotational inertia at the crank, and `ω` is crank
angular velocity in rad/s. There's also a kinetic-energy term that matters
during accelerations and decelerations:

```
P_KE = I · ω · dω/dt
```

Total rider input is the sum:

```
P_corrected = (a·R + b) · I · ω²  +  I · ω · dω/dt
```

If `dω/dt = 0` (steady cadence) the second term vanishes and you get pure
steady-state power. During a sprint launch the second term adds the work
needed to accelerate the flywheel; during a coastdown it subtracts and the
total goes to zero (because the rider is no longer doing work).

### Where the constants come from

**`a` and `b` from spin-downs.** During a coastdown (rider stops pedaling,
flywheel decelerates by itself), the equation of motion is
`I·dω/dt = -(a·R + b)·ω`, which gives `ω(t) = ω₀·exp(-λ(R)·t)` with
`λ(R) = (a·R + b)/I`. Each coastdown gives one λ value at one R. Plot λ vs
R, fit a line, and you've separated the dial-modulated term from the
dial-independent term — independent of `I`:

![Spin-down calibration](docs/figures/spindown_fit.png)

Fifteen clean coastdowns spanning R = 1, 4, 11, 15, 23, 31, 37, 38, 45.
The line fit gives **a = 0.00573 / (s·R-unit)** and **b = 0.0359 / s** with
τ at R=0 of about 28 s. Above R ≈ 45 the rider can't reach 125 rpm against
the brake, so coastdowns are too short (3–6 s) to fit a clean exponential —
but the linear extrapolation is consistent with pedal-feel up to R=100.

**`b` is residual drag, not classical friction.** Calling `b` "friction"
is a stretch. The dial only goes down to R=1, so we never measure the
brake fully off; `b` is the linear extrapolation to R=0. That extrapolated
y-intercept lumps belt drag, bearing drag, and any residual eddy drag from
the magnet sitting at its mechanical home position (which on a screw-driven
brake is unlikely to be zero distance from the flywheel). If `b` were
Coulomb friction at the magnitude we measure, the R=1 spindown would
decay linearly in time and stop at a finite moment — instead it fits a
clean exponential (r² ≥ 0.95 on every coastdown), which rules out
Coulomb-dominant dynamics. So `b` is velocity-proportional drag the dial
doesn't change.

How the brake/residual split varies with the dial:

| R   | λ total (1/s) | τ = 1/λ (s) | brake share | residual share |
|-----|---------------|-------------|-------------|----------------|
|  1  | 0.042         | 24          | 14%         | 86%            |
| 10  | 0.093         | 11          | 62%         | 38%            |
| 30  | 0.208         | 4.8         | 83%         | 17%            |
| 50  | 0.323         | 3.1         | 89%         | 11%            |
| 80  | 0.494         | 2.0         | 93%         |  7%            |

Crossover is near R ≈ 6. Above R ≈ 10 the dial-modulated term dominates;
near R = 1 the bike is almost free-spinning and the residual drag is what
you feel.

**`I` from one outdoor anchor.** With λ(R) known, the only remaining
unknown is `I`. We pin it from a single matched-effort outdoor reference
(4iiii crank meter on a snow ride, matched HR + cadence bins): `I = 12.4
kg·m²` (effective at the crank) preserves the originally measured ~18% NP
gap with the IC8 broadcast. This is the weakest link in the pipeline —
it's a single anchor, and the per-cadence-bin estimates spread from ~10
(high cad) to ~18 (low cad), which suggests the steady-state model's `cad²`
isn't a perfect description (real eddy-current physics may have a
sub-quadratic correction at high speed). I=12.4 is a reasonable single
compromise; expect a few percent error at the cadence extremes.

## Reality checks

### Indoor: the model decomposes a sprint cleanly

A real BLE-logged spin-up at R = 28 — cadence 0 → 67 rpm in about 8
seconds, then held steady for 8 more:

![Indoor surge-and-hold](docs/figures/indoor_surge.png)

The blue area is the steady term `(aR+b)·I·ω²`, the red area is the
positive KE term `I·ω·dω/dt`. The solid red line (their sum) is what we
re-broadcast. The dashed line is the IC8's own broadcast.

- **During the spin-up:** KE adds 50–80 W on top of the steady term while
  the rider is accelerating the flywheel.
- **Once cadence holds:** KE collapses to ≈ 0 within 1–2 seconds, and the
  corrected power settles at ≈ 105 W — the new steady-state dissipation
  at cad 67.
- **The IC8 broadcast** (dashed) holds at ~160 W — about 50% above
  corrected at this low-cadence, mid-R operating point. The gap shrinks
  toward higher cadence (see `power_curves.png` above) but is largest
  exactly where the IC8's `cad^1.5` formula overshoots most.

### Outdoor: a 4iiii crank meter shows the same shape

The same physics governs an outdoor bike — bike + rider mass is the
"flywheel," air drag and rolling resistance are the "brake." A short
surge from a snow ride:

![Outdoor surge-and-hold](docs/figures/outdoor_surge.png)

Speed goes from 22 to 33 km/h over 7 seconds. Power peaks near 390 W
during the acceleration, then settles at ~157 W to hold the new pace.
Same bump-during-spin-up, settle-on-hold pattern as the indoor plot,
measured by a different sensor on a different system.

## What the bridge does

```
  ┌──────────────┐         ┌──────────────────────────┐         ┌──────────────┐
  │  indoor bike │   BLE   │       bridge phone       │   BLE   │ training app │
  │              ├────────▶│                          ├────────▶│              │
  │  FTMS 0x1826 │ R, cad, │  P = (aR+b)·I·ω²         │  FTMS + │  Rouvy       │
  │              │ power,  │      + I·ω·dω/dt         │  Power  │  MyWhoosh    │
  │              │  HR     │                          │  0x1818 │  Zwift       │
  │              │         │                          │         │  Garmin      │
  └──────────────┘         └──────────────────────────┘         └──────────────┘
```

The phone running the bridge connects to your bike over BLE (it shows up as
"Nautilus,Inc - IC Bike" or similar), reads the FTMS Indoor Bike Data
characteristic, runs the correction on every sample, and presents itself to
your iPad/Apple TV/computer as a virtual FTMS bike + cycling power meter
named **"IC Bike (corrected)"** by default (configurable in Settings). Your
training app pairs to the bridge instead of the bike.

The bridge ships with:

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

```
bridge/                          Flutter app — the bridge itself
bridge/lib/ble/                  BLE central + peripheral
bridge/lib/physics/              the corrector + coastdown fit (Dart port of
                                 spindown_fit.py — what the in-app
                                 Auto-calibrate runs on the phone)
analysis/parse_nrf_log.py        nRF Connect log -> CSV (FTMS + CSC joined)
analysis/spindown_fit.py         CSV of coastdowns -> a, b
analysis/pin_inertia.py          outdoor 4iiii FIT files -> I_crank
analysis/correct_power.py        offline reprocessor (applies the correction
                                 to a parsed BLE log; Python mirror of the
                                 Dart corrector)
analysis/plot_surge_examples.py  generates the README figures
analysis/decode_ftms.py          FTMS Indoor Bike Data (0x2AD2) decoder
analysis/decode_csc.py           CSC Measurement (0x2A5B) decoder
data/calibration/                BLE logs used to fit a, b
data/                            outdoor FIT files (anchors / validation)
docs/figures/                    README plots
```

## How to build and run

```
cd bridge
flutter pub get
flutter run                      # connect a phone first
```

In the app: if a Bluetooth icon appears in the top bar, tap it to grant
permissions; then tap **Find bike**, tap your bike when it shows up, and the
bridge starts. From your training app on a separate device, pair to
**"IC Bike (corrected)"** (or whatever name you set under Settings → Bike
name in training apps) as a power meter and as an FTMS bike. Done.

## Calibrating to your bike

The app ships with the IC8 calibration as the default. If you have a
different model (or just want to dial in the scale on your specific bike),
two routes:

**In-app (recommended).** Open Settings → **Auto-calibrate**. Follow the
steps: pedal up, stop, repeat at 3+ different resistance levels. The app
fits the brake/friction curve and saves it. Takes 5–10 minutes. To match
absolute scale against an external power meter (e.g. a crank meter on an
outdoor session at matched effort), use the Power scale slider on the same
screen.

**Offline (developers).** The Python pipeline in `analysis/` is what shipped
the defaults. Steps 1–3 are also what the in-app Auto-calibrate does (the
fit is ported to Dart in `bridge/lib/physics/coastdown.dart`); use the
Python path when you want to capture raw BLE logs and rerun the fit on a
desktop.
1. Capture a BLE log with nRF Connect (~5 spin-downs from cad ≥ 80 at
   different R values).
2. `python3 analysis/parse_nrf_log.py raw.txt > data/calibration/spin_downs.csv`
3. `python3 analysis/spindown_fit.py` → emits `λ(R) = a·R + b`
4. `python3 analysis/pin_inertia.py` against one outdoor session at matched
   intensity → emits `I_crank`.
5. Update the defaults in `bridge/lib/physics/calibration.dart` (and
   mirror them in `analysis/correct_power.py` if you use it for offline
   reprocessing).

Tests live in `bridge/test/` — `flutter test` should pass after any default
changes.
