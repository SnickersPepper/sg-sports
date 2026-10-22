# Team and league artwork

Status: implemented in SGH-302. This document is the design record, the provider terms summary and the operator contract.

## Principles

1. **The public release redistributes no third-party logos.** The repository, the neutral APK and the Docker image carry no team, league, club or university marks, no hero photographs and no personal favorite list. `scripts/scan_public.mjs` fails a release tree that does.
2. **Runtime retrieval is not redistribution.** Each installation fetches artwork on the operator's own host from a source the operator chooses. That is the operator's use, under the provider's terms; it grants no right to republish.
3. **The operator decides.** The source can be changed or switched off in Setup. Nothing is fetched while it is off.
4. **Failure is quiet.** Missing, failed, unlicensed or still-loading art is a colour monogram. A failed logo never breaks a page.

## Architecture

```
TV app --(paired bearer)--> GET /tv/artwork/<key> --> backend cache (/data/artwork) --> source
        <-- validated image, ETag ----                 (miss/stale)  conditional GET --> ESPN | operator server | pack
```

**Keys** are canonical identities, never URLs: `espn_<sport>_<league>_<teamId>` and `league_<sport>_<league>` (the stems the app's bundled identity data already uses) or a backend catalog ID (`team.football.men.nfl.kansas-city-chiefs`, `league.football.nfl`). The backend maps a key to a source URL only through fixed tables (ESPN sport/league paths, the catalog), so a key can never choose a host.

**Sources** (setting `integrations[kind=artwork].options.mode`; absent = `off` on a fresh install; an upgraded existing install is migrated to an explicit `espn`): `espn`, `url`, `pack`, `off`. The `url` base address is a write-only secret and is never stored in the cache index, status or exports. A `pack` is a directory plus an optional versioned manifest:

```json
{"schema_version": 1, "name": "My pack", "license": "CC0 (own work)", "attribution": "Me", "entries": {"team.football.men.nfl.kansas-city-chiefs": "kc.png"}}
```

Without a manifest, files named `<key>.png|jpg|jpeg|webp` are used. An invalid manifest disables the pack and is reported in Setup.

**Fetching** is bounded: 8 s timeouts, four concurrent fetches, at most three redirects each re-checked against the allow-list (ESPN: https and `*.espncdn.com` / `site.api.espn.com` only, public addresses only; own server: the same scheme, host and port), 1 MB per image and 6 MB per metadata response, concurrent requests for one key collapsed, a 15-minute back-off after a failure.

**Validation.** Type comes from the bytes: PNG, JPEG and WebP only, at most 2048 x 2048 where the dimensions are readable. **SVG, HTML and everything else are refused** (they can carry script and the TV cannot render them anyway). Images are served with `X-Content-Type-Options: nosniff` and a restrictive CSP.

**Cache.** Per key: file, content type, size, SHA-256, source (`source`, `source_host`, and `source_url` only for the public ESPN source), retrieval time, `ETag`, `Last-Modified`, and the last failure. Stale entries (7 days) are revalidated with a conditional request; a failed revalidation keeps and serves the last good copy. Limits: 64 MB and 3000 entries, least recently used evicted. A file whose hash no longer matches is not served. Entries are tied to the source that produced them, so switching source never serves the previous source's art. A damaged index is set aside and rebuilt.

**Android.** `rememberAssetImage` uses a bundled asset when the build has one (private flavors); otherwise it asks the configured backend by key, caches the bytes on the device (24 MB, 1500 files, refreshed after 14 days, last good kept when offline), and decodes with a size bound. Four requests at once; a key with no result is not asked for again for five minutes. The monogram (initials plus a name-derived colour) is drawn until, or instead of, an image.

## Provider terms

| Source | Terms and status |
| --- | --- |
| ESPN public sports metadata | The endpoints (`site.api.espn.com`, `a.espncdn.com`) are undocumented public endpoints. ESPN publishes no licence to use or redistribute the logos; their terms (https://disneytermsofuse.com/) govern. This project therefore never bundles or republishes them, keeps the fetch per-installation and on demand, and makes the source easy to disable. **Operators are responsible for accepting ESPN's terms for their own use.** If ESPN's terms or endpoints change, the source degrades to monograms with no app update. |
| Operator server / pack | The operator's own responsibility. The pack manifest records name, licence and attribution, which Setup shows. |

Logos remain the trademarks of their owners; the display here does not imply endorsement.

## Private builds

The maintainer's Living Room / Studio / Design flavors keep the complete bundled set (`android/app/src/private/assets-art`, `res-brand`), excluded from the public export. Bundled assets win over backend art, so those builds are unchanged.

## Verification

- Unit: `tests/test_artwork.py` (validation, fetch policy, cache, outage and last-good, settings contract, export/restore, routes), `ArtworkStoreTest` (Android).
- Release scan: `node scripts/export_public.mjs OUT && node scripts/scan_public.mjs OUT`.
- Clean Compose: `scripts/compose-clean-install.sh` runs `compose_smoke.py artwork` against a stand-in logo server and proves populate, restart/recreate persistence and provider-down serving. It needs a Docker host.
