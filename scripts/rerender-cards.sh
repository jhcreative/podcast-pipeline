#!/usr/bin/env bash
# CARD-05 back catalogue: replace each episode's Drive card set with the IBM Plex set
# (ADR-0015). Only the type changes -- the captions are the ones in Drive, byte for byte,
# and the cover titles are the ones already reviewed. Run on the Mac, by John.
#
#   scripts/rerender-cards.sh               # every episode with cards/ in Drive -> review
#   scripts/rerender-cards.sh 29 30         # just these
#   scripts/rerender-cards.sh --push 29 30  # replace EXACTLY what was reviewed
#
# Build: fetches quotes, show notes, speaker images and the existing captions; takes the
# cover titles from the staged/local cover_titles.json (drafted on the box only where an
# episode has none); renders here into Episodes/_rerender/<E-folder>/cards/.
#
# Push: CARD-02's clear-and-repopulate -- the old Avenir set goes to Drive's trash
# (recoverable for 30 days), then the staged Plex set goes up. It prints what it trashes.
# Carousels already posted on Instagram stay as posted.
set -euo pipefail

BOX_SERVER_OVERRIDE="${CARD04_REHEARSAL_SERVER:-}"   # rehearsal only (a name nobody exports by habit); else box.conf
source "$(dirname "$0")/box.sh"
APP="${CARD04_APP:-/opt/rockstar-pipeline}"
REMOTE="${CARD04_REMOTE:-/tmp/card05}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="${CARD04_STAGE:-$HERE/Episodes/_rerender}"
PUSH=0
ONLY=()
for arg in "$@"; do
  case "$arg" in
    --push) PUSH=1 ;;
    [0-9]*) ONLY+=("$arg") ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done

box() {
  ssh "$SERVER" "cd $APP && set -a && . ./.env && set +a && \
    PROMPTS_DIR=$REMOTE/prompts PYTHONPATH=$REMOTE:$REMOTE/quote_cards:$APP \
    $APP/venv/bin/python $*"
}

trap 'ssh "$SERVER" "rm -rf $REMOTE" || true' EXIT
echo "== ship CARD-05 modules to $SERVER:$REMOTE"
ssh "$SERVER" "rm -rf $REMOTE && mkdir -p $REMOTE/quote_cards $REMOTE/prompts $REMOTE/Episodes \
  && cp -p $APP/prompts/*.md $REMOTE/prompts/"
scp -q "$HERE/anthropic_client.py" "$SERVER:$REMOTE/"
scp -q "$HERE/quote_cards/"{cover_backfill,card_upload,cover_titles,make_cover_titles,quote_source}.py \
  "$SERVER:$REMOTE/quote_cards/"
scp -q "$HERE/prompts/cover_titles.md" "$SERVER:$REMOTE/prompts/"

if ((${#ONLY[@]})); then
  EPS=("${ONLY[@]}")
else
  echo "== Drive: episodes with cards/"
  LIST="$(box "$REMOTE/quote_cards/cover_backfill.py list")"
  echo "$LIST"
  EPS=()
  while IFS=$'\t' read -r ep _; do
    [[ "$ep" =~ ^E([0-9]+)$ ]] && EPS+=("${BASH_REMATCH[1]}")
  done <<< "$LIST"
fi
((${#EPS[@]})) || { echo "no episodes"; exit 0; }

mkdir -p "$STAGE"
FAILED=()
for n in "${EPS[@]}"; do
  echo; echo "== E$n"
  local_dir="$(ls -d "$STAGE"/E"$n"-* 2>/dev/null | head -1 || true)"

  if ((PUSH)); then
    if [[ -z "$local_dir" || ! -d "$local_dir/cards" ]]; then
      echo "  nothing staged -- run without --push first"; FAILED+=("E$n:not-built"); continue
    fi
    covers=$(find "$local_dir/cards" -name '*_0cover.png' | wc -l | tr -d ' ')
    slides=$(find "$local_dir/cards" -name '*_[0-9]*of[0-9]*.png' | wc -l | tr -d ' ')
    caps=$(find "$local_dir/cards" -name '*_caption.txt' | wc -l | tr -d ' ')
    if ((covers == 0 || slides == 0)); then
      echo "  incomplete set -- rebuild before pushing"; FAILED+=("E$n:incomplete"); continue
    fi
    echo "  replacing with: $slides slides, $covers covers, $caps captions (captions unchanged)"
    ssh "$SERVER" "rm -rf $REMOTE/push && mkdir -p $REMOTE/push"
    scp -q "$local_dir/cards/"* "$SERVER:$REMOTE/push/"
    box "$REMOTE/quote_cards/card_upload.py --episode $n --dir $REMOTE/push" || FAILED+=("E$n:push")
    continue
  fi

  if ! box "$REMOTE/quote_cards/cover_backfill.py fetch --captions --episode $n --dest $REMOTE/Episodes"; then
    FAILED+=("E$n:fetch"); continue
  fi
  folder="$(ssh "$SERVER" "ls $REMOTE/Episodes | grep -E '^E$n-' | head -1")"
  [[ "$folder" =~ ^E$n-[^/]+$ ]] || { FAILED+=("E$n:staged-name"); continue; }
  dest="${STAGE:?}/${folder:?}"
  keep_titles=""
  [[ -f "$dest/cover_titles.json" ]] && keep_titles="$(cat "$dest/cover_titles.json")"
  rm -rf "${dest:?}" && mkdir -p "$dest/cards"
  for f in $(ssh "$SERVER" "cd $REMOTE/Episodes/$folder && ls | grep -iE '^(quotes\.json|apple_summary\.md|drive_cards\.txt)$|(tile|reel).*\.(jpg|jpeg|png|webp)$' || true"); do
    scp -q "$SERVER:$REMOTE/Episodes/$folder/$f" "$dest/"
  done
  for f in $(ssh "$SERVER" "cd $REMOTE/Episodes/$folder/cards 2>/dev/null && ls | grep -iv '\.png$' | grep -v '^_upload_incomplete' || true"); do
    scp -q "$SERVER:$REMOTE/Episodes/$folder/cards/$f" "$dest/cards/"
  done

  # Cover titles: this stage's, else the ones reviewed earlier (backfill / new-cards /
  # Episodes/), else a model draft on the box. A hand edit always wins (PP-11).
  if [[ -n "$keep_titles" ]]; then
    printf '%s\n' "$keep_titles" > "$dest/cover_titles.json"
  else
    for c in "$HERE"/Episodes/_cover_backfill/E"$n"-*/cover_titles.json \
             "$HERE"/Episodes/_new_cards/E"$n"-*/cover_titles.json \
             "$HERE"/Episodes/E"$n"-*/cover_titles.json; do
      [[ -f "$c" ]] && { cp "$c" "$dest/"; echo "  cover titles from ${c#"$HERE"/}"; break; }
    done
  fi

  override="TITLE_E$n"
  if [[ -n "${!override:-}" ]]; then
    title="${!override}"
  else
    title="$(python3 "$HERE/quote_cards/cover_backfill.py" title --dir "$dest")" \
      || { echo "  set $override=\"<published title>\" and re-run"; FAILED+=("E$n:title"); continue; }
  fi
  if [[ ! -f "$dest/cover_titles.json" ]]; then
    if box "$REMOTE/quote_cards/make_cover_titles.py --episode $n --dir $REMOTE/Episodes/$folder --title $(printf %q "$title")"; then
      scp -q "$SERVER:$REMOTE/Episodes/$folder/cover_titles.json" "$dest/"
    else
      FAILED+=("E$n:cover-titles"); continue
    fi
  fi
  if ! python3 "$HERE/quote_cards/render_cards.py" --episode "$n" --title "$title" --dir "$dest"; then
    FAILED+=("E$n:render"); continue
  fi
done

if ((!PUSH)); then
  python3 - "$STAGE" <<'PY'
import glob, os, sys
from PIL import Image
for d in sorted(glob.glob(os.path.join(sys.argv[1], "E*"))):
    fs = sorted(glob.glob(os.path.join(d, "cards", "*.png")))
    if not fs:
        continue
    t = 270
    s = Image.new("RGB", (t * 5, t * ((len(fs) + 4) // 5)), "white")
    for i, f in enumerate(fs):
        s.paste(Image.open(f).convert("RGB").resize((t, t)), ((i % 5) * t, (i // 5) * t))
    s.save(os.path.join(d, "_sheet.jpg"))
    print(f"  {os.path.basename(d)}: {len(fs)} PNG(s) -> {os.path.join(d, '_sheet.jpg')}")
PY
fi

if ((${#FAILED[@]})); then
  echo "FAILED: ${FAILED[*]}"; exit 1
fi
((PUSH)) && echo "replaced: Plex sets in Drive; old sets in Drive's trash for 30 days" \
         || echo "review each _sheet.jpg, then: scripts/rerender-cards.sh --push ${EPS[*]}"
