#!/usr/bin/env bash
# remodel-units.sh — point the box's four services at the rockstar package, or back (2026-10-14 remodel).
#
# The package and the old flat modules sit side by side on the box after deploy.sh; which one runs is decided by each
# unit's ExecStart. This script changes ONLY that, with one drop-in per service, and never edits the box's unit files:
#
#   bash scripts/remodel-units.sh --check      # what each service will run, read from systemd; nothing changes
#   bash scripts/remodel-units.sh --install    # write the four drop-ins, daemon-reload, restart what was running
#   bash scripts/remodel-units.sh --rollback   # remove them, daemon-reload, restart what was running: the old code
#
# Production runs are John's (OPS-5): an agent builds and rehearses this; John runs it. The box's address is
# scripts/box.conf, as for deploy.sh. Any ssh failure stops it, and a stop part-way says so (Orly, S3).

set -euo pipefail

ROOT="$(CDPATH= cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
MODE="${1:-}"
[[ "$MODE" =~ ^--(check|install|rollback)$ ]] || { echo "usage: remodel-units.sh --check | --install | --rollback" >&2; exit 2; }

unset BOX_SERVER_OVERRIDE
builtin source "$ROOT/scripts/box.sh" ""
SSH_OPTS=(-o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=2 -o BatchMode=yes)
SUDO=""; [[ "${SERVER%%@*}" != "root" ]] && SUDO="sudo -n"
APP=/opt/rockstar-pipeline
DROPIN=remodel-package.conf
NAMES=(rockstar-worker rockstar-webhook rockstar-poll-drive rockstar-renew-watches)
STARTS=(
  "$APP/venv/bin/python -m rockstar.service.worker"
  "$APP/venv/bin/uvicorn rockstar.service.webhook_service:app --host 127.0.0.1 --port 8000 --workers 1"
  "$APP/venv/bin/python -m rockstar.service.poll_drive_changes"
  "$APP/venv/bin/python -m rockstar.service.renew_watches"
)

remote() { ssh -n "${SSH_OPTS[@]}" "$SERVER" "$@"; }  # -n: never read the caller's input
fail() { echo "[units] FAIL: $*" >&2; exit 1; }

echo "[units] box: $SERVER"
remote true || fail "cannot reach $SERVER (nothing changed)"

# Every service's state, read once and up front. A short or failed answer stops here, before anything changes: a
# service that can't be read must never be taken for a stopped one (it would then not be restarted).
states=$(remote "for u in ${NAMES[*]}; do printf '%s %s\n' \$u \$(systemctl is-active \$u 2>/dev/null || true); done") \
  || fail "could not read the services' state (nothing changed)"
[[ $(wc -l <<<"$states" | tr -d ' ') -eq ${#NAMES[@]} ]] || fail "expected ${#NAMES[@]} service states, got: $states"

if [[ "$MODE" == "--check" ]]; then
  for u in "${NAMES[@]}"; do
    # What systemd will actually run (ExecStart after every drop-in), and whether a daemon-reload is still owed.
    remote "printf '%-24s %-9s ' $u \$(systemctl is-active $u 2>/dev/null || true);
            x=\$(systemctl show $u -p ExecStart --value 2>/dev/null | grep -o 'argv\[\]=[^;]*' | head -1 | cut -c8-);
            r=\$(systemctl show $u -p NeedDaemonReload --value 2>/dev/null);
            d=\$( [ -f /etc/systemd/system/$u.service.d/$DROPIN ] && echo 'drop-in' || echo 'no drop-in');
            echo \"\$d | runs: \${x:-?}\${r:+ | reload owed: \$r}\"" || fail "lost ssh reading $u"
  done
  exit 0
fi

# The package must be on the box before a unit points at it: deploy.sh first.
remote "test -f $APP/rockstar/service/worker.py && test -f $APP/rockstar/__init__.py" \
  || fail "$APP/rockstar is not on the box — run scripts/deploy.sh first; nothing changed"

# From here a stop is a partial change: say so, and how to get back.
trap 'echo "[units] STOPPED PART-WAY: some drop-ins may be changed and not loaded. Run: bash scripts/remodel-units.sh --rollback, then --check" >&2' ERR

for i in "${!NAMES[@]}"; do
  u=${NAMES[$i]}
  if [[ "$MODE" == "--install" ]]; then
    remote "$SUDO install -d -m 755 /etc/systemd/system/$u.service.d && printf '%s\n' '# remodel 2026-10-14: run the rockstar package (scripts/remodel-units.sh)' '[Service]' 'ExecStart=' 'ExecStart=${STARTS[$i]}' | $SUDO tee /etc/systemd/system/$u.service.d/$DROPIN >/dev/null"
    echo "[units] $u → package"
  else
    remote "$SUDO rm -f /etc/systemd/system/$u.service.d/$DROPIN"
    echo "[units] $u → old flat code"
  fi
done

remote "$SUDO systemctl daemon-reload"

# Restart only what was running: a service paused for a measurement, or a box before its cutover, stays as it was.
running=()
while read -r u s; do [[ "$s" == "active" ]] && running+=("$u"); done <<<"$states"
if (( ${#running[@]} )); then
  remote "$SUDO systemctl restart ${running[*]} && systemctl is-active ${running[*]}" \
    || fail "a service did not come back — run: bash scripts/remodel-units.sh --rollback"
  echo "[units] restarted and active: ${running[*]}"
else
  echo "[units] nothing was running: nothing restarted (the timers pick up the change on their next run)"
fi
trap - ERR
