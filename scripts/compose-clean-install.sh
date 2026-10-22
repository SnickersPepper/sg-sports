#!/bin/sh
# Clean-install acceptance on a Docker host: fresh export, empty volumes, synthetic data only, no provider contact.
#   scripts/compose-clean-install.sh [REPO_DIR]   (REPO_DIR defaults to this repository; its tracked files are exported with git archive)
# Records the image digest, runs first sign-in/forced password change/configure/pair, restart persistence, redacted export + secrets restore into a second empty
# instance, rollback, and an upgrade-with-retention check. Removes every container, volume and image it created.
set -eu
docker compose version >/dev/null 2>&1 || { echo "FAIL: Docker Compose v2 plugin missing (docker compose version); install docker-compose-plugin on the host first" >&2; exit 2; }
repo=$(CDPATH= cd -- "${1:-$(dirname -- "$0")/..}" && pwd)
work=$(mktemp -d); id=$(od -An -N4 -tx1 /dev/urandom | tr -d " \n"); proj="sgclean$id"; projb="sgcleanb$id"; projc="sgcleanc$id"
porta=${SG_CLEAN_PORT_A:-18399}; portb=${SG_CLEAN_PORT_B:-18400}; portc=${SG_CLEAN_PORT_C:-18401}
smoke="$repo/scripts/compose_smoke.py"
# ports derived from the random id so concurrent runs and other local services do not collide
porta=$((20000 + 0x$(printf %s "$id" | cut -c1-4) % 9000 * 3)); portb=$((porta + 1)); portc=$((porta + 2))
# Explicit, non-overlapping subnets (one per instance; the worker is serialized) so Docker's automatic address pools are never needed.
# Override with SG_CLEAN_SUBNET_A/B/C on hosts where the defaults are in use. Networks stay non-internal because Docker cannot publish ports from internal ones.
suba=${SG_CLEAN_SUBNET_A:-172.30.52.0/28}; subb=${SG_CLEAN_SUBNET_B:-172.30.52.16/28}; subc=${SG_CLEAN_SUBNET_C:-172.30.52.32/28}
python3 "$repo/scripts/compose_subnets.py" validate "$suba" "$subb" "$subc" || exit 2
dca="docker compose -f $work/src/compose.yaml -f $work/net-a.yaml -p $proj"; dcb="docker compose -f $work/src/compose.yaml -f $work/net-b.yaml -p $projb"; dcc="docker compose -f $work/src/compose.yaml -f $work/net-c.yaml -p $projc"
cleanup() { for d in "$dca" "$dcb" "$dcc"; do (cd "$work/src" 2>/dev/null && $d down -v --rmi local --remove-orphans >/dev/null 2>&1) || true; done; rm -rf "$work"; }
trap cleanup EXIT
mkdir -p "$work/src" && git -C "$repo" archive HEAD | tar -x -C "$work/src"
cd "$work/src"
python3 scripts/compose_subnets.py override "$suba" > "$work/net-a.yaml"; python3 scripts/compose_subnets.py override "$subb" > "$work/net-b.yaml"; python3 scripts/compose_subnets.py override "$subc" > "$work/net-c.yaml"
export SG_AUTO_REFRESH=0
SG_PORT=$porta $dca up -d --build --wait
$dca images --format json || true
echo "IMAGE_ID: $($dca images -q | head -1)"
base=http://127.0.0.1:$porta
python3 "$smoke" setup "$base" "$work/a.json"
$dca restart && sleep 3
n=0; until curl -fsS "$base/health" >/dev/null 2>&1 || python3 -c "import urllib.request;urllib.request.urlopen('$base/health',timeout=2)"; do n=$((n+1)); [ $n -gt 40 ] && exit 1; sleep 1; done
python3 "$smoke" persist "$base" "$work/a.json"
# upgrade: rebuild the same source (new image id allowed) with the volume kept
SG_PORT=$porta $dca up -d --build --wait
python3 "$smoke" persist "$base" "$work/a.json"
# team logos: stand-in provider inside the container -> populate -> provider stopped -> restart -> still served from the data volume
python3 "$smoke" artwork-populate "$base" "$work/a.json" "$dca exec -T sgsports"
$dca restart && sleep 3
n=0; until python3 -c "import urllib.request;urllib.request.urlopen('$base/health',timeout=2)"; do n=$((n+1)); [ $n -gt 40 ] && exit 1; sleep 1; done
python3 "$smoke" artwork-persist "$base" "$work/a.json"
# second empty instance for redacted export + separate secrets restore
SG_PORT=$portb $dcb up -d --build --wait
python3 "$smoke" restore "$base" "$work/a.json" http://127.0.0.1:$portb "$work/b.json"
python3 "$smoke" rollback "$base" "$work/a.json"
# operator recovery from inside the container (new password, then back to admin/admin with a forced change)
python3 "$smoke" recover "$base" "$work/a.json" "$dca exec -T sgsports"
# an upgraded volume that only has a legacy administrator token: one-time migration, no admin/admin, no data loss
SG_PORT=$portc $dcc up -d --build --wait
python3 "$smoke" migrate http://127.0.0.1:$portc "$dcc exec -T sgsports" "$dcc restart"
echo "COMPOSE CLEAN INSTALL PASSED"
