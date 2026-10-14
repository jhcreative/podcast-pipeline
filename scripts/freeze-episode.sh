#!/usr/bin/env bash
# freeze-episode.sh — freeze an episode's record before a reset and re-run (the E13 freeze, any episode). John runs it.
#
#   scripts/freeze-episode.sh E6                     # journal from 14 days back
#   SINCE='2026-10-01 00:00' scripts/freeze-episode.sh E6
#
# READ-ONLY on the box's pipeline and on Drive. Its only writes are a scratch dir in the box's /tmp (removed at the end)
# and the freeze folder on this Mac: ~/RockstarPodcast-evaldata/freeze/<EP>/ (private: it holds guests' words).
#   worker-journal-full.txt   the worker's journal since SINCE, unfiltered (UTC)
#   state files               publication_schedule.json, blog_posts.json, episode_counter.json, run_log.jsonl, as now
#   jobs-rows.json            every job for the episode (by its code, or its number in the payload), read-only open
#   ledger-<EP>.txt           the ledger summary
#   drive/inventory.json      every file in the episode's Drive folder, its revisions and current content (no audio/video)
# Then SHA256SUMS, and the folder is made read-only. A freeze is made once: it refuses an existing folder.
set -euo pipefail

EP="${1:?usage: freeze-episode.sh E6}"
[[ "$EP" =~ ^E[0-9]+$ ]] || { echo "usage: freeze-episode.sh E6 (an episode code)"; exit 2; }
ROOT="$(CDPATH= cd "$(dirname "$0")/.." && pwd)"
source "$ROOT/scripts/box.sh"          # SERVER from scripts/box.conf (SHARE-01)
APP="${BOX_APP_OVERRIDE:-/opt/rockstar-pipeline}"   # the rehearsal test points this at a fake box
BASE="${FREEZE_ROOT:-$HOME/RockstarPodcast-evaldata/freeze}/$EP"
SINCE="${SINCE:-$(date -u -v-14d '+%Y-%m-%d %H:%M' 2>/dev/null || date -u -d '14 days ago' '+%Y-%m-%d %H:%M')}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
REMOTE="/tmp/freeze-$EP-$STAMP"
N="${EP#E}"

if [[ -e "$BASE" ]]; then
  echo "FREEZE STOP: $BASE already exists. A freeze is made once; move it aside yourself if this is a redo." >&2
  exit 2
fi
mkdir -p "$BASE"
exec > >(tee "$BASE/freeze-run.txt") 2>&1
echo "== $EP freeze $STAMP (UTC) → $BASE   (journal since $SINCE UTC)"

trap 'ssh "$SERVER" "rm -rf $REMOTE" || true' EXIT
ssh "$SERVER" "mkdir -m 700 $REMOTE"
scp -q "$ROOT/rockstar/tools/drive_inventory.py" "$SERVER:$REMOTE/"

echo "== 1/4 worker journal since $SINCE (UTC), unfiltered"
ssh "$SERVER" "journalctl -u rockstar-worker --since '$SINCE' --no-pager -o short-iso > $REMOTE/worker-journal-full.txt"

echo "== 2/4 box state, whole files as they are now"
ssh "$SERVER" "cp -p $APP/state/publication_schedule.json $APP/state/blog_posts.json \
  $APP/state/episode_counter.json $APP/state/run_log.jsonl $REMOTE/"

echo "== 3/4 the episode's job rows (read-only open) and its ledger summary"
ssh "$SERVER" "cd $APP && venv/bin/python -c \"
import json, sqlite3
c = sqlite3.connect('file:$APP/state/jobs.db?mode=ro', uri=True); c.row_factory = sqlite3.Row
rows = [dict(r) for r in c.execute('select * from jobs order by id')]
def number(r):
    try:
        return str(json.loads(r.get('payload') or '{}').get('episode_number'))
    except ValueError:
        return ''
mine = [r for r in rows if r.get('episode_id') == '$EP' or number(r) == '$N']
print(json.dumps(mine, indent=2))
\" > $REMOTE/jobs-rows.json
  venv/bin/python -m rockstar.tools.ledger_summary $EP > $REMOTE/ledger-$EP.txt 2>&1 || true"

echo "== 4/4 Drive: the $EP folder, every file, its revisions, current content (read-only)"
ssh "$SERVER" "cd $APP && set -a && . ./.env && set +a && \
  PYTHONPATH=$APP venv/bin/python $REMOTE/drive_inventory.py $EP $REMOTE/drive"

echo "== copy back"
ssh "$SERVER" "tar -C $REMOTE -czf - --exclude drive_inventory.py ." | tar -C "$BASE" -xzf -

echo "== seal: SHA256SUMS, then read-only"
( cd "$BASE" && find . -type f ! -name SHA256SUMS ! -name freeze-run.txt -print0 | sort -z \
    | xargs -0 shasum -a 256 > SHA256SUMS )
echo "sealed: $(wc -l < "$BASE/SHA256SUMS" | tr -d ' ') files; jobs: $(grep -c '"id":' "$BASE/jobs-rows.json" || true)"
echo "should print above: a 'drive inventory' line with a file count, and one line per Drive file."
echo "next: scripts/reset-episode.sh $EP --check"
trap - EXIT
ssh "$SERVER" "rm -rf $REMOTE"
chmod -R a-w "$BASE"
