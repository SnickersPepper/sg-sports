#!/bin/sh
# Process-level clean-install smoke (no Docker): two disposable backends on loopback, empty data dirs, no network egress needed.
# Exercises sign-in with the forced first password change, synthetic configuration, pairing, restart persistence, redacted export + secrets restore into an empty instance, rollback.
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
work=$(mktemp -d); pids=""
cleanup() { for p in $pids; do kill "$p" 2>/dev/null || true; done; rm -rf "$work"; }
trap cleanup EXIT
start() { # name port
  mkdir -p "$work/$1/data" "$work/$1/secrets"
  ( cd "$root" && SG_DATA="$work/$1/data" SG_SECRETS="$work/$1/secrets" SG_AUTO_REFRESH=0 SGSPORTS_LEGACY_IMPORT=off PORT=$2 python3 -c "
import app, sys
from http.server import ThreadingHTTPServer
app.adminauth.prepare(app.DATA, app.SECRETS, lambda m: None)
ThreadingHTTPServer(('127.0.0.1', $2), app.Handler).serve_forever()" ) >"$work/$1.log" 2>&1 &
  echo $!
}
wait_up() { n=0; until python3 -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:$1/health',timeout=1)" 2>/dev/null; do n=$((n+1)); [ $n -gt 40 ] && { cat "$work"/*.log; exit 1; }; sleep 0.5; done; }
smoke="$root/scripts/compose_smoke.py"
# stands in for `docker compose exec sgsports`: runs python3 inside the repository against one instance's data directory
cat > "$work/pyexec" <<EOF2
#!/bin/sh
data=\$1; shift; shift
cd "$root" && SG_DATA="\$data" SG_SECRETS="\$data/../secrets" SG_AUTO_REFRESH=0 exec python3 "\$@"
EOF2
chmod +x "$work/pyexec"
a=$(start a 18099); pids="$a"; wait_up 18099
python3 "$smoke" setup http://127.0.0.1:18099 "$work/a.json"
kill "$a"; wait "$a" 2>/dev/null || true
a=$(start a 18099); pids="$a"; wait_up 18099
python3 "$smoke" persist http://127.0.0.1:18099 "$work/a.json"
b=$(start b 18100); pids="$pids $b"; wait_up 18100
python3 "$smoke" restore http://127.0.0.1:18099 "$work/a.json" http://127.0.0.1:18100 "$work/b.json"
python3 "$smoke" rollback http://127.0.0.1:18099 "$work/a.json"
python3 "$smoke" recover http://127.0.0.1:18099 "$work/a.json" "sh $work/pyexec $work/a/data"
c=$(start c 18101); pids="$pids $c"; wait_up 18101
python3 "$smoke" migrate http://127.0.0.1:18101 "sh $work/pyexec $work/c/data" true
echo "LOCAL SMOKE PASSED"
