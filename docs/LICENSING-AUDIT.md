# Licensing and third-party material audit

Status: **licence chosen: Apache-2.0** (`LICENSE`, `NOTICE`; owner decision recorded in SGH-310, 2026-10-22). Artwork and roster blockers resolved (SGH-302). The launcher icon was created by the project owner, who authorizes its release under the project licence. The ESPN artwork source is optional and off by default on a fresh install; operators who enable it accept ESPN's terms themselves. Remaining before publication: independent review of the final diff (SGH-299).

## 1. Code dependencies

| Component | Used for | License |
| --- | --- | --- |
| Python 3.12 standard library | Backend (no third-party Python packages) | PSF |
| Debian `ffmpeg`, `tesseract-ocr` (installed in the image at build time on the user's host) | Stream verification | LGPL/GPL (ffmpeg build options), Apache-2.0; not redistributed by this repository |
| AndroidX / Jetpack Compose / Material3 / Media3 | TV app | Apache-2.0 |
| OkHttp 4.12 | TV app networking | Apache-2.0 |
| Gradle wrapper | Build | Apache-2.0 |

## 2. Third-party artwork and data — resolution (SGH-302)

**Decision: runtime retrieval on the operator's host (option a), not bundling.** The public tree, the neutral APK and the Docker image carry no third-party team or league artwork and no personal roster. Design and operator contract: `docs/TEAM-ARTWORK.md`.

| Material | Origin | Public tree | Private (maintainer) flavors |
| --- | --- | --- | --- |
| `logos/espn_*`, `league_*`, `espn.png`, ESPN wordmark, `sec.png` | ESPN CDN | **Removed.** Fetched per installation by the backend (ESPN public metadata; operator may switch off) and cached in the data volume. | Kept in `src/private/assets-art` |
| `logos/team_*`, `conf_*`, `drawable/lady_vols.xml` (club/university marks) | Club/university sites | **Removed** (a blank placeholder drawable keeps the build compiling). Bundled-only identities show a colour monogram. | Kept (`assets-art`, `res-brand`) |
| `logos/banana_*`, `lvbp_*` | Team/league marks | **Removed.** Not on any public source; monogram. | Kept |
| `banners/*.jpg` (hero imagery), design flavor sample banners | Unknown provenance | **Removed** (`src/design/assets` excluded from the export). Heroes draw without a photograph. | Kept |
| `favorites.json`, `teams.json` (the maintainer's six favorites) | Personal | **Replaced** by an empty roster (`{"teams":[]}`, `{}`); the scan fails if it is non-empty. Favorites come from backend settings and the catalog. | Kept (`assets-art`) |
| `team-identities.json`, `leagues.json`, `team-brand-colors.json`, `ncaa_conferences.json` | Factual names, aliases, ESPN identifiers and hand-chosen colours; **no images** | Kept (facts, not artwork). Their `logos/...` file names are only lookup keys. | Kept |
| `lvbp_data.json` | Maintainer's provider stream mapping | Not part of the public default (`private/`) | — |
| `logos/sgsports.png` | Project mark | Kept; listed in `redistributable-assets.txt` | Kept |
| `app_icon.png` (launcher icon) | User-supplied launcher art | **Open:** the maintainer must confirm they may license it, or replace it before release. | Kept |

Provider channel logos and the private certificate authority were already outside the public path (`android/app/src/private`).

**Runtime artwork terms.** ESPN's public endpoints are undocumented and ESPN publishes no licence for using or redistributing the logos. The design therefore never bundles, mirrors or republishes them; each installation fetches only on demand or on the operator's explicit refresh, stores the result privately, serves it only to its own paired TVs, and the operator can switch the source off or use their own server or pack. Operators are told this in Setup and in the operator guide. Logos remain their owners' trademarks; no endorsement is implied. If the terms are unacceptable to the project owner, ship with the source set to *None* by default (one constant: `artwork.DEFAULT_MODE`).

`scripts/scan_public.mjs` fails the release tree if any image under `assets/logos` or `assets/banners` is not listed in `docs/redistributable-assets.txt`, or if a bundled default roster is not empty. `scripts/public-exclude.txt` keeps `src/private`, `src/design/assets` and the old screenshots out of the export.

## 3. Repository history

Git history contains deployment-specific values (private hostnames, addresses, provider host names, certificate material, `.paperclip` agent records). **Publish from a new history**: `node scripts/export_public.mjs OUT` writes the tracked public tree (see `scripts/public-exclude.txt`); initialise a fresh repository from it. Do not push this repository's history.

## 4. Protocol and service terms

The project does not include or endorse any IPTV service. ESPN public endpoints used for schedules/scores and, by default, team logos are subject to their terms (see section 2); check them before enabling background refresh at scale.
