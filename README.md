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

- **Right shape across the resistance range.** The bike's formula goes as cad^1.6 and R^0.82 (R is the resistance dial). The brake is an eddy-current damper, so its power goes as cadence squared, and its strength levels off as the dial slides the magnets fully onto the disc.
- **Honest power during transients.** Surging from 80 to 110 rpm spins up the flywheel — roughly 70–115 W the bike doesn't see. The bridge adds the kinetic-energy term `I·ω·dω/dt`, so surges read at full value and coastdowns drop to zero on time.
- **Crank-precision cadence.** The bridge reads the bike's CSC characteristic (per-revolution counts timed to 1/1024 s) on top of the noisier 1 Hz FTMS cadence field, which sharpens the acceleration math during fast transients.
- **Low-R drivetrain fine-tune.** Auto-calibrate (Settings → Auto-calibrate) refits the residual drivetrain drag from flywheel-decay curves — 5–10 minutes, on-device. It matters most at warm-up resistances; at race-pace R the eddy brake dominates. For absolute scale, use the Power scale slider against an external power meter or your FTP from one.
- **Standard FTMS out, no firmware mods.** The bridge re-broadcasts as a standard FTMS power meter, so any training app that pairs to FTMS works. The bike doesn't change.

## Supported models

The Schwinn IC8 (UK/EU), IC4 (US), and Bowflex C6/C7 are the same
underlying hardware. The defaults shipped in the app were fitted on
an IC8 and apply directly.

| Model                    | Status                                          |
|--------------------------|-------------------------------------------------|
| **Schwinn IC8 / IC4**    | Reference platform. Ships calibrated.           |
| **Bowflex C6 / C7**      | Same hardware. Ships calibrated.                |
| Other FTMS indoor bikes  | Cadence + KE math will be right, but the brake shape, magnitude, and flywheel inertia are all pinned to the IC8 family. Auto-calibrate only refits β (drivetrain drag), so a bike with different brake hardware or a different flywheel will read wrong and the in-app knobs can't fix it. |

## Build and run

```
cd bridge
flutter pub get
flutter run --release            # connect a phone via USB first; --release so the app keeps running after you unplug
```

To update the app later, use `flutter run --release` again, which
installs over the old copy. Don't use `flutter install`: it uninstalls
the old copy first, and on iOS that deletes your saved settings (Power
scale, auto-calibration, measured power table, device name).

The app starts advertising **"IC Bike (corrected)"** as soon as
Bluetooth is on; the tile under the status line says whether it is
visible and how many apps have paired. Pair your training app (on a
separate device) to it as a power meter or FTMS bike at any time,
then tap **Find bike** and pick your IC8. Keep IC Bridge in the
foreground: iOS strips the name and service list from a backgrounded
app's advertisement, and most training apps then stop seeing it.

The Darwin half of the `bluetooth_low_energy` plugin is vendored in
`bridge/packages/` with one patch: it advertises 16-bit Bluetooth SIG
UUIDs as 16-bit (upstream issue #137). Unpatched, FTMS and Cycling
Power don't fit the 31-byte advertisement and iOS moves them to the
overflow area that training apps don't scan.

If warm-up power feels off, use Settings → **Auto-calibrate**
(5–10 min, on-device). For absolute scale, do one reference session
([docs/reference-session.md](docs/reference-session.md)): a pedal
power meter grid, a mass hung from a crank arm for the inertia, or, with
no hardware, one hard indoor effort against your FTP from a road-bike
power meter. The session either sets **Power scale** or produces a
measured P(R, cadence) table that you paste into Settings → **Measured
power**, where it replaces the physics model.

## Limitations

- **Absolute scale is not yet checked by a power meter on this bike.** Spin-downs measure the brake's shape in cadence and dial position, but only up to the flywheel inertia $I$: a stronger brake on a heavier flywheel slows down identically, so no analysis of spin-downs can set the scale. $I$ comes from the flywheel's measured geometry with steel weight rings, good to roughly 10%. A threshold check agrees within 2%: the reference rider's best 20 minutes indoors at $R \approx 31$ read 207 W in the model and 292 W on the bike's display, an indoor threshold of 197 W against a 200 W FTP on an outdoor power meter (`analysis/ftp_anchor.py`). That check goes through one rider's body and holds near $R \approx 30$; elsewhere the error can vary with $R$. [docs/reference-session.md](docs/reference-session.md) describes three ways to set the scale on your bike: a pedal power meter swept over $R$ 10 to 90 at three cadences, which `analysis/fit_reference.py` turns into a measured power table that replaces the model; a mass hung from a crank arm and filmed, which pins the inertia at no cost; or one hard indoor effort against your FTP from a road-bike power meter. Another unit adds its own tolerance on top.
- **High-cadence cap.** The IC8 saturates broadcast cadence at 125 rpm. Above the cap, the bridge falls back to CSC-derived cadence. Without CSC it clamps and slightly underestimates power at very high rpm.
- **A heavy dial at high cadence is extrapolated.** Spin-downs at $R \le 57$ start at 116 to 165 rpm, but above that they start lower: the fastest at about 100 rpm for $R$ 66 to 74, and at 57 to 91 rpm for $R$ 81 to 93. Riding $R \ge 80$ above about 90 rpm, or $R \approx 70$ above about 100 rpm, carries the linear brake past the data. A real eddy brake fades once the disc is fast enough; the spin-downs fit worse with a fade anywhere below about 250 rpm at the crank (see "The fix"), but they cannot rule one out in that corner.

---

## Why the bike's numbers can't be trusted

The IC8 broadcasts power as a function of cadence and the resistance
dial:

$$P_{\text{IC8}} \approx 0.0125 \cdot R^{0.82} \cdot \text{cad}^{1.6}$$

(fit to logged BLE holds, within 7% at every $R$ from 10 up). Both exponents are wrong. Real eddy-current physics gives $P \propto \omega^2$ in the linear regime, not $\text{cad}^{1.6}$, and the absolute scale drifts unit-to-unit and across the dial. That's why forum reports disagree about whether the bike reads high or low.

The shape of the gap is consistent though. Dashed lines are what the bike broadcasts, solid lines are what the bridge re-broadcasts:

![IC8 vs corrected power curves](docs/figures/power_curves.png)

The two curves meet around $R \approx 50$ at 80 rpm (nearer 60 at 60 rpm, 45 at 100 rpm). Below that the bridge reads lower than the bike, by about a third at $R \approx 30$; above it the bridge reads higher, by about 12% at $R = 60$ and a third at $R = 80$, at 80 rpm. The exact crossover depends on your unit; the **Power scale** slider pins it against an external reference.

## The fix

The IC8 is a permanent-magnet eddy brake: two pairs of N42 magnets straddle a 5 mm aluminium disc at a fixed 3 mm gap, and the dial slides them from just off the rim onto the disc. A magnet moving through a conducting sheet induces currents that drag on it in proportion to speed, so at the crank the brake is linear damping:

$$\tau_{\text{brake}}(R,\omega) = c\,H(R)^2\,\omega$$

where $H(R)^2$ is how much of the magnets' flux the disc catches at dial position $R$, rising to 1 when they are fully on. The bike's power is that torque plus the residual drag of the drivetrain, plus the kinetic-energy term that matters during accelerations:

$$P_{\text{corrected}} = \left(c\,H(R)^2\,\omega + \tau_c + I\,\beta\,\omega\right)\omega + I\,\omega\,\dot\omega$$

At steady cadence the last term is zero. During an acceleration it adds the work spent spinning up the flywheel; during a coastdown it subtracts.

### Where the constants come from

**Brake curve from spin-downs.** $H(R)$ is a sum of two Hill curves:

$$H(R) = w \cdot \frac{R^{p_1}}{R^{p_1} + R_{h1}^{p_1}} + (1-w) \cdot \frac{R^{p_2}}{R^{p_2} + R_{h2}^{p_2}}$$

The two-Hill split is empirical: the curve has a mid-band shoulder that doesn't fit a single sigmoid, and the IC8's two magnet pairs engage across the same $R$ range, so the second Hill doesn't map cleanly to one of them.

Fit by integrating $I\,\dot\omega = -\tau_{\text{brake}} - \tau_c - I\,\beta\,\omega$ against $\omega(t)$ of every spin-down. Residual drag splits into a constant Coulomb term $\tau_c$ (bearings + belt + seal friction) and a viscous term $I\,\beta\,\omega$ (windage + air-film); $R=0$ spin-downs prefer that split over viscous-only by ~15× in RSS. $\omega(t)$ comes from 120 fps phone video (1 Hz BLE cadence is too coarse for a fast decay) across 46 segments spanning $R = 0$ to 93 (`analysis/track_crank.py`, `analysis/fit_spindowns.py`):

- $c = 43.77$ N·m·s/rad, $\beta = 0.0139$ s⁻¹, $\tau_c = 1.10$ N·m, at $I_{\text{crank}} = 6.66$ kg·m² (see Inertia below).
- $w = 0.450$; broad Hill $R_{h1} = 129.3$, $p_1 = 0.650$; sharp Hill $R_{h2} = 78.9$, $p_2 = 1.87$. At $R = 100$, $c\,H^2 = 12.8$ N·m·s/rad.
- RSS 0.0119 across 51,792 samples.

![Spin-down calibration](docs/figures/spindown_fit.png)

**No fade in the data.** Textbook eddy brakes fade at high speed, where the induced currents' own field cancels the magnets' (Wouterse; Wiederick et al., Am. J. Phys. 55, 500, 1987). Earlier versions modelled that with a fade speed tied to the magnets' field, as when a gap closes, pinned by the 1000 W spec. The IC8's gap is fixed, and the spin-downs reject that model: with its fade speed free, the fit drives it to infinity, and the shipped fit is 37% better in RSS than the old one. A fade at a fixed crank speed fits worse the lower it sits: 4% worse in RSS at 300 rpm, 40% at 160 rpm, 98% at 120 rpm (`analysis/fit_spindowns.py --fade-profile`). Thin-sheet theory puts it at 90 to 125 rpm for this disc (`analysis/physics_first_brake.py`), which the data rule out; that theory is for a sheet much wider than the magnets, and the measured brake doesn't follow it.

**Magnet physics cross-check.** `analysis/physics_first_brake.py` solves the eddy currents for the measured magnets, gap and disc, with the field from the magnetic circuit and conductivity for pure aluminium or 6061. At $R = 100$ it gives 1.0 to 2.5 times the fitted damping at the shipped inertia; the field is an upper bound (leakage is ignored), so the low end is likelier. With the dial taken as linear in magnet travel, the geometry's shape matches the fit within 8% from $R = 50$ up and runs lower below: at $R = 30$ the fit has 14% of the full-dial damping and the geometry 10%. The dial-to-magnet mapping is the likely difference. Refitting with the geometry's shape and a one-parameter warp of the dial in place of the two Hills fits 2.5 times worse in RSS (`analysis/fit_geom_hill.py`), so the two-Hill form ships.

The H-shape and $\tau_c$ ship as fixed defaults; they entangle brake physics, the dial-to-magnet mapping, and bearing/belt friction in ways spin-down data alone can't separate. Auto-calibrate refits only $\beta$, against the collapse $\lambda_{\text{eff}}(R) = \beta + (c/I) \cdot H(R)^2$ of each spin-down's decay rate. $c$ and $I_{\text{crank}}$ are structurally degenerate in spin-down data (only their ratio appears in $I\,\dot\omega = -\tau$), so absolute scale comes from the inertia below and the Power scale slider, not from auto-calibrate.

**Inertia: flywheel geometry.** The flywheel is a 5 mm aluminium disc ($R = 23$ cm) with a steel weight ring on each face, ruler-measured at $r$ = 14 to 18 cm by 2.03 cm and 13 to 17 cm by 1.52 cm. A magnet sticks to the rings and not to the disc. That is a 13.1 kg flywheel, and with the belt ratio $g = 4.5$ (which enters as $g^2$) it gives **$I_{\text{crank}} = 6.66$ kg·m²**, the shipped value. Spin-downs fix only torque over inertia, so $c$ and $\tau_c$ ship as the fit carried to this $I$: scaling $c$, $\tau_c$ and $I$ together leaves every fitted $\omega(t)$ and the RSS unchanged. The ring heights are ruler upper bounds with chamfered edges, the hub, pulleys and crank are left out, and $g$ is a ruler measurement; counting flywheel turns per crank turn would pin it. A magnet does not tell steel from cast iron, which would give 6.21. So the geometry holds to roughly 10%. Earlier versions shipped 9.09, which took the rings to be lead because only lead brings the measured volumes up to the spec sheet's 18 kg, and then 7.55, from matching outdoor rides at equal heart rate.

A maximal effort cross-checks the geometry through one rider: their best 20 minutes indoors at $R \approx 31$ read 207 W in the model (292 W on the bike's display), an indoor threshold of 197 W against their 200 W outdoor FTP on a 4iiii crank meter, 2% apart (`analysis/ftp_anchor.py`; $R$ is recovered from the bike's broadcast formula, which costs up to about 5% in model power). That check covers $R \approx 30$, and the shape across the rest of the dial is the spin-downs' alone until a power meter is put on this bike. Owners of IC4/C6 units who did that report the bike's own number over-reads by 10 to 40%, growing with $R$. This model puts the over-read at about 55% near $R = 30$ and 20% at $R = 40$, and has the bike under-reading from $R \approx 50$ up, by about a quarter at $R = 80$ (all at 80 rpm): it matches those reports mid-dial but runs against their growth with $R$. The spin-downs measure the brake directly up to $R = 93$, so a pedal power meter on this bike is what would settle it.

The in-app **Power scale** slider scales $c$, $\tau_c$ and $I_{\text{crank}}$ together, so steady-state, residual drag, and the KE term move in lockstep. Default 1.0; a reference session sets it. If that session shows the ratio to a power meter changing with $R$, the shape above is wrong for the bike and the app's **Measured power** table takes over: bilinear interpolation on the measured grid, with the acceleration term kept.

## Reality check: the model decomposes an acceleration cleanly

A BLE-logged acceleration at $R = 25$: cadence climbs from 23 to 125 rpm over ~11 seconds, then the rider stops pushing and the flywheel coasts back down.

![Indoor acceleration](docs/figures/indoor_surge.png)

Blue area is the steady term $\tau_{\text{brake}}(R,\omega)\,\omega$, red is the KE term $I\,\omega\,\dot\omega$. KE adds ~160 W at the peak of the ramp, then flips negative during the coastdown so total power drops to near zero.

## Repository layout

```
bridge/            Flutter app (the bridge itself)
  lib/ble/           BLE central + peripheral
  lib/physics/       corrector + coastdown fit
                     (what Auto-calibrate runs on-device) + measured
                     power table (power_table.dart)
analysis/          Calibration pipeline (Python): nRF Connect log → CSV
                   → video crank tracking → spin-down curation →
                   spin-down ODE fit. fit_reference.py compares
                   the model to a power meter and writes the table;
                   falling_mass.py gets the inertia from a hung mass;
                   ftp_anchor.py sets the scale from your FTP. Each
                   script documents its role at the top; deps in
                   `analysis/requirements.txt`.
docs/              reference-session.md (how to anchor absolute power)
docs/figures/      README plots and the bridge data-flow diagram.
```

## License

[PolyForm Noncommercial 1.0.0](LICENSE). Free to use, modify, and share for personal, research, hobby, and other noncommercial purposes. Commercial use is not permitted.
