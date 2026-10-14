#!/usr/bin/env bash
# box-setup.sh — HOST-01 lane A: build the pipeline box on a hardened Ubuntu 24.04 VPS (run box-harden.sh first).
# Bill · Oliver. Idempotent and phased: safe to re-run; each phase checks what it needs and gates on what it made.
#
#   scripts/box-setup.sh <ipv4>              # as deploy@ (key + passwordless sudo, from box-harden.sh)
#
# What it builds, matching the Hetzner box (scripts/box-inventory.sh is the recipe source):
#   1 packages      bc, jq, sqlite3, curl, the Python the venvs need, Node (built-ins only: no npm packages), Caddy
#                   from its own apt repo; 1b ffmpeg 8.0.1 matching production (pinned static build, sha256 checked)
#   2 user, dirs    the system user rockstar (home /home/rockstar: the model cache), /opt/rockstar-pipeline, its state/
#   3 app venv      /opt/rockstar-pipeline/venv from requirements.txt (pinned)
#   4 whisperx env  /opt/rockstar-pipeline/scripts/whisperx_env: CPU torch + scripts/box-setup/whisperx-requirements.txt,
#                   the Hetzner box's pip freeze (pinned to what runs today)
#   5 units         systemd units, drop-ins and tmpfiles.d INSTALLED, NOT ENABLED, NOT STARTED; the Caddyfile installed,
#                   Caddy NOT started: pipeline.example.test points at the old box until the cutover, and a premature
#                   certificate request would fail (and count against Let's Encrypt's limits)
# Not here, by design: credentials (Orly's move, at the cutover), state/ (the cutover moves it), the code (deploy.sh),
# Chromium (box-card-setup.sh --install), models (they download on the first measurement, as rockstar), and starting
# anything. Every run's output is the caller's to save (the HOST-01 runbook redirects it to a file).
set -euo pipefail

IP="${1:?usage: box-setup.sh <ipv4>}"
ROOT="$(CDPATH= cd "$(dirname "$0")/.." && pwd)"
APP=/opt/rockstar-pipeline
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=15 "deploy@$IP")
gate() { echo "GATE FAILED: $*" >&2; exit 1; }
remote() { "${SSH[@]}" "set -euo pipefail; export DEBIAN_FRONTEND=noninteractive; $1"; }
APT="sudo apt-get -o DPkg::Lock::Timeout=600 -y -qq"

WX_REQ="$ROOT/scripts/box-setup/whisperx-requirements.txt"
SKIP_WHISPERX="${SKIP_WHISPERX:-0}"   # 1: build everything else while the pins are still being read from the old box
[[ "$SKIP_WHISPERX" == 1 || -f "$WX_REQ" ]] || gate "$WX_REQ is missing: make it from the Hetzner inventory (scripts/box-inventory.sh, whisperx-venv.txt)"
NODE_MAJOR="$(cat "$ROOT/scripts/box-setup/node-major" 2>/dev/null || echo 20)"

echo "-- 0: the box"
v="$(remote '. /etc/os-release; echo "$VERSION_ID"; sudo -n whoami; getent passwd deploy | cut -d: -f7')"
# bash as deploy@'s login shell: the scripts quote box commands with printf %q, whose $'…' only bash parses (Toby).
[[ "$v" == $'24.04\nroot\n/bin/bash' ]] || gate "expected Ubuntu 24.04, passwordless sudo and bash as deploy@'s shell, got: $v"
echo "OK — Ubuntu 24.04, deploy@ sudo, bash"

echo "-- 1: packages"
remote "$APT update
$APT install ca-certificates curl gnupg debian-keyring debian-archive-keyring apt-transport-https \
  bc jq sqlite3 xz-utils python3.12 python3.12-venv python3-pip >/dev/null
if ! command -v node >/dev/null || [ \"\$(node -p 'process.versions.node.split(\".\")[0]')\" != $NODE_MAJOR ]; then
  curl -fsSL https://deb.nodesource.com/setup_$NODE_MAJOR.x | sudo -E bash - >/dev/null
  $APT install nodejs >/dev/null
fi
if ! command -v caddy >/dev/null; then
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | sudo gpg --batch --yes --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  $APT update
  $APT install caddy >/dev/null
  sudo systemctl disable --now caddy >/dev/null 2>&1   # not before the cutover: see the header
fi"
echo "-- 1b: ffmpeg 8.0.1, matching production (scripts/box-setup/ffmpeg.txt: pinned URL, sha256 checked on the box)"
FF_URL="$(sed -n 's/^URL=//p' "$ROOT/scripts/box-setup/ffmpeg.txt")"
FF_SHA="$(sed -n 's/^SHA256=//p' "$ROOT/scripts/box-setup/ffmpeg.txt")"
FF_VER="$(sed -n 's/^VERSION=//p' "$ROOT/scripts/box-setup/ffmpeg.txt")"
[[ "$FF_SHA" =~ ^[0-9a-f]{64}$ && "$FF_URL" == https://github.com/BtbN/FFmpeg-Builds/releases/download/* ]] || gate "scripts/box-setup/ffmpeg.txt is malformed"
# Our copy, not BtbN's (Orly): BtbN deletes old autobuilds, so the tarball lives in a Mac-side cache we keep
# (~/jhc/artifacts/ffmpeg/, downloaded once if absent). Its sha256 is checked on the Mac and again on the box.
FF_CACHE="${FFMPEG_CACHE:-$HOME/jhc/artifacts/ffmpeg}/$(basename "$FF_URL")"
if ! "${SSH[@]}" "test -x /opt/ffmpeg-$FF_VER/bin/ffmpeg"; then
  if [[ ! -f "$FF_CACHE" ]]; then
    mkdir -p "$(dirname "$FF_CACHE")"
    curl -fsSL --max-time 600 -o "$FF_CACHE.part" "$FF_URL" && mv "$FF_CACHE.part" "$FF_CACHE"
  fi
  [[ "$(shasum -a 256 "$FF_CACHE" | cut -d' ' -f1)" == "$FF_SHA" ]] || gate "$FF_CACHE: sha256 MISMATCH (not sent to the box)"
  scp -q -o BatchMode=yes "$FF_CACHE" "deploy@$IP:/tmp/ff.tar.xz"
  remote "echo '$FF_SHA  /tmp/ff.tar.xz' | sha256sum -c --quiet - || { echo 'ffmpeg on the box: sha256 MISMATCH, not installed'; rm -f /tmp/ff.tar.xz; exit 1; }
sudo mkdir -p /opt/ffmpeg-$FF_VER && sudo tar -C /opt/ffmpeg-$FF_VER --strip-components=1 -xJf /tmp/ff.tar.xz && rm -f /tmp/ff.tar.xz"
fi
# Ubuntu's ffmpeg package would overwrite the links below without a word (Toby): apt may never install it here.
remote "printf 'Package: ffmpeg\\nPin: release *\\nPin-Priority: -1\\n' | sudo tee /etc/apt/preferences.d/no-ubuntu-ffmpeg >/dev/null
dpkg -s ffmpeg >/dev/null 2>&1 && $APT remove ffmpeg >/dev/null || true   # Ubuntu's 6.1 binary (its libraries stay)
sudo ln -sfn /opt/ffmpeg-$FF_VER/bin/ffmpeg /usr/bin/ffmpeg; sudo ln -sfn /opt/ffmpeg-$FF_VER/bin/ffprobe /usr/bin/ffprobe
pol=\$(apt-cache policy ffmpeg)   # captured first: never grep -q on a pipe under pipefail (the 2026-10-09 RSB 521)
[ \"\$(printf '%s\\n' \"\$pol\" | grep -c 'Candidate: (none)')\" -ge 1 ] || { echo 'apt could still install Ubuntu ffmpeg'; exit 1; }"
remote "v=\$(ffmpeg -hide_banner -version); case \"\${v%%\$'\\n'*}\" in *' n8.0.1'*) ;; *) echo 'ffmpeg is not 8.0.1'; exit 1;; esac
f=\$(ffmpeg -hide_banner -filters 2>/dev/null); e=\$(ffmpeg -hide_banner -encoders 2>/dev/null)
for x in drawtext ass loudnorm colorspace amix afade atrim scale volume; do printf '%s\n' \"\$f\" | grep -c -w \$x >/dev/null || { echo \"missing filter \$x\"; exit 1; }; done
for x in libx264 aac; do printf '%s\n' \"\$e\" | grep -c -w \$x >/dev/null || { echo \"missing encoder \$x\"; exit 1; }; done
echo \"ffmpeg \$(ffmpeg -hide_banner -version | head -1 | cut -d' ' -f3): drawtext ass loudnorm colorspace amix afade atrim scale volume, libx264 aac\"" \
  || gate "ffmpeg 8.0.1 is not in place with the filters the reels use"

remote 'for b in ffmpeg bc jq sqlite3 python3.12 node caddy; do command -v $b >/dev/null || { echo "missing $b"; exit 1; }; done
echo "ffmpeg $(ffmpeg -version | head -1 | cut -d" " -f3) · node $(node -v) · $(python3.12 --version) · caddy $(caddy version | cut -d" " -f1)"' \
  || gate "a package is missing"

echo "-- 2: the rockstar user and the app's folders"
remote "id rockstar >/dev/null 2>&1 || sudo useradd --system --create-home --home-dir /home/rockstar --shell /usr/sbin/nologin rockstar
sudo install -d -m 755 -o rockstar -g rockstar $APP $APP/scripts $APP/jobs $APP/quote_cards $APP/BrandAssets
sudo install -d -m 750 -o rockstar -g rockstar $APP/state
sudo install -d -m 755 -o rockstar -g rockstar /var/tmp/rockstar-work"
remote "id rockstar; stat -c '%U %a %n' $APP $APP/state" | sed 's/^/   /'

echo "-- 3: the app venv (requirements.txt, pinned)"
scp -q -o BatchMode=yes "$ROOT/requirements.txt" "deploy@$IP:/tmp/requirements.txt"
remote "[ -x $APP/venv/bin/python ] || sudo -u rockstar python3.12 -m venv $APP/venv
sudo -u rockstar $APP/venv/bin/pip install --quiet --upgrade pip
sudo -u rockstar $APP/venv/bin/pip install --quiet -r /tmp/requirements.txt
$APP/venv/bin/python -c 'import anthropic, fastapi, uvicorn, googleapiclient, PIL, playwright; print(\"app venv imports: ok\")'"

echo "-- 4: the WhisperX env (CPU torch, the Hetzner box's pins)"
if [[ "$SKIP_WHISPERX" == 1 ]]; then echo "SKIPPED (SKIP_WHISPERX=1): the box is not finished until this phase runs"; else
scp -q -o BatchMode=yes "$WX_REQ" "deploy@$IP:/tmp/whisperx-requirements.txt"
remote "[ -x $APP/scripts/whisperx_env/bin/python ] || sudo -u rockstar python3.12 -m venv $APP/scripts/whisperx_env
sudo -u rockstar $APP/scripts/whisperx_env/bin/pip install --quiet --upgrade pip
sudo -u rockstar $APP/scripts/whisperx_env/bin/pip install --quiet --extra-index-url https://download.pytorch.org/whl/cpu \
  -r /tmp/whisperx-requirements.txt
sudo -u rockstar $APP/scripts/whisperx_env/bin/python -c 'import torch, whisperx; print(\"whisperx env imports: ok, torch\", torch.__version__, \"cuda\", torch.cuda.is_available())'
command -v $APP/scripts/whisperx_env/bin/whisperx >/dev/null"
fi

echo "-- 5: units, drop-ins, tmpfiles, Caddyfile — installed, nothing enabled or started"
tar -C "$ROOT" -czf - systemd Caddyfile | "${SSH[@]}" "rm -rf /tmp/rockstar-units && mkdir -p /tmp/rockstar-units && tar -C /tmp/rockstar-units -xzf -"
remote "cd /tmp/rockstar-units
for u in systemd/rockstar-*.service systemd/rockstar-*.timer; do sudo install -m 644 \"\$u\" /etc/systemd/system/; done
for d in systemd/rockstar-*.service.d; do sudo install -d -m 755 /etc/systemd/system/\$(basename \$d); sudo install -m 644 \$d/*.conf /etc/systemd/system/\$(basename \$d)/; done
sudo install -m 644 systemd/tmpfiles.d/rockstar-credentials.conf /etc/tmpfiles.d/
sudo systemd-tmpfiles --create /etc/tmpfiles.d/rockstar-credentials.conf   # never --remove (Orly's note in the file)
sudo install -m 644 Caddyfile /etc/caddy/Caddyfile
sudo systemctl daemon-reload
rm -rf /tmp/rockstar-units"
# is-enabled / is-active exit non-zero for a disabled or stopped unit — the state we want — so their exit is not the gate.
state="$(remote "{ systemctl is-enabled rockstar-worker rockstar-webhook rockstar-poll-drive.timer rockstar-renew-watches.timer caddy 2>&1 || true; } | tr '\n' ' '; { systemctl is-active rockstar-worker rockstar-webhook caddy 2>&1 || true; } | tr '\n' ' '")"
echo "   enabled/active: $state"
[[ "$state" != *" enabled"* && "$state" != enabled* && "$state" != *" active"* ]] || gate "something is enabled or running before the cutover: $state"

echo
echo "BUILT — $IP has the packages, rockstar, the app venv$([[ "$SKIP_WHISPERX" == 1 ]] && echo " (NOT the WhisperX env: SKIP_WHISPERX=1)" || echo " and the WhisperX env") and the units (none enabled). Next:"
echo "  (from main) scripts/deploy.sh --new-box --check, then scripts/deploy.sh --new-box; box-card-setup; lane C."
