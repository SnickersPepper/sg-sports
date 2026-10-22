# Release signing and artifact verification

Signing keys are never committed. `*.jks`, `*.keystore`, `secrets/` and `private/` are git-ignored, and `scripts/scan_public.mjs` fails the release if key material is present.

## Create the key once (maintainer, offline)

```sh
keytool -genkeypair -v -keystore sgsports-release.jks -alias sgsports -keyalg RSA -keysize 4096 -validity 10000
```

Back the keystore up in at least two offline places. Losing it means users must uninstall to update; leaking it means anyone can ship updates that Android accepts as yours.

## Sign in CI or locally

The build reads protected environment values only:

| Variable | Meaning |
| --- | --- |
| `SG_RELEASE_KEYSTORE` | Absolute path to the keystore file (a CI secret file, restored for the job only) |
| `SG_RELEASE_STORE_PASSWORD` | Keystore password |
| `SG_RELEASE_KEY_ALIAS` | Key alias |
| `SG_RELEASE_KEY_PASSWORD` | Key password |

```sh
cd android && ./gradlew assembleNeutralRelease -PenableNeutralBuild=true
```

With the variables unset the build still succeeds but produces `app-neutral-release-unsigned.apk`, which Android will not install. On GitHub Actions store the four values as encrypted repository/environment secrets, write the keystore from a base64 secret to a temporary file, and delete it after the job. Never echo these values or run the job for untrusted pull requests.

## Publish and verify

```sh
cd release && sha256sum sgsports-<version>.apk > SHA256SUMS
apksigner verify --print-certs sgsports-<version>.apk     # shows the signer certificate fingerprint
```

Publish the APK, `SHA256SUMS`, the version, the signer certificate SHA-256 fingerprint and the backend image/commit it was built with in the release notes. Users verify with `sha256sum -c SHA256SUMS` before installing.
