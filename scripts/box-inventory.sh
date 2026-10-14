#!/usr/bin/env bash
# box-inventory.sh — what the pipeline box is made of, read from the box itself (HOST-01). John runs it.
#
#   scripts/box-inventory.sh                 # the box in scripts/box.conf (today: the Hetzner box)
#
# READ-ONLY: it reads and lists; it changes nothing on the box. The current box was built by hand in phases
# (May 2026) and never written down; this is the recipe source for scripts/box-setup.sh, so nothing is forgotten.
# SECRETS: never a value. Env files are listed as their variable NAMES only; credential files by name, size and mode.
# Saves everything to ~/RockstarPodcast-evaldata/host-01/inventory-<UTC>/ (the 2026-10-12 manual rule) and prints a summary.
set -euo pipefail

ROOT="$(CDPATH= cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/box.sh"
APP="${BOX_APP_OVERRIDE:-/opt/rockstar-pipeline}"
OUT="${INVENTORY_ROOT:-$HOME/RockstarPodcast-evaldata/host-01}/inventory-$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$OUT"
SSH=(ssh -o ConnectTimeout=15 -o BatchMode=yes "$SERVER")

grab() {  # grab NAME 'remote command'
  "${SSH[@]}" "$2" > "$OUT/$1.txt" 2>&1 || echo "(exit $?)" >> "$OUT/$1.txt"
  printf '  %-22s %s lines\n' "$1" "$(wc -l < "$OUT/$1.txt" | tr -d ' ')"
}

echo "== inventory of $SERVER -> $OUT"
grab os            '. /etc/os-release; echo "$PRETTY_NAME"; uname -r; nproc; free -m; df -h / /opt /var/tmp'
grab packages      'dpkg-query -W -f="\${Package} \${Version}\n" | sort'
grab apt-sources   'ls /etc/apt/sources.list.d/; grep -rh "^deb\|^URIs\|^Suites" /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null'
grab runtimes      'for b in python3 python3.11 python3.12 node npm ffmpeg ffprobe caddy sqlite3 jq git; do printf "%s: " $b; (command -v $b && $b --version 2>&1 | head -1) | tr "\n" " "; echo; done'
grab users         'getent passwd rockstar deploy; id rockstar; ls -la /home/rockstar 2>/dev/null; sudo -n true 2>/dev/null && echo "sudo: ok" || echo "sudo: needs password"'
grab app-tree      "cd $APP && find . -maxdepth 2 -not -path './venv/*' -not -path './scripts/whisperx_env/*' -not -path './state/*' -not -path './ms-playwright/*' | sort | head -400; du -sh venv scripts/whisperx_env ms-playwright state 2>/dev/null"
grab app-venv      "$APP/venv/bin/python --version; $APP/venv/bin/pip freeze 2>/dev/null"
grab whisperx-venv "$APP/scripts/whisperx_env/bin/python --version; $APP/scripts/whisperx_env/bin/pip freeze 2>/dev/null"
grab models        'for d in /home/rockstar/.cache/huggingface /home/rockstar/.cache/torch /root/.cache/huggingface; do echo "== $d"; sudo -n du -sh $d 2>/dev/null; sudo -n ls $d/hub 2>/dev/null; done'
grab node-modules  "cd $APP && ls node_modules 2>/dev/null | head -100; cat package.json 2>/dev/null"
grab systemd       'systemctl list-unit-files "rockstar-*" --no-pager; for u in /etc/systemd/system/rockstar-*; do echo "== $u"; [ -d "$u" ] && ls "$u" || true; done; systemctl list-timers "rockstar-*" --no-pager'
grab units-diff    "cd /etc/systemd/system && for f in rockstar-*.service rockstar-*.timer rockstar-*.service.d/*.conf; do echo \"== \$f\"; cat \"\$f\"; done 2>/dev/null"
grab tmpfiles      'cat /etc/tmpfiles.d/rockstar* 2>/dev/null'
grab caddy         'cat /etc/caddy/Caddyfile; systemctl is-active caddy; caddy version 2>/dev/null'
grab firewall      'sudo -n ufw status verbose 2>/dev/null; sudo -n systemctl is-active fail2ban 2>/dev/null'
grab cron          'crontab -l 2>/dev/null; sudo -n crontab -l 2>/dev/null; ls /etc/cron.d'
# Secrets: names only. sed keeps what's left of "=", never what's right of it.
grab secret-shapes "for f in $APP/.env $APP/scripts/.env; do echo \"== \$f (\$(stat -c '%U %a %s bytes' \$f 2>/dev/null))\"; sed -n 's/^\\([A-Za-z_][A-Za-z0-9_]*\\)=.*/\\1=<set>/p' \$f 2>/dev/null; done; echo '== /etc/rockstar-pipeline'; sudo -n ls -la /etc/rockstar-pipeline 2>/dev/null; echo '== key files under the app'; find $APP -maxdepth 2 -name '*.json' -path '*cred*' -o -maxdepth 2 -name '*service*account*' 2>/dev/null | xargs -r stat -c '%n %U %a %s' 2>/dev/null"
grab state-files   "cd $APP/state && ls -la; sqlite3 --version 2>/dev/null"

( cd "$OUT" && shasum -a 256 *.txt > SHA256SUMS )
echo "saved: $OUT ($(ls "$OUT" | wc -l | tr -d ' ') files). Nothing on the box changed; no secret value was read."
if grep -q -E '=[^<]' "$OUT/secret-shapes.txt" 2>/dev/null; then
  echo "WARNING: secret-shapes.txt holds something after an '=' that isn't <set>: check it before sharing" >&2
fi
