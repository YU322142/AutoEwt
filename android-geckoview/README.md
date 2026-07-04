# AutoEwt GeckoView Prototype

This is an Android-only prototype for the future cross-platform browser adapter.

Goals:

- Bundle a browser engine through GeckoView instead of asking users to install drivers.
- Keep Android as a first-class platform.
- Keep the browser/automation layer separate from the UI: Java Activity + GeckoView own the browser session, while Compose/Material3 owns the Android interface.
- Communicate with web pages through a bundled WebExtension/native messaging bridge.

## Requirements

- Android Studio or Android SDK command line tools
- JDK 17 or newer
- Android SDK Platform 36
- Android SDK Build-Tools 36.0.0
- Gradle 9.4.1 or another version compatible with Android Gradle Plugin 9.2.x

## Build

```powershell
cd android-geckoview
gradle :app:assembleDebug
```

If you prefer a wrapper, run this once on a machine with Gradle installed:

```powershell
gradle wrapper --gradle-version 9.4.1
```

## Compatibility Notes

- `geckoview-omni` is used for the prototype because it carries multiple ABIs.
- Production releases should build ABI-specific APK/AAB artifacts to reduce download size.
- `minSdk` is 26 / Android 8.0. The mainline Android build should stay here because current GeckoView requires Android 8+.
- The UI uses Compose/Material3. It is intentionally a view layer only; browser control and automation stay outside the composables.
- GeckoView communication is done through a bundled WebExtension. This is the path Mozilla documents for content/native messaging in GeckoView.
- The prototype has two in-app pages:
  - Browser: address bar, responsive action bar, optional browser panel, progress, and full/single/hidden logs.
  - Config: username, password, course list URL, mode (`video` for 刷课 / `paper` for 做题), answer/report options, login-fill behavior, and desktop-browser mode.
- Desktop-browser mode is enabled by default because some EWT pages behave differently with a mobile Android user agent.
- Login fill is assistive only. If a page asks for captcha or SMS verification, the app fills account/password and leaves verification to the user.
