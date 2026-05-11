# bridge

The Flutter app. See the [top-level README](../README.md) for what it does and how it works.

## Build

```
flutter pub get
flutter run        # connect a phone first
flutter test       # unit tests live in test/
```

## iOS signing

The repo ships with no Apple Developer team configured. Before
`flutter run` on iOS, open `ios/Runner.xcworkspace` in Xcode, select
the Runner target, and under Signing & Capabilities pick your team.
Automatic signing handles the rest.

## Android signing

`flutter run --release` uses the debug keystore so it works out of the
box. For Play Store distribution, configure a release signing config
per the [Flutter Android signing guide](https://docs.flutter.dev/deployment/android#signing-the-app).
