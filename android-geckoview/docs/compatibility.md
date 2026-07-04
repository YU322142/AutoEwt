# GeckoView Compatibility Plan

## Baseline

- Minimum Android: API 26 / Android 8.0 for the current GeckoView build.
- Implementation style: Java `ComponentActivity` owns GeckoView/session lifecycle; Compose/Material3 owns the UI layer.
- Browser engine: GeckoView bundled as an AAR.
- Prototype artifact: `geckoview-omni`, because it contains multiple ABIs.
- Release artifact: prefer ABI-specific builds or Android App Bundle splits.
- Android 6/API 23 is not a mainline target. Supporting it would require a separate legacy browser-engine flavor and would weaken browser/security compatibility.

## Why GeckoView

GeckoView is a self-contained embeddable browser engine for Android. That makes it a better fit than Android System WebView when the product goal is to ship the browser engine with the app and reduce user setup.

## Automation Boundary

The automation engine should not depend on Android Activity APIs directly. It should talk to a browser adapter.

```text
TaskEngine
  -> BrowserAdapter
       -> GeckoViewAndroidAdapter
       -> PlaywrightDesktopAdapter
```

The Android adapter should expose primitives such as:

- `load(url)`
- `probePage()`
- `click(selector)`
- `eval(script)`
- `observePageState()`
- `observeVideoProgress()`

For GeckoView, these primitives should be implemented through bundled WebExtension scripts and native messaging.

## Compatibility Risks

- GeckoView API evolves quickly. Pin a known-good GeckoView version for releases.
- `geckoview-omni` is large. Use it for early compatibility testing, not final release size.
- Some websites may behave differently in Gecko compared with Chromium. EWT compatibility must be tested before committing fully to GeckoView.
- Native messaging from content scripts requires the extension permissions used in `assets/autoewt/manifest.json`.

## Device Matrix

Before depending on GeckoView in production, test:

- Android 8, 10, 12, 14, 15+
- arm64-v8a and armeabi-v7a
- Low-memory process kill and restore
- Login, cookies, local storage, video playback, background/foreground restore
- Captive network/proxy environments common in schools
