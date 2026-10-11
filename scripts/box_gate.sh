#!/usr/bin/env bash
# box_gate.sh — never overwrite a box copy that someone may have edited on the box (PROMPT-01 scope 3, PP-11).
#
# Usage: bash scripts/box_gate.sh SERVER DEST FILE...      (run from the repo root; deploy.sh calls it)
#
# For each FILE the box copy at DEST/FILE is one of:
#   ABSENT                       nothing to overwrite                        → ok
#   identical to local           nothing changes                             → ok
#   identical to a COMMITTED     a version this repo shipped; the local file  → ok ("ours")
#   version of FILE              is simply newer
#   anything else                placed or edited outside this repo          → STOP, named
#
# One ssh call answers for every file. The gate passes only on an explicit answer for each one:
# ssh failing, a sha256sum that errors, or a missing line is a FAIL, never "absent" (PQ-5a). The
# first version trusted an empty checksum as "absent", so a refused connection passed the gate
# (cold review of PR #29, 2026-10-10).

set -euo pipefail

fail() { echo "[box-gate] FAIL: $*" >&2; exit 1; }

[[ $# -ge 3 ]] || fail "usage: box_gate.sh SERVER DEST FILE..."
server=$1 dest=$2
shift 2
files=("$@")

for f in "${files[@]}"; do
  [[ -f "$f" ]] || fail "local file missing: $f"
done

# The remote side prints exactly one line per file: SUM <hash> <file>, ABSENT <file> or ERROR <file>.
remote='for f in'
for f in "${files[@]}"; do remote+=" $(printf '%q' "$f")"; done
remote+="; do p=$(printf '%q' "$dest")/\$f;"
remote+=' if [ ! -e "$p" ]; then echo "ABSENT $f";'
remote+=' elif s=$(sha256sum "$p" 2>/dev/null); then echo "SUM ${s%% *} $f";'
remote+=' else echo "ERROR $f"; fi; done'

# Times out like every deploy.sh call: a box that hangs after the probe fails here instead of hanging (Toby, PR #39).
if ! out=$(ssh -o ConnectTimeout=15 -o ServerAliveInterval=15 -o ServerAliveCountMax=2 "$server" "$remote"); then
  fail "could not read the box copies over ssh — nothing compared, deploy stopped"
fi

n=$(printf '%s\n' "$out" | grep -c . || true)
[[ "$n" -eq ${#files[@]} ]] || fail "expected ${#files[@]} answers from the box, got $n — deploy stopped"

differs=()
i=0
while read -r kind a b; do
  f=${files[$i]}
  i=$((i + 1))
  case "$kind" in
    ABSENT) [[ "$a" == "$f" ]] || fail "answer out of order for $f"
            echo "[box-gate] ABSENT    $f" ;;
    SUM)    [[ "$b" == "$f" ]] || fail "answer out of order for $f"
            local_sum=$(shasum -a 256 "$f" | awk '{print $1}')
            if [[ "$a" == "$local_sum" ]]; then
              echo "[box-gate] SAME      $f"
              continue
            fi
            ours=""
            for c in $(git log --format=%H -- "$f"); do
              # a commit that deleted or renamed the file has no blob: pipefail makes that a skip, never a
              # hash of empty output (hashed straight from the pipe: $(...) would strip trailing newlines)
              blob_sum=$(git show "$c:$f" 2>/dev/null | shasum -a 256 | awk '{print $1}') || continue
              if [[ "$blob_sum" == "$a" ]]; then
                ours=${c:0:7}
                break
              fi
            done
            if [[ -n "$ours" ]]; then
              echo "[box-gate] OURS      $f  (box holds commit $ours; local is newer)"
            else
              echo "[box-gate] DIFFERS   $f  (box ${a:0:12}… matches no committed version)" >&2
              differs+=("$f")
            fi ;;
    ERROR)  fail "the box could not checksum $f — deploy stopped" ;;
    *)      fail "unreadable answer from the box: $kind $a $b" ;;
  esac
done <<< "$out"

if [[ ${#differs[@]} -gt 0 ]]; then
  fail "box copy differs and was not shipped from this repo: ${differs[*]}. Resolve each first:
  scp $server:$dest/<file> /tmp/  then diff it against the repo and record the decision (for a prompt, with Heather)"
fi
echo "[box-gate] OK: ${#files[@]} box copies checked; none was edited outside this repo"
