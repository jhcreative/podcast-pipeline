#!/usr/bin/env bash
# CARD-04: a full quote-card set (slides + covers + captions) for episodes that have none
# in Drive yet. Run on the Mac, by John (agents do not touch the box).
#
#   scripts/new-cards.sh 25 28 33          # build: fetch, draft titles + captions, render -> review
#   scripts/new-cards.sh --push 25 28 33   # push EXACTLY what was reviewed; nothing is redrafted
#   TITLE_E28="Hook | Topic" scripts/new-cards.sh 28   # when apple_summary.md has no title line
#
# Build: quotes, show notes and the speaker images come from Drive; the cover titles
# (make_cover_titles.py) and captions (make_captions.py) are drafted on the box, where
# the model key is, with the box's own prompts (Heather tunes them, D-5); slides and
# covers render here (the Mac path until CARD-05 moves it into Phase 2). Everything lands in
# Episodes/_new_cards/<E-folder>/ for review. A re-run keeps a cover_titles.json or
# captions already staged, so hand edits survive; delete them to redraft.
#
# Push: card_upload --new-only -- creates cards/ and uploads the staged set; refuses an
# episode whose cards/ already has files (that one is backfill-covers.sh's).
set -euo pipefail

BOX_SERVER_OVERRIDE="${CARD04_REHEARSAL_SERVER:-}"   # rehearsal only (a name nobody exports by habit); else box.conf
source "$(dirname "$0")/box.sh"
APP="${CARD04_APP:-/opt/rockstar-pipeline}"
REMOTE="${CARD04_REMOTE:-/tmp/card04}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
STAGE="${CARD04_STAGE:-$HERE/Episodes/_new_cards}"
PUSH=0
EPS=()
for arg in "$@"; do
  case "$arg" in
    --push) PUSH=1 ;;
    [0-9]*) EPS+=("$arg") ;;
    *) echo "unknown argument: $arg" >&2; exit 2 ;;
  esac
done
((${#EPS[@]})) || { echo "name the episodes: scripts/new-cards.sh 25 28 33" >&2; exit 2; }

box() {   # python on the box, the shipped modules first on the path
  ssh "$SERVER" "cd $APP && set -a && . ./.env && set +a && \
    PROMPTS_DIR=$REMOTE/prompts PYTHONPATH=$REMOTE:$REMOTE/quote_cards:$APP \
    $APP/venv/bin/python $*"
}

trap 'ssh "$SERVER" "rm -rf $REMOTE" || true' EXIT
echo "== ship CARD-04 modules to $SERVER:$REMOTE"
ssh "$SERVER" "rm -rf $REMOTE && mkdir -p $REMOTE/quote_cards $REMOTE/prompts $REMOTE/Episodes \
  && cp -p $APP/prompts/*.md $REMOTE/prompts/"
scp -q "$HERE/anthropic_client.py" "$SERVER:$REMOTE/"
scp -q "$HERE/quote_cards/"{cover_backfill,card_upload,cover_titles,make_cover_titles,make_captions,quote_source}.py \
  "$SERVER:$REMOTE/quote_cards/"
scp -q "$HERE/prompts/cover_titles.md" "$SERVER:$REMOTE/prompts/"

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
    if ((covers == 0 || slides == 0 || caps == 0)); then
      echo "  incomplete set -- rebuild before pushing"; FAILED+=("E$n:incomplete"); continue
    fi
    echo "  staged: $slides slides, $covers covers, $caps captions"
    ssh "$SERVER" "rm -rf $REMOTE/push && mkdir -p $REMOTE/push"
    scp -q "$local_dir/cards/"* "$SERVER:$REMOTE/push/"
    box "$REMOTE/quote_cards/card_upload.py --episode $n --dir $REMOTE/push --new-only" \
      || FAILED+=("E$n:push")
    continue
  fi

  if ! box "$REMOTE/quote_cards/cover_backfill.py fetch --new --episode $n --dest $REMOTE/Episodes"; then
    FAILED+=("E$n:fetch"); continue
  fi
  folder="$(ssh "$SERVER" "ls $REMOTE/Episodes | grep -E '^E$n-' | head -1")"
  [[ "$folder" =~ ^E$n-[^/]+$ ]] || { FAILED+=("E$n:staged-name"); continue; }
  dest="${STAGE:?}/${folder:?}"
  mkdir -p "$dest/cards"
  for f in quotes.json apple_summary.md drive_cards.txt; do
    scp -q "$SERVER:$REMOTE/Episodes/$folder/$f" "$dest/" 2>/dev/null || true
  done
  for f in $(ssh "$SERVER" "cd $REMOTE/Episodes/$folder && ls | grep -iE '(tile|reel).*\.(jpg|jpeg|png|webp)$' || true"); do
    scp -q "$SERVER:$REMOTE/Episodes/$folder/$f" "$dest/"
  done

  override="TITLE_E$n"
  if [[ -n "${!override:-}" ]]; then
    title="${!override}"
  else
    title="$(python3 "$HERE/quote_cards/cover_backfill.py" title --dir "$dest")" \
      || { echo "  set $override=\"<published title>\" and re-run"; FAILED+=("E$n:title"); continue; }
  fi
  tq="$(printf %q "$title")"

  # Cover titles: keep a staged (possibly hand-edited) sidecar; else draft on the box.
  if [[ -f "$dest/cover_titles.json" ]]; then
    scp -q "$dest/cover_titles.json" "$SERVER:$REMOTE/Episodes/$folder/"
  elif box "$REMOTE/quote_cards/make_cover_titles.py --episode $n --title $tq"; then
    scp -q "$SERVER:$REMOTE/Episodes/$folder/cover_titles.json" "$dest/"
  else
    FAILED+=("E$n:cover-titles"); continue
  fi

  # Captions: keep staged ones; else draft on the box.
  if ls "$dest/cards/"*_caption.txt >/dev/null 2>&1; then
    echo "  captions already staged -- kept"
  elif box "$REMOTE/quote_cards/make_captions.py --episode $n --title $tq"; then
    for f in $(ssh "$SERVER" "cd $REMOTE/Episodes/$folder/cards && ls | grep '_caption\.txt$' || true"); do
      scp -q "$SERVER:$REMOTE/Episodes/$folder/cards/$f" "$dest/cards/"
    done
  else
    FAILED+=("E$n:captions"); continue
  fi

  find "$dest/cards" -name '*.png' -delete
  if ! python3 "$HERE/quote_cards/render_cards.py" --episode "$n" --title "$title" --dir "$dest"; then
    FAILED+=("E$n:render"); continue
  fi
done

if ((!PUSH)); then
  python3 - "$STAGE" <<'PY'
import glob, os, sys
from PIL import Image
stage = sys.argv[1]
for d in sorted(glob.glob(os.path.join(stage, "E*"))):
    fs = sorted(glob.glob(os.path.join(d, "cards", "*.png")))
    if not fs:
        continue
    t = 270
    sheet = Image.new("RGB", (t * 5, t * ((len(fs) + 4) // 5)), "white")
    for i, f in enumerate(fs):
        sheet.paste(Image.open(f).convert("RGB").resize((t, t)), ((i % 5) * t, (i // 5) * t))
    sheet.save(os.path.join(d, "_sheet.jpg"))
    print(f"  {os.path.basename(d)}: {len(fs)} PNG(s) -> {os.path.join(d, '_sheet.jpg')}")
PY
fi

if ((${#FAILED[@]})); then
  echo "FAILED: ${FAILED[*]}"; exit 1
fi
((PUSH)) && echo "pushed: full sets in Drive" \
         || echo "review each _sheet.jpg and the captions in cards/, then: scripts/new-cards.sh --push ${EPS[*]}"
