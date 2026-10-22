# SG Sports TV app

Kotlin / Jetpack Compose Android TV app that talks to your SG Sports backend.

## Flavors

| Flavor | Purpose | Backend |
| --- | --- | --- |
| `neutral` | Public sideload release. No built-in address, DNS override or certificate authority. | Entered by the user on first launch |
| `design` | Offline interactive design/preview with sample data; never opens a stream. | none |

Maintainers may add private test flavors in `app/private-flavors.gradle` (not part of the public tree).

## Build

```sh
./gradlew testDesignDebugUnitTest
./gradlew assembleNeutralDebug -PenableNeutralBuild=true      # emulator/testing
SG_RELEASE_KEYSTORE=... SG_RELEASE_STORE_PASSWORD=... SG_RELEASE_KEY_ALIAS=... SG_RELEASE_KEY_PASSWORD=... \
  ./gradlew assembleNeutralRelease -PenableNeutralBuild=true  # signed release (see ../docs/RELEASE-SIGNING.md)
```

Requires JDK 17 and Android SDK 35. Without the `SG_RELEASE_*` variables the release APK is produced unsigned.
