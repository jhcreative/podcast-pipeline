#!/bin/bash
#
# transcribe_episode.sh — The Podcast Manager transcription wrapper (WhisperX edition)
#
# Usage: transcribe_episode.sh <episode_folder_path> <episode_slug>
# Exit codes:
#   0 = success
#   1 = invalid arguments / missing dependencies / out-of-scope path
#   2 = no usable audio source found
#   3 = whisperx failed
#   4 = HF_TOKEN not set

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

# Safety: refuse to operate outside ~/RockstarPodcast/
case "$(realpath "$EPISODE_FOLDER")" in
  "$HOME/RockstarPodcast"/*) ;;
  *) echo "ERROR: Episode folder must be inside ~/RockstarPodcast/. Refusing to operate." >&2; exit 1 ;;
esac

# Load HF token
ENV_FILE="$HOME/RockstarPodcast/scripts/.env"
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

VENV_PATH="$HOME/RockstarPodcast/scripts/whisperx_env"
if [[ ! -d "$VENV_PATH" ]]; then
  echo "ERROR: WhisperX venv not found at $VENV_PATH" >&2
  exit 1
fi

PROMPT_FILE="$HOME/RockstarPodcast/recordings/whisper_prompt.txt"
PROMPT_ARG=""
if [[ -f "$PROMPT_FILE" ]]; then
  PROMPT_ARG="--initial_prompt"
fi

OUTPUT_DIR="$EPISODE_FOLDER/transcripts"
WORK_DIR="$EPISODE_FOLDER/.transcribe_work"
rm -rf "$WORK_DIR"  # clean any stale state from a prior failed run
mkdir -p "$OUTPUT_DIR" "$WORK_DIR"

# --- Detect audio source (three tiers) ---
shopt -s nullglob
ALL_M4A=("$EPISODE_FOLDER"/*.m4a)
ALL_MP4=("$EPISODE_FOLDER"/*.mp4)
shopt -u nullglob

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

# Run WhisperX
echo "INFO: Running WhisperX (transcribe + align + diarize). This takes ~6 min for a 60-min episode."

source "$VENV_PATH/bin/activate"

WHISPERX_CMD=(
  whisperx
  "$INPUT_AUDIO"
  --model large-v3
  --language en
  --diarize
  --output_dir "$WORK_DIR"
  --output_format json
  --compute_type int8
  --batch_size 8
)

# shellcheck disable=SC2206
WHISPERX_CMD+=($DIARIZATION_HINT)

if [[ -n "$PROMPT_ARG" ]]; then
  WHISPERX_CMD+=("$PROMPT_ARG" "$(cat "$PROMPT_FILE" | tr -d '\n')")
fi

if ! "${WHISPERX_CMD[@]}" 2> "$WORK_DIR/whisperx.log"; then
  echo "ERROR: WhisperX failed. See $WORK_DIR/whisperx.log for details." >&2
  deactivate
  exit 3
fi

deactivate

WHISPERX_JSON="$WORK_DIR/input.json"
if [[ ! -f "$WHISPERX_JSON" ]]; then
  echo "ERROR: WhisperX did not produce expected JSON at $WHISPERX_JSON" >&2
  exit 3
fi

# Normalize JSON
jq '{
  episode_slug: "'"$EPISODE_SLUG"'",
  generated_at: now | todate,
  segments: [.segments[] | {
    speaker: (.speaker // "UNKNOWN"),
    start: .start,
    end: .end,
    text: (.text | ltrimstr(" "))
  }]
}' "$WHISPERX_JSON" > "$OUTPUT_DIR/transcript.json"

# Build human-readable .txt
jq -r '.segments[] |
  "[\(.start | floor / 60 | floor):\(.start | floor % 60 | tostring | if length < 2 then "0" + . else . end)] \(.speaker): \(.text)"' \
  "$OUTPUT_DIR/transcript.json" > "$OUTPUT_DIR/transcript.txt"

rm -rf "$WORK_DIR"

echo "SUCCESS: Transcripts written to $OUTPUT_DIR/"
echo "  - transcript.txt"
echo "  - transcript.json"
echo ""
echo "NOTE: Speaker labels are generic (SPEAKER_00, SPEAKER_01, ...)."
echo "The PM will surface a card asking Heather to map them to real names."
exit 0
