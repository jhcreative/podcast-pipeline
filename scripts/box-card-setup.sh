#!/usr/bin/env bash
# CARD-05 one-time box setup (Oliver's check, then the install). Run on the Mac, by John.
#
#   scripts/box-card-setup.sh           # check only: disk, memory, WhisperX, what's installed
#   scripts/box-card-setup.sh --install # then install Playwright + Chromium into the pipeline venv
#
# Ubuntu newer than 24.04 (the box is 26.04): Playwright's 24.04 Chromium build is used,
# and the final render test is the proof it runs.
#
# What the card step needs on the box: Playwright (pinned to the Mac's 1.60.0, so
# both render alike), its Chromium plus system libraries, and Pillow. About 450 MB on disk.
# Chromium headless takes ~250 MB RAM per render; the card step runs after Step 12,
# when WhisperX has long exited (it OOMs when doubled up -- reference_rockstar_pipeline_ops).
#
# --install refuses if free disk is under 2 GB or WhisperX is running right now.
#
# Chromium goes into $APP/ms-playwright, not root's ~/.cache: the worker runs as $WORKER_USER, and
# Playwright looks in the caller's own cache, so a browser installed as root was never found
# (E6, 2026-10-11: "Please run playwright install"). rockstar/jobs/card_step.py points every stage at this
# folder, and the render test below runs as $WORKER_USER, the way the worker does.
set -euo pipefail

# --new-box: the OVH box before its cutover (scripts/box-new.conf, HOST-01). Anywhere in the arguments.
BOX_WHICH=""
for a in "$@"; do [[ "$a" == "--new-box" ]] && BOX_WHICH=__box_new__; done
_args=(); for a in "$@"; do [[ "$a" == "--new-box" ]] || _args+=("$a"); done; set -- ${_args[@]+"${_args[@]}"}
source "$(dirname "$0")/box.sh" "$BOX_WHICH"   # SERVER from scripts/box.conf, or box-new.conf (SHARE-01, HOST-01)
# As root (the Hetzner box) a box command runs as written. As deploy@ (the OVH box, no root login) it runs through
# sudo -n bash -c, the command quoted whole, so the script below is the same for both.
box() { if [[ "${SERVER%%@*}" == root ]]; then ssh "$SERVER" "$1"; else ssh "$SERVER" "sudo -n bash -c $(printf '%q' "$1")"; fi; }
APP="/opt/rockstar-pipeline"
PLAYWRIGHT="1.60.0"
BROWSERS="$APP/ms-playwright"     # rockstar/jobs/card_step.py BROWSERS
WORKER_USER="rockstar"            # systemd/rockstar-worker.service User=
INSTALL=0
[[ "${1:-}" == "--install" ]] && INSTALL=1

echo "== box check (read-only)"
box "set -e
  echo \"disk free on /:   \$(df -h / | awk 'NR==2{print \$4}')\"
  echo \"memory:           \$(free -h | awk '/Mem:/{print \$2\" total, \"\$7\" available\"}')\"
  echo \"swap:             \$(free -h | awk '/Swap:/{print \$2}')\"
  if pgrep -f 'bin/[w]hisperx' >/dev/null; then echo 'whisperx:         RUNNING now'; else echo 'whisperx:         not running'; fi
  echo \"playwright:       \$($APP/venv/bin/python -c 'import importlib.metadata as m; print(m.version(\"playwright\"))' 2>/dev/null || echo 'not installed')\"
  echo \"pillow:           \$($APP/venv/bin/python -c 'import PIL; print(PIL.__version__)' 2>/dev/null || echo 'not installed')\"
  c=\$(ls -d $BROWSERS/chromium* 2>/dev/null | head -1); echo \"chromium (shared): \${c:-none}\"
  r=\$(ls -d /root/.cache/ms-playwright/chromium* 2>/dev/null | head -1); echo \"root's own cache:  \${r:-none} (the worker can't use this one)\""

((INSTALL)) || { echo; echo "check only. To install: scripts/box-card-setup.sh --install"; exit 0; }

echo; echo "== install (Playwright $PLAYWRIGHT + Chromium + Pillow into $APP/venv)"
box "set -e
  free_kb=\$(df -k / | awk 'NR==2{print \$4}')
  [ \"\$free_kb\" -ge 2097152 ] || { echo 'REFUSED: under 2 GB free on /'; exit 1; }
  if pgrep -f 'bin/[w]hisperx' >/dev/null; then echo 'REFUSED: WhisperX is running; try again after it exits'; exit 1; fi
  $APP/venv/bin/pip install --quiet 'playwright==$PLAYWRIGHT' pillow
  # Playwright $PLAYWRIGHT doesn't know Ubuntu releases newer than 24.04 (the box is 26.04:
  # \"does not support chromium on ubuntu26.04-x64\"). Install the 24.04 build instead;
  # the render test below proves whether it runs here.
  export PLAYWRIGHT_BROWSERS_PATH=$BROWSERS
  . /etc/os-release
  if [ \"\$ID\" = ubuntu ] && [ \"\${VERSION_ID%%.*}\" -gt 24 ]; then
    export PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64
    echo \"Ubuntu \$VERSION_ID: installing Playwright's ubuntu24.04 Chromium build\"
  fi
  # --with-deps installs 24.04's apt package names; if 26.04 renamed one, fall back to the
  # browser alone and let the render test name any missing library.
  $APP/venv/bin/python -m playwright install --with-deps chromium \\
    || { echo 'system deps step failed -- installing the browser alone'; $APP/venv/bin/python -m playwright install chromium; }
  chmod -R a+rX $BROWSERS
  echo \"render test as $WORKER_USER (the worker's user):\"
  cd /tmp   # runuser keeps the cwd; root's home is unreadable to $WORKER_USER
  runuser -u $WORKER_USER -- env PLAYWRIGHT_BROWSERS_PATH=$BROWSERS \${PLAYWRIGHT_HOST_PLATFORM_OVERRIDE:+PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=\$PLAYWRIGHT_HOST_PLATFORM_OVERRIDE} \\
    $APP/venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    page = p.chromium.launch().new_page()
    page.set_content('<p>ok</p>')
    print('chromium renders:', page.inner_text('p'))
PY"
echo "installed. Next: bash scripts/deploy.sh --check, then bash scripts/deploy.sh"
