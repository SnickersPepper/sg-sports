# SG Sports

SG Sports is a self-hosted sports guide and Android TV / Google TV player front end. You run the **backend** on your own Linux server with Docker Compose, point it at **your own** IPTV playlist/guide sources (direct M3U/M3U8 and XMLTV, an Xtream-style login, an IPTVBoss export, or optionally Dispatcharr/Jellyfin), and sideload the **TV app** (an APK) on your own Android/Google TV. It is free and open source, is not distributed through Google Play, and needs no hosted SG Sports account, paid service or AI service.

SG Sports does not provide any channels, playlists, guide data or streams. You bring your own, and you are responsible for having the right to use them.

## What you need

- A Linux host with Docker Engine and the Docker Compose plugin (`docker compose version`).
- An Android TV / Google TV (Android 7.0+, API 24) that allows sideloading, on the same network as the host.
- Your own sources (any of): an M3U/M3U8 playlist, an XMLTV (`.xml`/`.xml.gz`) guide, Xtream-style credentials, an IPTVBoss-generated M3U + XMLTV export.

## Quick start

```sh
git clone <this repository> sgsports && cd sgsports
docker compose up -d --build
```

1. Open `http://<host>:8099/`, the administration console (the older `/setup` address still redirects here) (by default only `127.0.0.1` is published; see the [operator guide](docs/OPERATOR-GUIDE.md) to expose it on your LAN and for TLS).
2. Sign in with the starting username `admin` and password `admin`. You must choose a new password straight away; nothing else in the console works until you do. (Lost it later? See *Recovery* in the operator guide.)
3. Add your sources and guide in the console. Secrets you enter are write-only.
4. Install the TV app (below), start it, enter your backend address (for example `http://192.168.1.20:8099` on a home network, or `https://sports.example.org`), then choose **Create a TV pairing code** in the console and enter that code on the TV.

## TV app

Download the release APK and its checksum from the project's GitHub release page and verify it before installing:

```sh
sha256sum -c SHA256SUMS            # or: shasum -a 256 -c SHA256SUMS
adb connect <tv-address>:5555 && adb install -r sgsports-<version>.apk
```

The app is controlled with the TV remote's D-pad. It carries no built-in server address: it asks for yours on first launch, checks that an SG Sports backend answers, and remembers it. Changing the backend later (Settings → Connection) signs out and never sends your old credential to the new address.

## Documentation

- [Operator guide](docs/OPERATOR-GUIDE.md): ports, TLS/reverse proxy, secrets, backup/restore, upgrade, rollback, reset, uninstall, troubleshooting, supported capabilities.
- [Release signing](docs/RELEASE-SIGNING.md): how the APK is signed without committing keys, and how to verify artifacts.
- [Licensing and asset audit](docs/LICENSING-AUDIT.md): dependency licenses and third-party material status.

## Status

Licensed under the [Apache License 2.0](LICENSE). Release 1.0.0 artifacts, checksums and provenance: `docs/RELEASE-MANIFEST-1.0.0.md`.
