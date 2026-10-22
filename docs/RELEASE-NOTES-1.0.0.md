# SG Sports 1.0.0 — release notes (published)

## TV app (sideload)

- Package: `com.sgsports.tv.v2.open`, version 1.0.0 (versionCode 100), minSdk 24.
- File: `sgsports-1.0.0.apk`
- SHA-256: `096e1962c27cd36bd5ff036c243dc5404942d6dadbd410ebcd22d51d4731c9bf`
- Signer certificate SHA-256: `1fc933f59969cc02c3f8437afdeb79d732e9e4c640bd0e314646fcf9322582af`

Verify: `sha256sum -c SHA256SUMS`, then `apksigner verify --print-certs sgsports-1.0.0.apk` and compare the signer fingerprint. The app carries no built-in server address; its first-run onboarding (Step 1 of 3) asks for your backend address, then a pairing code. This build includes the SGH-314 onboarding changes and replaces the earlier 1.0.0 file; the signer certificate is unchanged. Built from source 65415a3 (no Android source differs after 4e155e3).

## Backend

`docker compose up -d --build` from a clean clone; sign in at `/` as `admin` / `admin` and choose a new password at once (see the operator guide). Open the root address (`/`) for setup; `/setup` is only a compatibility redirect. Clean-install evidence at source 65415a3 (SGH-317): `scripts/compose-clean-install.sh` passed all gates (11 + 13 synthetic checks), backend image `sha256:51fd36443d3adbebb40d67f583251c66b22041fe361e23d145ac0fc024bbe4f4` (ephemeral local build; base `python:3.12.7-slim-bookworm@sha256:60d9996b6a8a3689d36db740b49f4327be3be09a21122bd02fb8895abb38b50d`).

## Known limits

- Provides no content, channels or guide data; you bring your own sources.
- Team artwork is fetched at runtime by your own backend (docs/TEAM-ARTWORK.md); the monogram fallback is unit-tested but was not shown on the emulator.
- Not distributed through Google Play.

## Licence and provenance

Licence: Apache-2.0. ESPN artwork is opt-in (off by default). Launcher icon authored by the project owner. Release signing key backup verified offline by the owner. Published 2026-10-22 as release v1.0.0 of SnickersPepper/sg-sports, exported from reviewed source 0cf9883 as a fresh public history.
