# Release manifest — 1.0.0 (post-SGH-314, unpublished)

| Item | Value |
|---|---|
| APK source commit | `65415a3e346d19bb3d873dc3f2de12df51c3f7ac` (the exact tree the signed APK was built from) |
| Release-metadata commits | `a224f52` (APK, checksums, notes, manifest, export list); `0509b3da313e7c22201c052e3a0b99203c7ac7f5` (release-APK pairing/returning-path evidence, manifest provenance); and the final manifest-provenance commit that adds this line (`git log -- docs/RELEASE-MANIFEST-1.0.0.md`). None changes application source or the APK |
| APK | `artifacts/release/sgsports-1.0.0.apk`, `com.sgsports.tv.v2.open`, 1.0.0 (100) |
| APK SHA-256 | `096e1962c27cd36bd5ff036c243dc5404942d6dadbd410ebcd22d51d4731c9bf` |
| Signer certificate SHA-256 | `1fc933f59969cc02c3f8437afdeb79d732e9e4c640bd0e314646fcf9322582af` (SHA-1 `318941a9008ef7bb1e3157b8cb01112b916a261d`) |
| Backend image | `sha256:51fd36443d3adbebb40d67f583251c66b22041fe361e23d145ac0fc024bbe4f4` (local clean-install build) |
| Base image | `python:3.12.7-slim-bookworm@sha256:60d9996b6a8a3689d36db740b49f4327be3be09a21122bd02fb8895abb38b50d` |

Evidence (SGH-317, 2026-10-22):

- `neutral-release` job: signed build, `apksigner verify`, `check_neutral_apk.sh` (public/private-value scan) passed; `lintVitalNeutralRelease` passed.
- `neutral-gate`: neutral debug and unsigned release builds, `lintNeutralDebug`, APK check passed.
- `export_public.mjs` + `scan_public.mjs`: 171 files, scan passed (the operator-only clean-install worker and its doc are excluded from the public export).
- `compose-clean-install`: passed, exit 0.
- Isolated Android TV emulator only: the release APK installs (versionCode 100) and launches into first-run onboarding "Step 1 of 3 — Connect to your SG Sports backend"; D-pad focus and typed address entry work; BACK and relaunch return to the same step. Screenshots: `artifacts/review/sgh317/`. No provider playback; physical TV untouched.
- Pairing and returning path on the signed release APK (same APK, versionCode 100, installed once; isolated emulator only): typed `http://<lan-address>:18200` (the synthetic metadata-only review backend, no providers) → connection test passed (`06`, `07`) → a one-time six-digit pairing code from the console was entered and accepted, "This TV is paired" Step 3 of 3 (`08`, `09`) → Show my games → app home (`10`) → HOME then relaunch goes straight to the home screen, not onboarding (`11`). No FATAL entries in the app log. The pairing code was single-use and expired; no credentials are in the evidence. Screenshots `05`–`11` in `artifacts/review/sgh317/`.
