# IC Bridge

If you ride a Schwinn IC8/IC4 (or rebadged Bowflex C6/C7) and pair it
to Rouvy, MyWhoosh, Zwift, or Garmin, the broadcast power numbers can
be way off. Some riders see an exact match against a crank meter,
others see 50–100 W gaps in the same zones.
IC Bridge is a small Flutter app that reads the bike's BLE output,
applies a physics-based correction, and re-broadcasts the result as a
virtual FTMS power meter your training apps can pair to.

## What the bridge does

![Bridge data flow: bike to bridge phone to training app](docs/figures/bridge_diagram.svg)

The bridge reads two BLE services from the bike: **FTMS Indoor Bike Data** (cadence, resistance level, the bike's own power estimate) and **CSC Cycling Speed and Cadence** (per-revolution crank counts and event times). It runs the physics correction on every sample, then advertises itself as a virtual FTMS bike + cycling power meter named **"IC Bike (corrected)"** (configurable in Settings). Your training app pairs to the bridge instead of the bike.

Heart rate works the usual way: pair your strap directly to your training app. The bridge isn't a HR proxy.

There's no resistance control. The bike has a manual dial, so ERG mode isn't possible regardless of what you pair to.

## Why use this

- **Right shape across the resistance range.** The bike's formula uses cad^1.5 and R^0.83 (R is the resistance dial). The actual eddy-current physics is quadratic in cadence and saturates in R. On the reference unit the bike reads low at low R (warm-ups feel harder than they are) and high at race-pace R.
- **Honest power during accelerations.** When you stand up and surge from 80 to 110 rpm, you're also spinning up an 18 kg flywheel. That's an extra 100–150 W the bike doesn't see. The bridge adds the kinetic-energy term `I·ω·dω/dt` so the surge reads at full value.
- **Honest power during coastdowns and recoveries.** When you stop pushing, the bike keeps reporting `R × cad^1.5`. The bridge subtracts the kinetic energy flowing out of the flywheel into the brake, so power drops to zero on time.
- **Crank-precision cadence.** The bridge reads the bike's CSC characteristic (per-revolution counts timed to 1/1024 s) on top of the noisier 1 Hz FTMS cadence field, which sharpens the acceleration math during fast transients.
- **Calibrates to your bike's drivetrain.** Auto-calibrate (Settings → Auto-calibrate) takes 5–10 minutes: pedal up to at least 70 rpm, lift both feet off so the pedals spin freely, wait for them to stop, then change the resistance and repeat — at least 3 different resistance levels. It fits your residual drivetrain drag from the resulting flywheel-decay curves. With an outdoor power meter, the Power scale slider pins the absolute scale against ground truth.
- **Standard FTMS out, no firmware mods.** The bridge re-broadcasts as a standard FTMS power meter, so any training app that pairs to FTMS works. The bike doesn't change.
- **Production-grade plumbing.**
  - Auto-reconnect with backoff if the BLE link drops.
  - Wakelock keeps the bridge phone awake.
  - The bridge advertises a manual brake (no ERG/sim) so training apps cleanly fall back to power-only mode.

## Supported models

The Schwinn IC8 (UK/EU), IC4 (US), and Bowflex C6/C7 are the same
underlying hardware. The defaults shipped in the app were fitted on
an IC8 and apply directly.

| Model                    | Status                                          |
|--------------------------|-------------------------------------------------|
| **Schwinn IC8 / IC4**    | Reference platform. Ships calibrated.           |
| **Bowflex C6 / C7**      | Same hardware. Ships calibrated.                |
| Other FTMS indoor bikes  | Should work if they broadcast resistance over FTMS. Run Auto-calibrate first, then verify scale against an outdoor power meter if you have one. |

## Build and run

```
cd bridge
flutter pub get
flutter run --release            # connect a phone via USB first; --release so the app keeps running after you unplug
```

In the app: if a Bluetooth icon appears in the top bar, tap it to
grant permissions, then tap **Find bike**, tap your bike, and the
bridge starts. From your training app on a separate device, pair to
**"IC Bike (corrected)"** as a power meter and as an FTMS bike.

If your numbers feel off, open Settings → **Auto-calibrate** to fit
your bike's drivetrain drag (5–10 minutes, on-device). If you have
an external power meter, use the **Power scale** slider on the same
screen to pin the absolute scale. Default is 100%.

## Limitations

- **Absolute scale depends on your unit.** Spin-downs can't disentangle brake strength from flywheel inertia, so we pin both from the reference IC8 (geometry for inertia, the 1000 W max-output spec for brake strength). Another unit with different manufacturing tolerances could still be off by 10%. The Power scale slider absorbs that against an external power meter.
- **High-cadence cap.** The IC8 saturates broadcast cadence at 125 rpm. Above the cap, the bridge falls back to CSC-derived cadence. Without CSC it clamps and slightly underestimates power at very high rpm.
- **Roll-off at the highest R values is theory, not data.** Our spin-downs sit mostly in the linear-damping regime where the brake is roughly linear in cadence. The saturating roll-off above that is pinned by classical eddy-brake theory (see "The fix" below) rather than fitted to our measurements.

---

## Why the bike's numbers can't be trusted

The IC8 broadcasts power as a function of cadence and the resistance
dial:

$$P_{\text{IC8}} \approx 0.019 \cdot R^{0.83} \cdot \text{cad}^{1.5}$$

Both exponents are wrong. Real eddy-current physics gives $P \propto \omega^2$ in the linear regime, not $\text{cad}^{1.5}$, and the absolute scale drifts unit-to-unit and across the dial. That's why forum reports disagree about whether the bike reads high or low.

The shape of the gap is consistent though. Dashed lines are what the bike broadcasts, solid lines are what the bridge re-broadcasts:

![IC8 vs corrected power curves](docs/figures/power_curves.png)

The two curves cross around $R \approx 45$ at moderate cadences. Below that the bridge reads lower than the bike, above it the bridge reads higher. The exact crossover depends on your unit; the **Power scale** slider pins it against an external reference.

## The fix

The IC8 is a permanent-magnet eddy brake on an aluminum disc. Classical Wouterse / Smythe / Wiederick theory gives the brake torque as a bell curve in $\omega$, linear below the critical speed $\omega_c$ and falling above it as induced eddy currents partially cancel the source flux:

$$\tau_{\text{brake}}(R,\omega) = \tau_{\max}(R) \cdot \frac{2(\omega/\omega_c(R))}{1 + (\omega/\omega_c(R))^2}$$

Add the kinetic-energy term that matters during accelerations:

$$P_{\text{corrected}} = \tau_{\text{brake}}(R,\omega) \cdot \omega + I\,\omega\,\dot\omega$$

At steady cadence the second term is zero. During an acceleration it adds the work spent spinning up the flywheel; during a coastdown it subtracts.

### Where the constants come from

**Brake curve from spin-downs.** Strict Wouterse pins both $\tau_{\max}(R)$ and $\omega_c(R)$ to a single underlying $B^2(R)$, via $\tau_{\max} \propto B^2$ and $\omega_c \propto 1/B^2$. We parameterize $B^2(R)$ with a Hill curve:

$$H(R) = \frac{R^p}{R^p + R_h^p}, \quad \tau_{\max}(R) = \alpha\,H(R), \quad \frac{1}{\omega_c(R)} = \kappa\,H(R)$$

Fit by integrating $I\,\dot\omega = -\tau_{\text{brake}} - I\,\beta\,\omega$ against $\omega(t)$ of every spin-down (46 video-tracked segments spanning $R = 0$ to 93; `analysis/fit_wouterse.py`):

- $\alpha = 165$ N·m, $\beta = 0.0389$ s⁻¹, $\kappa = 0.162$ s/rad, $R_h = 72.9$, $p = 1.27$.
- $\alpha/\kappa = 1020$ W, the strict-Wouterse asymptotic peak brake power. Within 2% of the manufacturer's 1000 W max-output spec.

![Spin-down calibration](docs/figures/spindown_fit.png)

$R_h$, $p$, and $\kappa$ entangle eddy-brake physics with the IC8 firmware's dial-to-magnet mapping, so they ship as fixed defaults. Auto-calibrate refits only $\beta$ against the linear-regime collapse $\lambda_{\text{eff}}(R) = \beta + (2\alpha\kappa/I) \cdot H(R)^2$. $\alpha$ and $I_{\text{crank}}$ are structurally degenerate in spin-down data (only their ratio appears in $I\,\dot\omega = -\tau$), so per-bike $\alpha$ fitting just absorbs $I_{\text{crank}}$ deviations into a wrong $\alpha$. Absolute scale is the Power scale slider's job.

**Inertia from flywheel geometry, no fitting.** The 18 kg flywheel is a uniform 5 mm Al disc ($R = 23$ cm) with two lead weight-rings measured by ruler:

- Disc ($\rho_{\text{Al}} = 2700$): 2.24 kg, $I = 0.059$ kg·m².
- Ring A ($r$ from 13.5 to 18.5 cm, $h \approx 1.77$ cm, $\rho_{\text{Pb}} = 11{,}340$): 10.09 kg, $I = 0.265$ kg·m².
- Ring B ($r$ from 13.0 to 17.0 cm, $h \approx 1.33$ cm, same density): 5.67 kg, $I = 0.130$ kg·m².

Lead is the only material that closes the mass budget at the measured ring volumes. Iron, brass, copper, and even bismuth all need rings thicker than the measured upper bounds (iron by 27%). With gear ratio $g = 4.5$, $I_{\text{crank}} = g^2 \cdot I_{\text{flywheel}} = 9.19$ kg·m².

Disc geometry, ring geometry, and the 1000 W max spec are three independent anchors. They land on a calibration consistent with the data (RSS = 0.0431 across 51,792 samples).

The in-app **Power scale** slider scales $\alpha$ and $I_{\text{crank}}$ together, so steady-state, residual drag, and the KE term move in lockstep. Default 1.0; tune against an external power meter when one is available.

## Reality check: the model decomposes an acceleration cleanly

A BLE-logged acceleration at $R = 25$. Cadence climbs from 24 to 118 rpm
over ~10 seconds (briefly hitting the FTMS 125-rpm cap), then the
rider stops pushing and the flywheel coasts back down to ~50 rpm:

![Indoor acceleration](docs/figures/indoor_surge.png)

Blue area is the steady term $\tau_{\text{brake}}(R,\omega)\,\omega$, red area is the KE term $I\,\omega\,\dot\omega$. KE adds ~135 W on top of the ~300 W steady at the peak of the ramp, then flips negative during the coastdown so total power drops to near zero (the rider has stopped pushing, the flywheel is bleeding off its kinetic energy into the brake).

## Repository layout

```
bridge/            Flutter app (the bridge itself)
  lib/ble/           BLE central + peripheral
  lib/physics/       corrector + Wouterse coastdown fit
                     (what Auto-calibrate runs on-device)
analysis/          Calibration pipeline (Python): nRF Connect log → CSV
                   → video crank tracking → spin-down curation →
                   strict-Wouterse ODE fit. Also ic8_logger.py for raw
                   BLE capture, plot_readme_figures.py for the README
                   figures, and physics_first_brake.py for an independent
                   geometry-only sanity check (not on the calibration
                   path). Each script documents its role in its top
                   docstring. Install deps with
                   `pip install -r analysis/requirements.txt`.
docs/figures/      README plots and the bridge data-flow diagram.
```

## License

[PolyForm Noncommercial 1.0.0](LICENSE). Free to use, modify, and share for personal, research, hobby, and other noncommercial purposes. Commercial use is not permitted.
