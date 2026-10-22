# Operator guide

## 1. Install

```sh
git clone <this repository> sgsports && cd sgsports
docker compose up -d --build
docker compose ps                   # State: running, Health: healthy
docker compose logs sgsports        # a fresh install says: sign in at / as admin / admin and change the password
```

The image runs as an unprivileged user (uid 10001) with a read-only root filesystem, all Linux capabilities dropped, `no-new-privileges`, and bounded memory/CPU/PIDs. No Docker socket and no host path is mounted. State lives in the named volume `sgsports-data` (mounted at `/data`).

Environment (set in your shell or an `.env` file next to `compose.yaml`):

| Variable | Default | Meaning |
| --- | --- | --- |
| `SG_BIND` | `127.0.0.1` | Host address the port is published on. Use `0.0.0.0` or a LAN address to reach it from the TV. |
| `SG_PORT` | `8099` | Host port. |
| `SG_AUTO_REFRESH` | `1` | Background metadata refresh. `0` disables it. |
| `SG_ALLOWED_ORIGINS` | empty | Comma-separated extra browser origins (only needed behind a proxy that rewrites `Host`). |
| `SG_ADMIN_TOKEN_FILE` | unset | Only for upgrades: a secret file holding the old administrator token, accepted once by the migration screen (section 3). |
| `SG_COOKIE_SECURE` / `SG_TRUST_PROXY` | unset | Mark the sign-in cookie `Secure` when HTTPS is in use (section 3). |
| `SG_ADMIN_IDLE_SECONDS` / `SG_ADMIN_SESSION_SECONDS` | `1800` / `43200` | Idle and absolute administrator session limits. |

`/health` is an unauthenticated liveness check and carries no configuration.

## 2. Ports, TLS and reverse proxy

The backend speaks plain HTTP on 8099. Do not expose it to the internet as-is.

- **Home network only:** set `SG_BIND=0.0.0.0` (or the host's LAN address) and give the TV `http://<lan-address>:8099`. The TV app accepts plain `http://` only for private/home-network addresses (loopback, RFC 1918, link-local, `.local`/`.lan`/`.home` names, single-label names).
- **Anything else:** put any TLS-terminating reverse proxy in front (Caddy, nginx, Traefik, a cloud tunnel…) and give the TV the `https://` address. Caddy and AdGuard are not required. Minimal Caddy example: `sports.example.org { reverse_proxy 127.0.0.1:8099 }`.
- Keep `SG_BIND=127.0.0.1` when a proxy on the same host fronts the service.
- If the proxy changes the `Host` header, set `SG_ALLOWED_ORIGINS=https://sports.example.org`.
- There is no firewall or DNS change in this project; configure those yourself.

## 3. Administrator sign-in

A new instance has one administrator account: username `admin`, password `admin`. Open the root address (for example `http://<host>:8099/`; the older `/setup` address redirects to it), sign in, and you are taken straight to a **Choose a new password** screen. Until you change the password nothing else works: setup, settings, sources, DVR, exports, TV pairing and every other administrator action are refused; you can only change the password or sign out. Change the password immediately after the first start, and do not publish a fresh instance to a network you do not trust before you have.

- **Passwords** are stored only as a salted scrypt hash (32 MiB, per-password salt) in `/data/admin-auth.json` (mode 0600). They are never logged, returned or exported. Choose 10 to 256 characters; `admin`, `password` and your username are refused.
- **Sessions** are opaque ids kept server-side (only their SHA-256 is stored in `/data/admin-sessions.json`) and sent in an `HttpOnly`, `SameSite=Strict` cookie. They expire after 30 minutes idle or 12 hours in total (`SG_ADMIN_IDLE_SECONDS`, `SG_ADMIN_SESSION_SECONDS`), end at **Sign out**, and are all revoked when the password changes. Every write also needs a per-session CSRF token and, from a browser, a matching `Origin`.
- **HTTPS:** the cookie gets the `Secure` flag (and the `__Host-` prefix) when `SG_COOKIE_SECURE=1`, or when `SG_TRUST_PROXY=1` and your TLS-terminating proxy sends `X-Forwarded-Proto: https`. Plain HTTP on a home network works without either; behind a proxy set both `SG_TRUST_PROXY=1` and (if it rewrites `Host`) `SG_ALLOWED_ORIGINS`.
- **Throttling:** after three wrong attempts sign-in waits 1, 2, 4… seconds (at most 30 s for the account and 5 min per client address); the wait expires by itself, and a restart clears it. Failures always read "The username or password was not accepted."
- **Account page:** change the password or (optionally) the username later under *Security and administrator account* in the console (`/`). Everything needs the current password.
- **TVs and integrations are separate.** TV pairing codes and TV tokens, and the Jellyfin/Dispatcharr/provider credentials, are unchanged by any of this; an administrator session is never accepted as a TV token and vice versa. There is no administrator bearer token any more, so scripts sign in with `POST /v1/auth/login` and send the returned cookie and `X-SG-CSRF` header (see `scripts/compose_smoke.py`).

### Upgrading an installation that used the old administrator token

Nothing is reset to `admin` / `admin`. After the upgrade the root console shows **Move to a username and password**: enter the old token once (a claimed token, `SG_ADMIN_TOKEN_FILE` or `secrets/api_token`), choose a username and password, and you are signed in. The token then stops authenticating anything, and `admin_token.sha256` is deleted. A token file you supplied yourself is left alone; you can delete it and the `SG_ADMIN_TOKEN_FILE` line afterwards. Settings, pairing and the data volume are untouched.

### Recovery (lost password)

Recovery needs a shell on the Docker host; there is no network route that resets the account.

```sh
docker compose exec sgsports python adminauth.py reset                  # prompts twice, hidden input
printf '%s' 'a-long-new-password' | docker compose exec -T sgsports python adminauth.py reset   # non-interactive
docker compose exec sgsports python adminauth.py reset --default        # back to admin / admin, change forced at next sign-in
docker compose exec sgsports python adminauth.py reset --username keeper   # also rename
```

The command never prints the password, signs out every existing session, retires any legacy token and clears the sign-in backoff. Configuration and paired TVs are kept. (Note that the non-interactive form puts the password in your shell history unless you read it from a file or variable.)

If `admin-auth.json` exists but is unreadable or invalid, sign-in is refused (HTTP 503 `auth_state_invalid`) rather than falling back to `admin` / `admin` or to a legacy token; run the recovery command to replace it.

### Rollback of this change

Back up the volume first (section 6). To return to a release that uses the administrator token, restore the pre-upgrade volume copy; a volume that already holds `admin-auth.json` is not understood by the old release, which would issue a new claim code. The files are `admin-auth.json`, `admin-sessions.json` and `.admin-auth.lock`; keep them out of any shared archive.

## 4. Configure sources

Everything is configured in the administration console at the root address `/` (or the versioned settings API behind it); no file edits or APK rebuilds are needed.

- **Direct M3U/M3U8** and **XMLTV (`.xml` / `.xml.gz`)**: add a playlist source and a guide source by URL.
- **Xtream-style**: add an account (host, username, password) and an Xtream source.
- **IPTVBoss**: use the generated M3U and XMLTV exports (kind "IPTVBoss export"). Map a merged export to every underlying account.
- **Optional Dispatcharr / Jellyfin**: add them as external integrations in the console. Both are skippable; baseline operation needs neither.
- **Connection limits** belong to the underlying subscription account, not to a playlist name. Several sources that share one subscription must point at the same account so its limit is shared.

Secret fields (URLs with credentials, passwords, API keys) are write-only: stored by the backend, never returned, never in exports or logs.

## 4a. Team logos

The public app and repository contain no team, league or club logos. Your backend fetches them at runtime, caches them in the data volume (`/data/artwork`, survives container recreation) and serves them to paired TVs; a team without a logo shows a colour monogram, and one missing logo never affects a page. Configure it under **Artwork** in the console (`/`). Logos are **Off** on a fresh install; an installation that already existed keeps the ESPN source it had, recorded as an explicit setting at upgrade:

| Source | What it does |
| --- | --- |
| ESPN public sports metadata (explicit opt-in; the console asks you to accept ESPN's terms) | Resolves team/league identities to ESPN's public logo URLs when a TV asks, or when you press **Fetch / refresh logos now** (which also pre-fetches the whole catalog). Retrieval is not a licence to redistribute; see `docs/TEAM-ARTWORK.md`. |
| My own web server | Reads `<base address>/<key>.png` (then `.webp`, `.jpg`) from an address you control. The address is a write-only secret. |
| A logo pack | Your own files in `/data/artwork/pack` (for example `docker compose cp ./my-pack/. sgsports:/data/artwork/pack/`) plus an optional `pack.json`; nothing is fetched. |
| None (default for a fresh install) | No artwork is fetched or served; colour monograms are shown. |

The status panel shows the source, its attribution and licence note, cache size, per-logo source and retrieval time, failures, and refresh progress. **Clear cached logos** empties the cache; it is rebuilt on demand. The source setting is part of the redacted export/restore (the base address is a secret to supply again); the cached images are derived data and are not part of exports.

## 5. Pair a TV

In the console choose **Create a TV pairing code** and enter the six digits on the TV within five minutes. Paired TVs are listed in the console and can be revoked there; a revoked TV must pair again. Each TV can be assigned a profile with its own preferences.

## 6. Backup, restore, upgrade, rollback

- **Redacted export** (console → Backup): configuration with placeholders only. Restoring it into another empty instance needs the secret values supplied separately in the restore form (`section/id/field` → value).
- **Full offline backup** of the settings store:
  ```sh
  docker compose exec sgsports python scripts/ops_backup.py create /data /data/backup.json
  docker compose cp sgsports:/data/backup.json ./backup.json   # contains no secret values
  docker compose exec sgsports python scripts/ops_backup.py verify /data/backup.json
  ```
  Also snapshot the volume itself for a complete copy, including secrets and the administrator credential database (`admin-auth.json`, a salted hash; restoring it restores that password): `docker run --rm -v sgsports_sgsports-data:/data -v "$PWD":/b busybox tar czf /b/sgsports-data.tgz -C /data .` (stop the stack first for a consistent copy; the archive contains secrets, so protect it).
- **Upgrade:** take a backup, then `git pull && docker compose up -d --build`. Settings migrate forward automatically and the volume is kept.
- **Rollback:** restore a previous export from the console (a restore first records a rollback bundle), or `git checkout <previous tag> && docker compose up -d --build` and restore the pre-upgrade backup. A restore from an export made by a newer version is refused by an older one rather than applied.
- **Cached logos** are not in an export or backup bundle; after a restore they are fetched again on demand (or press *Fetch / refresh logos now*). A logo pack in `/data/artwork/pack` is yours to back up with the volume.
- **Reset:** `docker compose down -v` deletes the volume (all configuration, pairing and the administrator account). The next start is a fresh install: `admin` / `admin` with a forced password change.
- **Uninstall:** `docker compose down` keeps your data; `docker compose down -v --rmi local` removes data and the image. Nothing is installed outside Docker.

## 7. Troubleshooting

| Symptom | Check |
| --- | --- |
| Forgot the administrator password | Recovery in section 3 (`python adminauth.py reset`). |
| The root console shows *Move to a username and password* | The volume came from a token-based release; enter the old token once (section 3). |
| TV says "No SG Sports backend answered" | Address/port reachable from the TV? `SG_BIND` must not be `127.0.0.1` for LAN use; `curl http://<address>:8099/health`. |
| TV rejects `http://` address | Plain http is only for home-network addresses; use https through a proxy otherwise. |
| `429 rate_limited` | Too many wrong passwords or codes; wait for `retry_after` seconds and retry. |
| Container unhealthy | `docker compose logs sgsports`; the data volume must be writable by uid 10001. |

## 8. Supported and unsupported

Supported: browsing the sports guide and teams, source/guide configuration, per-profile preferences, paired-TV playback of the sources you configure, optional recording through your own Jellyfin or Dispatcharr, bounded connection budgets.

Not provided: any content, channels or guide data; Google Play distribution; a hosted service; automatic discovery that bypasses your configuration; guarantees that a selected team or competition has a schedule, feed or recording on your sources. A favorite is a preference, not a promise of coverage.
