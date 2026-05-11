# IC Bridge

If you ride a Schwinn IC8/IC4 (or rebadged Bowflex C6/C7) and pair it
to Rouvy, MyWhoosh, Zwift, or Garmin, the broadcast power numbers can
be way off, and inconsistently so. Some riders see an exact match
against a crank meter, others see 50–100 W gaps in the same zones.
IC Bridge is a small Flutter app that reads the bike's BLE output,
applies a physics-based correction, and re-broadcasts the result as a
virtual FTMS power meter your training apps can pair to.

## Why use this

- **Physics-derived correction, not a flat scale factor.** The model
  is Wouterse permanent-magnet eddy-brake dynamics, $P = \tau_{\text{brake}}(R,\omega)\,\omega + I\,\omega\,\dot\omega$, with the bell-curve $\tau(\omega)$ shape that classical eddy-brake theory predicts for a conducting disc in a stationary magnetic field. It responds correctly to transients (sprints, coastdowns, low-cadence grinding) instead of just shifting every number by a percentage.
- **Calibrates to your specific bike.** Auto-calibrate (Settings →
  Auto-calibrate) fits the brake curve on-device in 5–10 minutes. If
  you have an outdoor power meter, the Power scale slider pins the
  absolute scale against ground truth.
- **No firmware mods, standard FTMS out.** The bike doesn't change.
  The bridge re-broadcasts as a standard FTMS power meter, so any
  training app that pairs to FTMS works.
- **Production-grade plumbing.** Auto-reconnect with backoff if the
  BLE link drops, wakelock so the bridge phone stays awake, and an
  FTMS Control Point stub that politely tells apps "manual brake, no
  ERG/sim" so they fall back to power-only mode cleanly.

## Supported models

The Schwinn IC8 (UK/EU), IC4 (US), and Bowflex C6/C7 are the same
underlying hardware. The defaults shipped in the app were fitted on
an IC8 and apply directly.

| Model                    | Status                                          |
|--------------------------|-------------------------------------------------|
| **Schwinn IC8 / IC4**    | Reference platform. Ships calibrated.           |
| **Bowflex C6 / C7**      | Same hardware. Ships calibrated.                |
| Other FTMS indoor bikes  | Should work if they broadcast resistance over FTMS. Run Auto-calibrate first, then verify scale against an outdoor power meter if you have one. |

## Why the bike's numbers can't be trusted

The IC8 broadcasts power as a function of cadence and the resistance
dial:

$$P_{\text{IC8}} \approx 0.019 \cdot R^{0.83} \cdot \text{cad}^{1.5}$$

Two issues:

1. **The cadence exponent is wrong.** Real eddy-current physics gives $P \propto \omega^2$, not $\text{cad}^{1.5}$.
2. **The absolute scale isn't fixed.** Whether the bike reads high, low, or on the money depends on the unit, the dial calibration, and the operating point. That's why forums are confusing. There's no single offset that fits every rider's experience.

The *shape* of the gap is consistent though, even when the magnitude
isn't. Dashed lines are what the bike broadcasts, solid lines are
what the bridge re-broadcasts:

![IC8 vs corrected power curves](docs/figures/power_curves.png)

The two curves disagree both on cadence-scaling (IC8 uses $\text{cad}^{1.5}$, the physics gives $\text{cad}^2$ in the linear brake regime) and on $R$-scaling. The IC8's $R^{0.83}$ is a soft sub-linear growth, while the real eddy-brake's effective damping rises sharply through the middle of the dial before saturating at the high end (see the spin-down plot below). The bridge and the IC8 cross around $R \approx 45$ at moderate cadences. Below that the bridge reads lower than the bike, above it the bridge reads higher. The exact crossover depends on the absolute scale of your unit, which the **Power scale** slider lets you pin against an external reference.

## The fix

For a permanent-magnet eddy brake on a conducting disc, exactly the
IC8 architecture (aluminum flywheel, gap-adjustable PM brake), the
classical Wouterse / Smythe / Wiederick theory gives the brake torque
as a bell curve in $\omega$:

$$\tau_{\text{brake}}(R,\omega) = \tau_{\max}(R) \cdot \frac{2(\omega/\omega_c(R))}{1 + (\omega/\omega_c(R))^2}$$

with peak torque $\tau_{\max}(R)$ and critical angular speed $\omega_c(R)$. Below $\omega_c$ the torque is linear in $\omega$. Above $\omega_c$ it falls because eddy currents create an opposing reaction field that cancels part of the source flux. Steady-state brake power is

$$P_{\text{steady}} = \tau_{\text{brake}}(R,\omega) \cdot \omega$$

There's also a kinetic-energy term that matters during accelerations
and decelerations:

$$P_{\text{KE}} = I\,\omega\,\dot\omega$$

Total rider input is the sum:

$$P_{\text{corrected}} = \tau_{\text{brake}}(R,\omega) \cdot \omega + I\,\omega\,\dot\omega$$

At steady cadence the second term is zero. During a sprint launch it
adds the work to spin up the flywheel. During a coastdown it
subtracts and the total goes to zero (the rider isn't doing work).

### Where the constants come from

**$\tau_{\max}(R)$ and $\omega_c(R)$ from spin-downs.** Both R-functions trace a single underlying $B^2(R)$ curve via the strict-Wouterse coupling $\tau_{\max} \propto B^2$ and $\omega_c \propto 1/B^2$. We parameterize $B^2(R)$ with a Hill curve so the model is smooth and continuous (zero at $R = 0$, saturating at high $R$):

$$H(R) = \frac{R^p}{R^p + R_h^p}, \quad \tau_{\max}(R) = \alpha\,H(R), \quad \frac{1}{\omega_c(R)} = \kappa\,H(R)$$

Fit by integrating the spindown ODE $I\,\dot\omega = -\tau_{\text{brake}}(R,\omega) - I\,\beta\,\omega$ against the actual $\omega(t)$ of every spindown — *not* a per-segment log-linear $\hat\lambda$ fit (which would be biased wherever the bell curve bites). Hand-curated dataset of 46 video-tracked spindowns spanning $R = 0$ to $93$ (`analysis/fit_wouterse.py`).

- $\alpha = 165$ N·m, peak torque amplitude. Anchored to the manufacturer's 1000 W max-output spec via $\alpha/\kappa = 1021$ W (the asymptotic peak brake power under strict Wouterse).
- $\beta = 0.0390$ per second, residual drag at $R = 0$.
- $\kappa = 0.162$ s/rad, $1/\omega_c$ at saturation (geometry).
- $R_h = 72.1$, Hill midpoint (geometry × bike-firmware mapping).
- $p = 1.27$, Hill sharpness (geometry × bike-firmware mapping).
- $\alpha/\kappa \approx 1.0$ kW, the $\tau_{\max} \cdot \omega_c$ invariant set by disc conductivity × thickness × pole-area × radius². Matches the marketing 1000 W max to ~2%.

![Spin-down calibration](docs/figures/spindown_fit.png)

In our spindown $\omega$ window the trajectories are mostly in the linear regime ($\omega < \omega_c$), so the bell-curve term contributes modestly except at the highest $R$. The model still beats pure linear damping for two reasons: (a) it's the correct physics, smoothly extrapolating into regions we can't sample, and (b) it bounds the high-$R$ power at the strict Wouterse asymptote $2\alpha/\kappa$ instead of running away as $R^p$ would.

$R_h$, $p$, and $\kappa$ are held fixed across bikes because they combine the eddy-brake gap-vs-dial physics with whatever non-linear mapping the IC8's firmware applies between dial position and physical brake state. Those layers are inseparable from spindown data alone, so they ship as defaults. Only $(\alpha, \beta)$ are fit per bike by Auto-calibrate, against the linear-regime design row $\lambda_{\text{eff}}(R) = \beta + (2\alpha\kappa/I) \cdot H(R)^2$ that the Wouterse model collapses to at user-coastdown cadences.

**$I$ from flywheel geometry, no fitting.** The IC8's 18 kg flywheel decomposes into an Al disc and two iron weight-rings, one on each face. Disc radius $R = 0.23$ m (46 cm OD). Rings measured by ruler against the outer edge:
- Side A: $r = 13.5$–$18.5$ cm, $h \le 2.0$ cm $\Rightarrow V\!\cdot\!\rho_{\text{Fe}} = 7.91$ kg.
- Side B: $r = 13.0$–$17.0$ cm, $h \le 1.5$ cm $\Rightarrow V\!\cdot\!\rho_{\text{Fe}} = 4.45$ kg.
- Disc residual: $m_{\text{disc}} = 18 - 12.36 = 5.64$ kg, implying ~12.6 mm average thickness. The original 5 mm reading was the thinnest exposed section — the disc has a thicker hub/backing structure.

For each ring as an annular disc, $I_{\text{ring}} = \tfrac{1}{2}\,m\,(r_{\text{in}}^2 + r_{\text{out}}^2)$:
$$I_{\text{flywheel}} = \underbrace{\tfrac{1}{2}\,m_{\text{disc}}\,R^2}_{0.149} + \underbrace{\tfrac{1}{2}\,m_A(r_{\text{in},A}^2 + r_{\text{out},A}^2)}_{0.208} + \underbrace{\tfrac{1}{2}\,m_B(r_{\text{in},B}^2 + r_{\text{out},B}^2)}_{0.102} = 0.459\ \text{kg·m}^2$$

With the video-confirmed flywheel-to-crank gear ratio $g = 4.5$, the effective inertia at the crank is $I_{\text{crank}} = g^2 \cdot I_{\text{flywheel}} = 9.29$ kg·m². The disc and rings sit at similar effective radii ($R^2/2 \approx 0.026$, $(r_{\text{in}}^2 + r_{\text{out}}^2)/2 \approx 0.024$–$0.026$ m²), so $I_{\text{crank}}$ is insensitive to how much of the 18 kg sits in the disc vs. the rings — making the residual-mass split robust to the "less-than" thickness bounds.

**$\alpha$ from the manufacturer's 1000 W max-output rating.** Under strict Wouterse, the asymptotic peak brake power at any single $\omega$ is $\alpha/\kappa$ — that's the most resistance the brake can ever produce. Setting $\alpha = 165$ N·m gives $\alpha/\kappa = 1021$ W against the data-fit $\kappa$, matching the spec to within ~2%.

These three anchors — disc geometry, belt geometry, and the brake's stated max output — are independent of each other and of perceived effort. They land on a calibration that's self-consistent both with the data (RSS = 0.0433 across 51,792 samples) and with the bike's design specs.

The in-app **Power scale** slider scales $\alpha$ and $I_{\text{crank}}$ together by the same factor, so steady-state, residual drag, and the KE term all scale linearly in lockstep. It's a clean absolute-scale knob that doesn't distort cadence or R shape. Default is 1.0. Tune against an external power meter when one is available.

## Reality check: the model decomposes a sprint cleanly

A BLE-logged sprint at $R = 25$. Cadence climbs from 24 to 118 rpm
over ~10 seconds (briefly hitting the FTMS 125-rpm cap), then the
rider stops pushing and the flywheel coasts back down to ~50 rpm:

![Indoor sprint](docs/figures/indoor_surge.png)

Blue area is the steady term $\tau_{\text{brake}}(R,\omega)\,\omega$, red area is the KE term $I\,\omega\,\dot\omega$. KE adds ~135 W on top of the ~300 W steady at the peak of the ramp, then flips negative during the coastdown so total power drops to near zero (the rider has stopped pushing, the flywheel is bleeding off its kinetic energy into the brake). The same shape shows up on a 4iiii crank meter during an outdoor acceleration: different sensor, different system, same physics.

## What the bridge does

```
  ┌──────────────┐         ┌──────────────────────────┐         ┌──────────────┐
  │  indoor bike │   BLE   │       bridge phone       │   BLE   │ training app │
  │              ├────────▶│                          ├────────▶│              │
  │  FTMS 0x1826 │ R, cad, │  P = τ_brake(R,ω)·ω      │  FTMS + │  Rouvy       │
  │              │ power,  │      + I·ω·dω/dt         │  Power  │  MyWhoosh    │
  │              │  HR     │                          │  0x1818 │  Zwift       │
  │              │         │                          │         │  Garmin      │
  └──────────────┘         └──────────────────────────┘         └──────────────┘
```

The phone running the bridge connects to your bike over BLE (it
shows up as "Nautilus,Inc - IC Bike" or similar), reads the FTMS
Indoor Bike Data characteristic, runs the correction on every
sample, and presents itself as a virtual FTMS bike + cycling power
meter named **"IC Bike (corrected)"** by default (configurable in
Settings). Your training app pairs to the bridge instead of the
bike.

There's no resistance control. The bike has a manual dial, so ERG
mode isn't possible regardless of what you pair it to.

## Limitations

- **Absolute scale depends on your unit, and the model can't infer it from spindowns alone.** The *shape* of the correction (Wouterse $\tau(\omega)$, saturating $\tau_{\max}(R)$) is physics-derived and solid. The multiplicative offset is structurally underdetermined: spindowns fit $\alpha$ from $I\,\dot\omega = -\tau$, so $\alpha$ scales linearly with whatever $I_{\text{crank}}$ we assume. The shipped $I_{\text{crank}} = 9.29$ kg·m² is from direct geometry (disc mass + iron-belt mass) on the reference unit, and $\alpha = 165$ N·m is anchored to the manufacturer's 1000 W rating; another IC8 with different mass distribution or manufacturing tolerances could still land 10% off. The Power scale slider scales $\alpha$ and $I_{\text{crank}}$ together to absorb that, but pinning it requires an external power meter on this bike.
- **High-cadence cap.** The IC8 saturates broadcast cadence at 125 rpm. Above the cap, the bridge falls back to CSC-derived cadence if the bike exposes CSC. Otherwise it clamps and slightly underestimates sprint power.
- **Bell-curve onset $\omega_c(R)$ is anchored by the Wouterse coupling $\tau_{\max} \cdot \omega_c = \alpha/\kappa$, not by spindowns reaching the regime.** Our spindowns sit mostly below $\omega_c$ in the linear-damping range, so the data anchors $\tau_{\max}(R)$ cleanly but the bell-curve $\omega_c(R)$ is constrained by physics (strict $\tau_{\max} \propto B^2$, $\omega_c \propto 1/B^2$) more than by trajectory shape. Disambiguating $\omega_c(R)$ at high $R$ would need either independent $B(R)$ measurement or coastdowns from much higher peak cadence, neither of which we have for the calibration set.

## Repository layout

```
bridge/                          Flutter app, the bridge itself
bridge/lib/ble/                  BLE central + peripheral
bridge/lib/physics/              corrector + Wouterse coastdown fit
                                 (what Auto-calibrate runs on-device)
analysis/parse_nrf_log.py        nRF Connect log -> CSV (FTMS + CSC joined)
analysis/decode_ftms.py          FTMS Indoor Bike Data parser
analysis/decode_csc.py           CSC measurement parser
analysis/track_crank.py          per-frame crank-angle PCA tracker on a
                                 spindown video (mod-π output)
analysis/extract_spindowns_from_video.py  segments active runs in a tracked
                                          crank-angle video
analysis/spindown_fit_video.py   per-segment exponential decay fit on raw
                                 mod-π crank angles
analysis/curate_spindowns.py     interactive in/out-marker tool over the
                                 video coastdown candidates
analysis/aggregate_spindowns.py  merges curated bounds into one per-rev ω(t)
                                 dataset (data/calibration/all_spindowns.csv)
analysis/fit_wouterse.py         strict-Wouterse 5-param fit on the curated
                                 dataset (one-shot trajectory ODE fit)
analysis/plot_readme_figures.py  regenerates power_curves.png and
                                 indoor_surge.png from the bridge defaults
                                 + the canonical R=25 sprint BLE log
data/calibration/                BLE logs + crank videos used to fit defaults
docs/figures/                    README plots
```

## Build and run

```
cd bridge
flutter pub get
flutter run                      # connect a phone first
```

In the app: if a Bluetooth icon appears in the top bar, tap it to
grant permissions, then tap **Find bike**, tap your bike, and the
bridge starts. From your training app on a separate device, pair to
**"IC Bike (corrected)"** as a power meter and as an FTMS bike.

If your numbers feel off, open Settings → **Auto-calibrate** to fit
the brake curve to your bike (5–10 minutes, on-device). If you have
an external power meter, use the **Power scale** slider on the same
screen to pin the absolute scale. It scales steady-state and
acceleration response by the same factor, so you only ever set one
number. Default is 100% — the shipped constants already absorb the
known offsets on the reference unit.

Tests live in `bridge/test/`. `flutter test` should pass after any
default changes.
