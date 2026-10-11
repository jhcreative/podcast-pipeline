#!/usr/bin/env bash
# CARD-04 backfill: a cover slide for every quote carousel already in Drive.
# Run on the Mac, by John (the box is a production read/write an agent does not make).
#
#   scripts/backfill-covers.sh                # list, fetch, draft titles, render -> review
#   scripts/backfill-covers.sh --push         # after review: add the covers to Drive
#   scripts/backfill-covers.sh --push 30 31   # just these episodes
#   TITLE_E23="Red Light and Blue: Our Midlife Clean Energy Future" scripts/backfill-covers.sh 23
#                                             # an episode whose apple_summary has no title line
#
# Without --push nothing is written to Drive. With it, card_upload --covers-only ADDS
# *_0cover.png to each existing cards/ folder: it never trashes, replaces or creates,
# and skips a cover already there, so a re-run is safe.
#
# Rendering is local (vendored IBM Plex, ADR-0015; it can run on the box too); Drive and the model key
# live on the box. New code rides to the box in /tmp/card04 via PYTHONPATH, the same
# shadowing the E31 caption run used, so /opt/rockstar-pipeline is not changed.
#
# Review: Episodes/_cover_backfill/_sheet.jpg, then each E<N>-*/covers/. A title is
# changed by editing that episode's cover_titles.json and re-running without --push.
set -euo pipefail

BOX_SERVER_OVERRIDE="${CARD04_REHEARSAL_SERVER:-}"   # rehearsal only (a name nobody exports by habit); else box.conf
source "$(dirname "$0")/box.sh"
APP="${CARD04_APP:-/opt/rockstar-pipeline}"
REMOTE="${CARD04_REMOTE:-/tmp/card04}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="${CARD04_STAGE:-$HERE/Episodes/_cover_backfill}"
PUSH=0
ONLY=()
for arg in "$@"; do
  case "$arg" in
    --push) PUSH=1 ;;
    [0-9]*) ONLY+=("$arg") ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

box() {   # run python on the box with the shipped modules first on the path
  ssh "$SERVER" "cd $APP && set -a && . ./.env && set +a && \
    PROMPTS_DIR=$REMOTE/prompts PYTHONPATH=$REMOTE:$REMOTE/quote_cards:$APP \
    $APP/venv/bin/python $*"
}

echo "== ship CARD-04 modules to $SERVER:$REMOTE"
# The box scratch goes on every exit, including a failed scp under set -e.
trap 'ssh "$SERVER" "rm -rf $REMOTE" || true' EXIT
ssh "$SERVER" "rm -rf $REMOTE && mkdir -p $REMOTE/quote_cards $REMOTE/prompts $REMOTE/stage"
scp -q "$HERE/anthropic_client.py" "$SERVER:$REMOTE/"
scp -q "$HERE/quote_cards/"{cover_backfill,card_upload,cover_titles,make_cover_titles,quote_source}.py \
  "$SERVER:$REMOTE/quote_cards/"
scp -q "$HERE/prompts/cover_titles.md" "$SERVER:$REMOTE/prompts/"

echo "== Drive: episodes with cards/"
LIST="$(box "$REMOTE/quote_cards/cover_backfill.py list")"
echo "$LIST"
EPS=()
while IFS=$'\t' read -r ep _; do
  [[ "$ep" =~ ^E([0-9]+)$ ]] || continue
  n="${BASH_REMATCH[1]}"
  if ((${#ONLY[@]})); then
    keep=0; for o in "${ONLY[@]}"; do [[ "$o" == "$n" ]] && keep=1; done
    ((keep)) || continue
  fi
  EPS+=("$n")
done <<< "$LIST"
((${#EPS[@]})) || { echo "no episodes to cover"; exit 0; }

mkdir -p "$STAGE"
FAILED=()
for n in "${EPS[@]}"; do
  echo; echo "== E$n"
  if ! box "$REMOTE/quote_cards/cover_backfill.py fetch --episode $n --dest $REMOTE/stage"; then
    FAILED+=("E$n:fetch"); continue
  fi
  folder="$(ssh "$SERVER" "ls $REMOTE/stage | grep -E '^E$n-' | head -1")"
  # Every rm below is under "$STAGE/$folder"; an empty name would point it at $STAGE.
  [[ "$folder" =~ ^E$n-[^/]+$ ]] || { FAILED+=("E$n:staged-name"); continue; }
  rm -rf "$STAGE/$folder.new"
  scp -q -r "$SERVER:$REMOTE/stage/$folder" "$STAGE/$folder.new"
  # Keep what a person already decided: an existing sidecar here (or a local draft in
  # Episodes/) wins over a fresh model draft (PP-11).
  local_titles="$(ls "$HERE"/Episodes/E"$n"-*/cover_titles.json 2>/dev/null | head -1 || true)"
  if [[ -f "$STAGE/$folder/cover_titles.json" ]]; then
    cp "$STAGE/$folder/cover_titles.json" "$STAGE/$folder.new/"
  elif [[ -n "$local_titles" ]]; then
    cp "$local_titles" "$STAGE/$folder.new/"
  fi
  rm -rf "${STAGE:?}/${folder:?}" && mv "$STAGE/$folder.new" "$STAGE/$folder"
  # The footer's episode title: TITLE_E<N> if set, else apple_summary.md (ADR-0009).
  override="TITLE_E$n"
  if [[ -n "${!override:-}" ]]; then
    title="${!override}"
  else
    title="$(python3 "$HERE/quote_cards/cover_backfill.py" title --dir "$STAGE/$folder")" \
      || { echo "  set $override=\"<published title>\" and re-run"; FAILED+=("E$n:title"); continue; }
  fi
  if [[ ! -f "$STAGE/$folder/cover_titles.json" ]]; then
    if ! box "$REMOTE/quote_cards/make_cover_titles.py --episode $n --dir $REMOTE/stage/$folder --title $(printf %q "$title")"; then
      FAILED+=("E$n:titles"); continue
    fi
    scp -q "$SERVER:$REMOTE/stage/$folder/cover_titles.json" "$STAGE/$folder/"
  fi
  rm -rf "$STAGE/$folder/covers"
  if ! python3 "$HERE/quote_cards/render_covers.py" --episode "$n" --title "$title" \
       --dir "$STAGE/$folder" --out "$STAGE/$folder/covers"; then
    FAILED+=("E$n:render"); continue
  fi
  if ((PUSH)); then
    ssh "$SERVER" "rm -rf $REMOTE/push && mkdir -p $REMOTE/push"
    scp -q "$STAGE/$folder/covers/"*_0cover.png "$SERVER:$REMOTE/push/"
    box "$REMOTE/quote_cards/card_upload.py --episode $n --dir $REMOTE/push --covers-only" \
      || FAILED+=("E$n:push")
  fi
done

python3 - "$STAGE" <<'PY'
import glob, os, sys
from PIL import Image
stage = sys.argv[1]
fs = sorted(glob.glob(os.path.join(stage, "E*", "covers", "*_0cover.png")))
if fs:
    t = 270
    sheet = Image.new("RGB", (t * 5, t * ((len(fs) + 4) // 5)), "white")
    for i, f in enumerate(fs):
        sheet.paste(Image.open(f).convert("RGB").resize((t, t)), ((i % 5) * t, (i // 5) * t))
    sheet.save(os.path.join(stage, "_sheet.jpg"))
    print(f"\n{len(fs)} cover(s); contact sheet -> {os.path.join(stage, '_sheet.jpg')}")
PY

if ((${#FAILED[@]})); then
  echo "FAILED: ${FAILED[*]}"; exit 1
fi
((PUSH)) && echo "pushed: covers added to Drive; nothing else touched" \
         || echo "review the sheet, then: scripts/backfill-covers.sh --push"
