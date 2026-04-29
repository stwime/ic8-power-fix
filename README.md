# IC8 Power Fix

Reverse-engineering the Schwinn 800IC's power formula and building a BLE proxy
that replaces the bike's inflated power readings with physics-grounded ones,
so Garmin Connect TSS for indoor rides matches outdoor reality.

## Setup

- **Bike:** Schwinn 800IC (= US IC8 = IC4 = Bowflex C6 family). Magnetic
  eddy-current brake. Resistance dial 0–100, calibrated per service manual.
- **Outdoor PM:** 4iiii single-sided crank-arm meter on the *weak* leg
  (knee history). Working assumption: bilateral imbalance < 5%, so 4iiii
  readings are roughly bilateral-equivalent.
- **Indoor apps:** Rouvy and MyWhoosh; neither writes resistance to FIT.

## Key findings so far

- **Resistance IS broadcast over BLE.** FTMS Indoor Bike Data 0x2AD2, flags
  0x0374 — speed, cad, distance, **resistance**, power, energy, HR all
  present. Resistance is sint16 LE at packet bytes 9–10. (In nRF Connect's
  display block 5 reads as the right number while distance < 65 km and
  R < 256, which always holds.)

- **IC8 firmware formula (4-packet fit, ±2%):**

      P_IC8 ≈ 0.019 · R^0.83 · cad^1.5

  Cross-validated: implied R from existing indoor FIT files comes out to
  ~30, matching the user's recollection of typical R = 29–34.

- **Real physics:** eddy-current brake has `P ∝ R^p · cad²` (cad² is locked
  in below saturation; `p` depends on dial-to-magnet-position curve). The
  IC8 firmware uses cad^1.5 — slightly damped vs physics.

- **IC8 overestimate** (intensity-matched): at HR 170 / cad 75 outdoor real
  power was 200 W (snow ride hardest 10-min). IC8 at HR 160 / cad 84 reads
  243 W. After cadence adjustment, gap is ~25–30%. Simplest correction:
  **P_real ≈ P_IC8 / 1.3** (flat scale).

- **Better physics-grounded form** (after calibration ride):

      P_real = a · R^p · cad²

  with `a` and `p` fitted to BLE-logged samples spanning multiple R values.

## Next step: calibration ride (~15 min, light-to-moderate effort)

Sweep grid — at each R level, hold each cadence for ~60 s:

| R   | Cadences (RPM) |
|-----|----------------|
| 10  | 60, 80, 95     |
| 20  | 60, 80, 95     |
| 30  | 60, 80, 95     |
| 45  | 60, 80, 95     |
| 60  | 60, 80, 95     |

~900 packets across that grid is enough to fit `P_IC8 = a · R^p · cad^q`
precisely.

## Why this seed unlocks the historical data

Once `a, p, q` are fit, the formula inverts cleanly to
`R = (P_IC8 / (a · cad^q))^(1/p)`. We then back-fill R for every second of
the existing indoor FIT files (Tenerife, Sunshine, MyWhoosh) — ~3 hours of
real training data with `(R, cad, P_IC8, HR)`. That's the dataset for
Phase 2: bridging to outdoor 4iiii via HR/cadence to fit `a_real` and
`p_real` for the *true* power formula `P_real = a_real · R^p_real · cad²`.

Validation built in: when we invert on Tenerife/Sunshine, implied R should
mostly land in 29–34 (matches user recollection). If not, the calibration
sweep didn't span enough space — redo it.

## End-state

BLE proxy (likely fork of qdomyos-zwift) that:
1. Reads IC8 0x2AD2 packets
2. Computes `P_real = a · R^p · cad²` with fitted constants
3. Re-broadcasts as Cycling Power Service (0x1818)
4. Rouvy / MyWhoosh / Garmin pair to the proxy, not the bike

Result: indoor TSS in Garmin matches outdoor reality.

## Project layout

    data/calibration/     new BLE logs (input to formula fitting)
    data/historical/      copies of the original FIT files we analyzed
    analysis/             scripts that fit and validate formulas
    logger/               BLE logger (Python/bleak) — to be written

## Reference data points

### BLE packets captured (4 from one ride):

| R | cadence | P_IC8 |
|---|---------|-------|
| 15 | 68     | 105 W |
| 29 | 70     | 181 W |
| 29 | 91     | 269 W |
| 29 | 77     | 207 W |

### Outdoor anchor (snow ride, hardest 10-min):

200 W at HR 170, cadence 75, real bilateral via 4iiii (with <5% imbalance).

### Existing indoor sessions (Rouvy):

- Tenerife: avg P 243, NP 258, HR 160, cad 87, ~82 min — implied R≈30
- Sunshine: avg P 226, HR 150, cad 80 — implied R≈30

## Decisions / open questions

- Ground-truth source for absolute scale: HR-bridged from outdoor model
  (cheap, ±5–8% per ride) vs power pedals (€500, ±2%).
- Proxy implementation: fork qdomyos-zwift vs minimal standalone Python/Node
  BLE bridge. qdomyos-zwift previously had connectivity issues with
  MyWhoosh/Rouvy on iPad — paired phone wasn't discovered. Worth retrying
  once the formula is in.
