# IC Bridge

If you ride one of the indoor bikes listed below and pair it to Rouvy,
MyWhoosh, Zwift, or Garmin, your power numbers are roughly **15–20% high**.
IC Bridge is a small Flutter app that reads the bike's BLE output, applies a
physics-based correction, and re-broadcasts the corrected number as a virtual
cycling power meter your training apps can pair to.

## Supported models

The correction *shape* (eddy-current brake → `(a·R + b)·I·ω²`) applies to any
indoor bike with a manual resistance dial and FTMS over BLE. The *constants*
were fitted on a Schwinn IC8 and ship as the default. **If you're not on an
IC8, run Auto-calibrate (Settings → Auto-calibrate) before your first ride** —
different flywheels and brake hardware mean the IC8 numbers will be off by
more than just a trim.

| Model                    | Status                                            |
|--------------------------|---------------------------------------------------|
| **Schwinn IC8 / 800IC**  | Reference platform — ships calibrated, ready to use |
| **Schwinn IC4**          | Different flywheel from IC8 — run Auto-calibrate first |
| **Bowflex C6 / C7**      | Same hardware as the IC4 under a different brand — run Auto-calibrate first |
| Other FTMS indoor bikes  | Should work if they broadcast resistance over FTMS — run Auto-calibrate first, then verify scale against an outdoor power meter if you have one |

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
At a typical hard zone of R ≈ 40 / cad ≈ 90, the bike reads ~343 W and the
bridge says ~330 W. At lower cadence the gap widens — at R ≈ 40 / cad ≈ 60,
IC8 reads ~163 W and the bridge ~135 W. (Above cad ≈ 100, `cad²` outpaces
`cad^1.5` and the bridge can read slightly higher than IC8 — see the
"structural limitation" note below.)

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

Twenty clean coastdowns from two structured sessions, spanning
R = 1, 4, 5, 11, 14, 15, 17, 23, 24, 25, 31, 32, 37, 38. The pooled
line fit gives **a = 0.00590 / (s·R-unit)** and **b = 0.0362 / s** with
τ at R=0 of about 28 s. Above R ≈ 45 the rider can't reach 125 rpm
against the brake, so coastdowns are too short (3–6 s) to fit a clean
exponential — but the linear extrapolation is consistent with pedal-feel
up to R=100.

The two sessions agree on `b` to within 1% (0.0363 vs 0.0360) but the
slope `a` differs by ~11% (0.00633 vs 0.00572). That difference is not
statistically significant — t ≈ 1.4σ given each session's slope SE — and
mostly reflects regression leverage: the smaller session has 6 points
clustered mid-R, so its slope is weakly identified. Subtle effects like
mild residual rider input during "spindowns", R-dial mechanical backlash,
and modest brake-temperature variation could each contribute a few
percent on top. The pooled fit averages them out.

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
| 10  | 0.095         | 11          | 62%         | 38%            |
| 30  | 0.213         | 4.7         | 83%         | 17%            |
| 50  | 0.331         | 3.0         | 89%         | 11%            |
| 80  | 0.508         | 2.0         | 93%         |  7%            |

Crossover is near R ≈ 6. Above R ≈ 10 the dial-modulated term dominates;
near R = 1 the bike is almost free-spinning and the residual drag is what
you feel.

**`I` from outdoor anchors.** With λ(R) known, the only remaining unknown
is `I`. We pin it against outdoor 4iiii crank-meter sessions in HR + cadence
bins, back-solving R from the IC8 broadcast and equating physical input:

```
P_outdoor(HR, cad) = (a·R_back + b) · I · ω²   →   I = P_out / [(a·R+b)·ω²]
```

**Pool**: a handful of outdoor 4iiii rides (~11k truth samples) matched
against indoor IC8 sessions on the same bike (~12k broadcast samples).
Per-cadence-bin median `I` (raw 4iiii):

| cad bin | I_est (kg·m²) |
|---------|---------------|
| 55–60   | 19.3          |
| 60–65   | 17.4          |
| 65–70   | 14.7          |
| 70–75   | 12.6          |
| 75–80   | 12.0          |
| 80–85   | 11.4          |
| 85–90   | 10.6          |

Two reasons to trust these numbers cautiously rather than literally:

1. **Outdoor truth can be biased low.** A single-sided crank meter
   (4iiii on one crank arm) under-counts bilateral output by an amount
   that depends on left-right balance. Cold, fatigued, or low-cadence
   grinding outdoor segments also produce less mechanical power per
   unit HR than fresh indoor efforts at the same HR. Both effects push
   `I_est` down by an estimated ~10–15%, multiplicatively across all bins.
2. **The cadence slope persists.** Even after bias correction, `I_est`
   slopes with cadence — high at low cad, low at high cad. R_back stays
   near 30 across all bins, so this isn't an R-confound; it's likely
   either a matching artifact (outdoor low-cad bins are usually climbs
   at a different drag regime than indoor flats) or a real sub-quadratic
   correction at high cadence that the `cad²` model can't express. The
   spindown data itself fits log-linear cleanly at cad 30–70, but the
   indoor zone spans cad 75–95 — extrapolation territory.

**Sanity check from physics alone.** Schwinn IC8 flywheel is reported at
~18 kg, with mass concentrated at the rim (designed for inertia). For a
ring-loaded flywheel `I_flywheel ≈ m·r² ≈ 0.4–0.5 kg·m²`. With a typical
~6:1 belt drive, `I_crank = I_flywheel · g² ≈ 14–18 kg·m²`. A perfect
solid-disc approximation gives `I_flywheel ≈ ½·m·r² ≈ 0.29` and
`I_crank ≈ 10–14`. So **I in the 10–18 range is consistent with the
hardware**, and `I = 14` sits in the middle.

**Default**: `I = 14.0 kg·m²`. It's the bias-corrected center of the
per-bin estimates near the rider's typical cadence (70–80 rpm), and
matches a roughly rim-loaded flywheel with ~6:1 gearing. Expect a few
percent error at the cadence extremes — the in-app **Power scale**
slider absorbs the leftover offset against an external reference.

**Known structural limitation.** Because `cad² / cad^1.586 ∝ cad^0.414`,
the bridge correction shrinks as cadence grows: most aggressive at low
cad, near zero (or crossing over) at high cad. If the IC8 firmware in
fact overstates power *more* at high cadence, the `cad²` model can't
represent that — a sub-quadratic exponent (β < 1.586) would be needed.
Resolving this requires bilateral indoor truth across a cadence sweep,
which the current dataset doesn't include.

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
  corrected power settles at ≈ 135 W — the new steady-state dissipation
  at cad 67.
- **The IC8 broadcast** (dashed) holds at ~160 W — about 18% above
  corrected at this low-cadence, mid-R operating point. The gap shrinks
  toward higher cadence (see `power_curves.png` above) and inverts above
  cad ≈ 100, exactly because the IC8's `cad^1.5` formula grows slower
  than `cad²`.

### Outdoor: a 4iiii crank meter shows the same shape

The same physics governs an outdoor bike — bike + rider mass is the
"flywheel," air drag and rolling resistance are the "brake." A short
acceleration from an outdoor ride:

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

The app ships with the IC8 calibration as the default. Open Settings →
**Auto-calibrate** to fit the brake/friction curve to your own bike — the
app walks you through it on-device, takes 5–10 minutes, and saves the
result. If you also have an external power meter (e.g. a crank meter from
a matched outdoor effort), use the **Power scale** slider on the same
screen to dial in the absolute scale.

The Python pipeline in `analysis/` is what shipped the defaults and mirrors
what Auto-calibrate runs on the phone. It's there for developers who want
to rerun the fit on a desktop against raw BLE logs.

Tests live in `bridge/test/` — `flutter test` should pass after any default
changes.
