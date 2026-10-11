#!/bin/bash
#
# transcribe_fast.sh — FAST first-pass transcription for quote selection
#
# Smart pipeline architecture (v1.6.2, 2026-05-20):
#   Stage 1: This script runs WhisperX with a SMALL model + diarization, no
#            word alignment. Output is text + segment-level timestamps +
#            speaker labels. Fast enough to fit in scheduled-task budgets.
#            Used for: intro/outro boundary detection + Brand Team quote
#            selection from text.
#
#   Stage 2: extract_quote_clips.sh cuts the 5 selected quote audio segments.
#
#   Stage 3: transcribe_clips.sh runs WhisperX large-v3 + alignment ONLY on
#            those 5 small clips for precise word-level timestamps.
#
#   Stage 4: inject_word_timestamps.js merges per-clip word data into
#            quotes.json.
#
# Net result: ~10× less large-v3 compute than the old transcribe_episode.sh
# pattern, because expensive word alignment runs only on the 5 selected
# clips (~5 min total audio) instead of the full ~50 min episode.
#
# Usage: transcribe_fast.sh <episode_folder_path> <episode_slug>
# Exit codes:
#   0 = success
#   1 = invalid arguments / missing dependencies / out-of-scope path
#   2 = no usable audio source found
#   3 = whisperx failed
#   4 = HF_TOKEN not set (still needed for pyannote diarization)

set -euo pipefail

EPISODE_FOLDER="${1:-}"
EPISODE_SLUG="${2:-}"

if [[ -z "$EPISODE_FOLDER" || -z "$EPISODE_SLUG" ]]; then
  echo "ERROR: Usage: $0 <episode_folder_path> <episode_slug>" >&2
  exit 1
fi

if [[ ! -d "$EPISODE_FOLDER" ]]; then
  echo "ERROR: Episode folder not found: $EPISODE_FOLDER" >&2
  exit 1
fi

# Safety: refuse to operate outside ~/RockstarPodcast/ or its Drive mirror.
# The rockstar-* arms are for the server: round_1.py / publish_finalize.py stage
# each episode in a per-job work dir under workspace.work_dir_base(). That base
# moved from /tmp to /var/tmp/rockstar-work in 0d8ed56 ("work dirs on disk, not
# RAM" — /tmp is a tmpfs that was OOM-crashing WhisperX), so BOTH arms are honored:
# the old /tmp/rockstar-<job> and the current /var/tmp/rockstar-work/rockstar-<job>.
# Harmless on the Mac, which never stages episodes in either — this keeps one
# script correct on both hosts.
case "$(realpath "$EPISODE_FOLDER")" in
  "$HOME/RockstarPodcast"/*|*"/My Drive/RockstarPodcast"/*|*"/GoogleDrive-"*"/My Drive/RockstarPodcast"/*|/tmp/rockstar-*|/var/tmp/rockstar-*) ;;
  *) echo "ERROR: Episode folder must be inside ~/RockstarPodcast/, its Drive mirror, or /tmp/rockstar-* or /var/tmp/rockstar-* (server). Refusing to operate." >&2; exit 1 ;;
esac

# Load HF token (pyannote diarization needs it).
# ENV_FILE/PROMPT_FILE/VENV_PATH/WORK_DIR_BASE all default to the Mac dev layout
# and are overridden per-env by the server's systemd unit (Environment=/EnvironmentFile=).
# Parameterizing them is what lets deploy.sh ship ONE canonical copy whose sha256
# matches on both hosts — the divergence that used to make this script drift.
ENV_FILE="${ENV_FILE:-$HOME/RockstarPodcast/scripts/.env}"
if [[ ! -f "$ENV_FILE" ]]; then
  echo "ERROR: $ENV_FILE not found. Create it with HF_TOKEN=hf_..." >&2
  exit 4
fi
# shellcheck disable=SC1090
source "$ENV_FILE"
if [[ -z "${HF_TOKEN:-}" ]]; then
  echo "ERROR: HF_TOKEN not set in $ENV_FILE" >&2
  exit 4
fi
# HF-01 (2026-10-01): the token reaches WhisperX through its ENVIRONMENT, never its argv.
# A command line is world-readable on Linux (ps, /proc/<pid>/cmdline) for the whole run, so
# `--hf_token "$HF_TOKEN"` showed the token to every user and process on the box, and to anyone
# who ran `ps` while debugging. A process environment is readable only by its own user and root.
# whisperx 3.8.5 with no --hf_token passes token=None to pyannote, and huggingface_hub then reads
# HF_TOKEN from the environment (verified on the box, 2026-10-01). test_hf01_token_off_cmdline.py
# keeps it that way.
export HF_TOKEN

# Dependency checks
command -v ffmpeg >/dev/null 2>&1 || { echo "ERROR: ffmpeg not found. brew install ffmpeg" >&2; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "ERROR: jq not found. brew install jq" >&2; exit 1; }

VENV_PATH="${VENV_PATH:-$HOME/RockstarPodcast/scripts/whisperx_env}"
if [[ ! -d "$VENV_PATH" ]]; then
  echo "ERROR: WhisperX venv not found at $VENV_PATH" >&2
  exit 1
fi

PROMPT_FILE="${PROMPT_FILE:-$HOME/RockstarPodcast/recordings/whisper_prompt.txt}"

OUTPUT_DIR="$EPISODE_FOLDER/transcripts"

# IMPORTANT: WORK_DIR lives on LOCAL disk, NOT inside the Episode folder.
# Reason: when the Episode folder is in Drive (the v1.6.6 canonical location),
# intermediate files written there would go through Drive's File Provider —
# which has been observed to SUSPEND reader processes mid-read on stream-mode
# files (see E25 2026-05-20: ffmpeg sat in `TN` state for 34 minutes with 0%
# CPU while File Provider tried to fault in audio chunks). Putting WORK_DIR
# on local disk + copying source audio in via `cp` (which triggers a clean
# synchronous download) prevents that. Output transcript_fast.json still goes
# back to Drive at the end.
SAFE_SLUG=$(echo "$EPISODE_SLUG" | tr -c 'a-zA-Z0-9_-' '_')
WORK_DIR="${WORK_DIR_BASE:-$HOME/RockstarPodcast/.tmp}/transcribe_fast_${SAFE_SLUG}_$$"
rm -rf "$WORK_DIR"
mkdir -p "$OUTPUT_DIR" "$WORK_DIR"

# Hygiene: remove any orphan work dirs from previous interrupted runs (>2 hours old).
# Each run creates its own PID-suffixed dir; successful runs clean themselves up at
# the end. Failed/interrupted runs leave the dir behind for debugging. 2 hours is
# generous — a normal whisperx run completes in 10-20 min.
find "${WORK_DIR_BASE:-$HOME/RockstarPodcast/.tmp}" -maxdepth 1 -name 'transcribe_fast_*' -type d -mmin +120 -exec rm -rf {} + 2>/dev/null || true

# --- Detect audio source (three tiers — same as legacy transcribe_episode.sh) ---
shopt -s nullglob
ALL_M4A=("$EPISODE_FOLDER"/*.m4a)
ALL_MP4=("$EPISODE_FOLDER"/*.mp4)
shopt -u nullglob

# --- Pre-materialize source audio locally ---
# Copy the detected source files from Episode folder (possibly Drive) into the
# local WORK_DIR. `cp` triggers Drive's File Provider to download the file
# synchronously in one shot, instead of leaving ffmpeg suspended mid-stream.
# 10-min timeout per file protects against truly-stuck Drive sync.
# Note: macOS doesn't ship GNU `timeout` (it's coreutils-only). If Drive cp
# ever hangs in a pathological case, manual `pkill cp` is the escape hatch.
# In practice cp on Drive paths completes — the suspension we saw with ffmpeg
# was specific to seekable random-access reads, not sequential bulk copies.
if [[ ${#ALL_M4A[@]} -ge 1 ]]; then
  echo "INFO: Copying ${#ALL_M4A[@]} source m4a file(s) from Episode folder to local work dir..."
  LOCAL_M4A=()
  for SRC in "${ALL_M4A[@]}"; do
    DST="$WORK_DIR/$(basename "$SRC")"
    if ! cp "$SRC" "$DST"; then
      echo "ERROR: cp failed on $SRC" >&2
      exit 2
    fi
    LOCAL_M4A+=("$DST")
  done
  ALL_M4A=("${LOCAL_M4A[@]}")
fi
if [[ ${#ALL_MP4[@]} -ge 1 ]]; then
  echo "INFO: Copying ${#ALL_MP4[@]} source mp4 file(s) from Episode folder to local work dir..."
  LOCAL_MP4=()
  for SRC in "${ALL_MP4[@]}"; do
    DST="$WORK_DIR/$(basename "$SRC")"
    if ! cp "$SRC" "$DST"; then
      echo "ERROR: cp failed on $SRC" >&2
      exit 2
    fi
    LOCAL_MP4+=("$DST")
  done
  ALL_MP4=("${LOCAL_MP4[@]}")
fi

INPUT_AUDIO=""
DIARIZATION_HINT=""

if [[ ${#ALL_M4A[@]} -ge 2 ]]; then
  echo "INFO: Found ${#ALL_M4A[@]} per-participant .m4a files. Mixing for diarization."
  CONCAT_LIST="$WORK_DIR/concat_list.txt"
  : > "$CONCAT_LIST"
  for FILE in "${ALL_M4A[@]}"; do
    echo "file '$FILE'" >> "$CONCAT_LIST"
  done
  ffmpeg -y -loglevel error -f concat -safe 0 -i "$CONCAT_LIST" \
    -filter_complex "amix=inputs=1:duration=longest" \
    -ar 16000 -ac 1 -c:a pcm_s16le "$WORK_DIR/input.wav"
  INPUT_AUDIO="$WORK_DIR/input.wav"
  DIARIZATION_HINT="--min_speakers 2 --max_speakers ${#ALL_M4A[@]}"
elif [[ ${#ALL_M4A[@]} -eq 1 ]]; then
  echo "INFO: Found single combined .m4a: $(basename "${ALL_M4A[0]}"). Diarizing combined audio."
  ffmpeg -y -loglevel error -i "${ALL_M4A[0]}" \
    -ar 16000 -ac 1 -c:a pcm_s16le "$WORK_DIR/input.wav"
  INPUT_AUDIO="$WORK_DIR/input.wav"
  DIARIZATION_HINT="--min_speakers 2 --max_speakers 4"
elif [[ ${#ALL_MP4[@]} -ge 1 ]]; then
  echo "INFO: No .m4a found. Extracting audio from .mp4: $(basename "${ALL_MP4[0]}")."
  ffmpeg -y -loglevel error -i "${ALL_MP4[0]}" \
    -ar 16000 -ac 1 -c:a pcm_s16le "$WORK_DIR/input.wav"
  INPUT_AUDIO="$WORK_DIR/input.wav"
  DIARIZATION_HINT="--min_speakers 2 --max_speakers 4"
else
  echo "ERROR: No usable audio source in $EPISODE_FOLDER (looked for *.m4a and *.mp4)" >&2
  exit 2
fi

# Run WhisperX in FAST mode:
#   --model base       (small enough for ~1-2 min Whisper pass)
#   --no_align         (skip word-level alignment — we'll do that per-clip later)
#   --diarize          (still need speaker labels for intro/outro detection)
# Estimated runtime: ~5-10 min for 50-min episode on CPU.
echo "INFO: Running WhisperX FAST pass (base model + diarize, no word alignment)."
echo "INFO: Estimated runtime: 5-10 min for a 50-min episode (vs 20-30 min for full large-v3)."

source "$VENV_PATH/bin/activate"

WHISPERX_CMD=(
  whisperx
  "$INPUT_AUDIO"
  --model base
  --language en
  --no_align
  --diarize
  --output_dir "$WORK_DIR"
  --output_format json
  --compute_type int8
  --batch_size 16
)

# shellcheck disable=SC2206
WHISPERX_CMD+=($DIARIZATION_HINT)

if [[ -f "$PROMPT_FILE" ]]; then
  WHISPERX_CMD+=(--initial_prompt "$(cat "$PROMPT_FILE" | tr -d '\n')")
fi

if ! "${WHISPERX_CMD[@]}" > "$WORK_DIR/whisperx.log" 2>&1; then
  echo "ERROR: WhisperX fast pass failed. See $WORK_DIR/whisperx.log for details." >&2
  deactivate
  exit 3
fi

deactivate

WHISPERX_JSON="$WORK_DIR/input.json"
if [[ ! -f "$WHISPERX_JSON" ]]; then
  echo "ERROR: WhisperX did not produce expected JSON at $WHISPERX_JSON" >&2
  exit 3
fi

# Normalize JSON. Note: no word-level data — that comes from transcribe_clips.sh later.
jq '{
  episode_slug: "'"$EPISODE_SLUG"'",
  pass: "fast",
  model: "base",
  word_aligned: false,
  generated_at: now | todate,
  segments: [.segments[] | {
    speaker: (.speaker // "UNKNOWN"),
    start: .start,
    end: .end,
    text: (.text | ltrimstr(" "))
  }]
}' "$WHISPERX_JSON" > "$OUTPUT_DIR/transcript_fast.json"

# Human-readable
jq -r '.segments[] |
  "[\(.start | floor / 60 | floor):\(.start | floor % 60 | tostring | if length < 2 then "0" + . else . end)] \(.speaker): \(.text)"' \
  "$OUTPUT_DIR/transcript_fast.json" > "$OUTPUT_DIR/transcript_fast.txt"

rm -rf "$WORK_DIR"

echo "SUCCESS: FAST transcript written to $OUTPUT_DIR/"
echo "  - transcript_fast.txt (human-readable, segment-level)"
echo "  - transcript_fast.json (segment-level, no word alignment)"
echo ""
echo "NEXT: PM uses transcript_fast.json for:"
echo "  - detect_boundaries.js (intro/outro detection + speaker mapping)"
echo "  - Brand Team quote selection → quotes.json"
echo "Then extract_quote_clips.sh + transcribe_clips.sh for precise word timestamps."
exit 0
