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
set -euo pipefail

source "$(dirname "$0")/box.sh"   # SERVER from scripts/box.conf (SHARE-01)
APP="/opt/rockstar-pipeline"
PLAYWRIGHT="1.60.0"
INSTALL=0
[[ "${1:-}" == "--install" ]] && INSTALL=1

echo "== box check (read-only)"
ssh "$SERVER" "set -e
  echo \"disk free on /:   \$(df -h / | awk 'NR==2{print \$4}')\"
  echo \"memory:           \$(free -h | awk '/Mem:/{print \$2\" total, \"\$7\" available\"}')\"
  echo \"swap:             \$(free -h | awk '/Swap:/{print \$2}')\"
  if pgrep -f '[w]hisperx' >/dev/null; then echo 'whisperx:         RUNNING now'; else echo 'whisperx:         not running'; fi
  echo \"playwright:       \$($APP/venv/bin/python -c 'import importlib.metadata as m; print(m.version(\"playwright\"))' 2>/dev/null || echo 'not installed')\"
  echo \"pillow:           \$($APP/venv/bin/python -c 'import PIL; print(PIL.__version__)' 2>/dev/null || echo 'not installed')\"
  c=\$(ls -d /root/.cache/ms-playwright/chromium* 2>/dev/null | head -1); echo \"chromium cache:   \${c:-none}\""

((INSTALL)) || { echo; echo "check only. To install: scripts/box-card-setup.sh --install"; exit 0; }

echo; echo "== install (Playwright $PLAYWRIGHT + Chromium + Pillow into $APP/venv)"
ssh "$SERVER" "set -e
  free_kb=\$(df -k / | awk 'NR==2{print \$4}')
  [ \"\$free_kb\" -ge 2097152 ] || { echo 'REFUSED: under 2 GB free on /'; exit 1; }
  if pgrep -f '[w]hisperx' >/dev/null; then echo 'REFUSED: WhisperX is running; try again after it exits'; exit 1; fi
  $APP/venv/bin/pip install --quiet 'playwright==$PLAYWRIGHT' pillow
  # Playwright $PLAYWRIGHT doesn't know Ubuntu releases newer than 24.04 (the box is 26.04:
  # \"does not support chromium on ubuntu26.04-x64\"). Install the 24.04 build instead;
  # the render test below proves whether it runs here.
  . /etc/os-release
  if [ \"\$ID\" = ubuntu ] && [ \"\${VERSION_ID%%.*}\" -gt 24 ]; then
    export PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64
    echo \"Ubuntu \$VERSION_ID: installing Playwright's ubuntu24.04 Chromium build\"
  fi
  # --with-deps installs 24.04's apt package names; if 26.04 renamed one, fall back to the
  # browser alone and let the render test name any missing library.
  $APP/venv/bin/python -m playwright install --with-deps chromium \\
    || { echo 'system deps step failed -- installing the browser alone'; $APP/venv/bin/python -m playwright install chromium; }
  $APP/venv/bin/python - <<'PY'
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    page = p.chromium.launch().new_page()
    page.set_content('<p>ok</p>')
    print('chromium renders:', page.inner_text('p'))
PY"
echo "installed. Next: bash scripts/deploy.sh --check, then bash scripts/deploy.sh"
