# IC Bridge

Realistic power for the Schwinn IC8 / IC4 and Bowflex C6 / C7 spin bikes.

The power these bikes send to Zwift, Rouvy, MyWhoosh or Garmin follows a simple formula, not the bike's actual brake. Compared with a physics model of the bike, it reads about 50% high around resistance 30, a typical endurance setting, and low at heavy resistance. IC Bridge is a phone app that sits between the bike and your training app. It reads the bike over Bluetooth, works out the power from how the magnetic brake and flywheel really behave, and passes the corrected number on as a standard power meter.

![Bridge data flow: bike to bridge phone to training app](docs/figures/bridge_diagram.svg)

## What you get

- **Realistic power across the whole dial.** The bridge models the bike's magnetic brake, measured on a real IC8 at dial positions 0 to 93.
- **Accelerations count.** Spinning up the heavy flywheel takes work the bike ignores. A hard 2 to 3 second surge from 80 to 110 rpm adds 70 to 105 W, and the bridge counts it. When you stop pedalling, power drops to near zero straight away.
- **Precise cadence.** The bridge times each crank revolution instead of using the bike's once-a-second cadence reading.
- **Works with any training app** that pairs to a Bluetooth smart bike (FTMS) or power meter. The bike itself isn't changed.

There's no ERG mode, because the resistance dial is manual and no app can turn it. Heart rate isn't passed through: pair your strap directly to your training app.

## Supported bikes

The Schwinn IC8 (UK/EU), Schwinn IC4 (US) and Bowflex C6 / C7 are the same bike, and the app ships calibrated for them. Other indoor bikes have a different brake and flywheel, so the numbers would be wrong.

## Install

You build the app yourself with [Flutter](https://docs.flutter.dev/get-started/install) and a phone connected by USB. On iPhone, first pick your Apple developer team in Xcode (see [bridge/README.md](bridge/README.md)).

```
cd bridge
flutter pub get
flutter run --release
```

To update later, run `flutter run --release` again. Don't use `flutter install`: on iPhone it deletes your saved settings.

## Ride with it

1. Open IC Bridge with Bluetooth on. It shows up to training apps as **IC Bike (corrected)**; you can rename it in Settings.
2. In your training app, on another device, pair **IC Bike (corrected)** as a smart bike or power meter. The tile under the status line shows how many apps are paired.
3. In IC Bridge, tap **Find bike** and pick your bike.
4. Keep IC Bridge open on screen while you ride. On iPhone, a backgrounded app loses its Bluetooth name and most training apps stop seeing it.

## Fit it to your bike

The defaults come from one IC8, where they agree within 2% with the rider's outdoor FTP. Bikes differ a little, so you can adjust them in Settings:

- **Power scale** moves all power up or down by a percentage. If you know your FTP from an outdoor power meter, one hard indoor effort tells you where to set it.
- **Auto-calibrate** takes 5 to 10 minutes of letting the flywheel coast down. It tunes drivetrain friction, which matters most at light resistance.
- **Measured power**: if you have a pedal power meter, ride a 40-minute grid of resistances and cadences, turn it into a table with the included script and paste it here. It replaces the model.

[docs/reference-session.md](docs/reference-session.md) explains each way of setting the scale.

## Bridge vs the bike's own number

Dashed lines are what the bike shows, solid lines what IC Bridge sends:

![IC8 vs corrected power curves](docs/figures/power_curves.png)

The two agree around resistance 50 at 80 rpm. Below that the bike reads high, about 1.5 times the bridge at resistance 30. Above it the bike reads low, about three quarters of the bridge at resistance 80.

## Limitations

- **Not yet checked against a power meter on this bike.** The absolute level rests on the flywheel's measured weight (good to about 10%) and one rider's FTP check at resistance 30. Owners who fitted power meters to these bikes report the bike's number reads further over as resistance rises; this model has it reading low at heavy resistance instead. A pedal power meter on the bike would settle it.
- **Heavy resistance at high cadence is estimated.** Above resistance 80 at more than about 90 rpm, or 70 at more than about 100 rpm, the numbers go beyond what was measured.
- **Above 125 rpm** cadence and power read low if the bike's crank timing drops out.

## How it works

The IC8 brakes with magnets beside a spinning aluminium disc. A brake like that resists in proportion to speed, so power rises with cadence squared; the bike's own formula uses cadence to the power 1.6. IC Bridge's brake model was fitted to 46 filmed coast-downs across the dial, scaled by the flywheel's measured weight, and adds the energy that goes into speeding up the flywheel. [docs/physics.md](docs/physics.md) has the full derivation, the constants and how each was checked.

## For developers

```
bridge/            Flutter app (see bridge/README.md)
  lib/ble/           BLE central + peripheral
  lib/physics/       power model, coast-down fit for Auto-calibrate,
                     measured power table
analysis/          Calibration pipeline (Python): BLE log → CSV → video
                   crank tracking → spin-down curation → spin-down fit,
                   plus the reference-session tools (fit_reference.py,
                   falling_mass.py, ftp_anchor.py). Each script documents
                   itself at the top; deps in analysis/requirements.txt.
docs/              physics.md, reference-session.md, figures/
```

## License

[PolyForm Noncommercial 1.0.0](LICENSE). Free to use, modify, and share for personal, research, hobby, and other noncommercial purposes. Commercial use is not permitted.
