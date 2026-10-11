#!/usr/bin/env python3
"""
align_episode.py — full-episode FORCED ALIGNMENT (word-level timing), align-only.

Smart-pipeline insert (ADR 0003, "Reel clip boundaries frame a complete thought").

WHAT
  Reads the existing base-model fast-pass transcript (segment-level text + coarse
  segment timings) and the episode audio, then runs WhisperX's wav2vec2 forced
  aligner against that *already-known* text to recover REAL per-word onset/offset
  times for the whole episode. Writes transcript_aligned.json in the same shape as
  transcript_fast.json but with a `words[]` array on every segment and
  word_aligned: true.

WHY align-only and not a full large-v3 re-transcribe (the compute-restraint call,
BP-3 / "use less electricity is a real reason"):
  WhisperX is two stages — Whisper *transcription* (expensive) and wav2vec2
  *forced alignment* (cheap, because the text is given). The smart pipeline
  (transcribe_fast → extract_quote_clips → transcribe_clips) deliberately avoids
  running large-v3 transcription on the full episode. We honor that: we DON'T
  re-transcribe. We reuse the base pass's text and pay only for one alignment pass.
  That gives the lookup/boundary stage real word gaps for the whole episode at a
  fraction of a transcription pass's cost, so findTimestamps can prove silence and
  emit a real opening breath (the lead_in_ms-comes-out-0 bug this fixes).

WHY the boundary stage needs this and the downstream per-clip alignment doesn't help:
  lookup_quote_timestamps.js runs BEFORE clips are cut. Its only timeline today is
  transcript_fast.json, whose word times are interpolated by even division across a
  segment (buildTimeline) — so inter-word gaps are all zero and the lead-in clamp
  can never prove a breath. Real word timing must exist AT the boundary stage, which
  is upstream of transcribe_clips. Hence a full-episode align pass here, not there.

USAGE
  align_episode.py <episode_folder> [--in transcript_fast.json] [--out transcript_aligned.json]
                   [--audio <file.m4a>] [--model <wav2vec2 model name>]

EXIT
  0 success · 1 bad args / missing input · 2 no audio · 3 alignment failed
"""
import argparse
import glob
import json
import os
import sys
import time


def find_audio(episode_folder, explicit):
    if explicit:
        p = explicit if os.path.isabs(explicit) else os.path.join(episode_folder, explicit)
        return p if os.path.isfile(p) else None
    # Prefer m4a (the canonical source), then mp4. Match transcribe_fast.sh's tiers.
    for pat in ("*.m4a", "*.mp4"):
        hits = sorted(glob.glob(os.path.join(episode_folder, pat)))
        if hits:
            return hits[0]
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("episode_folder")
    ap.add_argument("--in", dest="infile", default=None)
    ap.add_argument("--out", dest="outfile", default=None)
    ap.add_argument("--audio", default=None)
    # wav2vec2 alignment model; None lets WhisperX pick the default English aligner.
    ap.add_argument("--model", default=None)
    args = ap.parse_args()

    folder = args.episode_folder
    infile = args.infile or os.path.join(folder, "transcript_fast.json")
    outfile = args.outfile or os.path.join(folder, "transcript_aligned.json")

    if not os.path.isfile(infile):
        print(f"ERROR: input transcript not found: {infile}", file=sys.stderr)
        sys.exit(1)

    audio_path = find_audio(folder, args.audio)
    if not audio_path:
        print(f"ERROR: no episode audio (*.m4a/*.mp4) found in {folder}", file=sys.stderr)
        sys.exit(2)

    src = json.load(open(infile))
    segments = src.get("segments", [])
    if not segments:
        print(f"ERROR: no segments in {infile}", file=sys.stderr)
        sys.exit(1)

    # Import inside main so --help is instant and import cost is attributed to the run.
    import torch  # noqa: F401  (whisperx needs it; importing here surfaces a clear error if missing)
    import whisperx

    device = "cpu"  # Apple Silicon: WhisperX CPU path is what the rest of the pipeline uses.

    print(f"[align] audio={os.path.basename(audio_path)}  segments={len(segments)}  device={device}")
    t0 = time.time()
    audio = whisperx.load_audio(audio_path)
    t_audio = time.time() - t0
    audio_dur_s = len(audio) / 16000.0  # SAMPLE_RATE
    print(f"[align] loaded audio: {audio_dur_s:.1f}s of audio in {t_audio:.1f}s wall")

    # Forced alignment needs only {start, end, text} per segment — exactly what the
    # base pass already gives us. We do NOT re-transcribe.
    #
    # CRITICAL — extend each segment's END to the NEXT segment's START (last one to
    # the audio end). WHY: the base model's segment `end` timestamps are themselves
    # coarse and frequently land EARLY — the segment text runs longer than its
    # declared window. wav2vec2 only aligns words that fall inside [start, end], so
    # aligning against the base window verbatim silently DROPS every word spoken
    # after the (too-early) end — measured here as ~79% of words lost on E27. Words
    # don't disappear into the gap; the next segment begins where this one's window
    # now ends, so extending to the next start gives the aligner room to place the
    # overflow without overlapping into territory another segment owns. Net effect on
    # the FLAT timeline that buildTimeline/findTimestamps consume: 100% of words
    # recovered, still monotonic. (A word that overflowed seg N simply gets attributed
    # to seg N+1's window; the flat order is unchanged, which is all the lookup uses.)
    audio_end_s = len(audio) / 16000.0
    align_input = []
    for i, s in enumerate(segments):
        win_end = segments[i + 1]["start"] if i + 1 < len(segments) else audio_end_s
        align_input.append({
            "start": s["start"],
            "end": max(win_end, s["end"]),  # never shrink a window below the base end
            "text": s.get("text", ""),
        })

    t1 = time.time()
    model_a, metadata = whisperx.load_align_model(
        language_code="en", device=device, model_name=args.model
    )
    t_load = time.time() - t1
    print(f"[align] wav2vec2 align model loaded in {t_load:.1f}s (model={args.model or 'default-en'})")

    t2 = time.time()
    result = whisperx.align(
        align_input, model_a, metadata, audio, device,
        # 'nearest' fills any word the aligner can't pin (e.g. a number read oddly)
        # by snapping to its neighbour rather than dropping it — keeps the word
        # count stable so the boundary anchors still line up.
        interpolate_method="nearest",
        return_char_alignments=False,
    )
    t_align = time.time() - t2

    # WhisperX's align() RE-SEGMENTS its input — it does NOT return one aligned
    # segment per input segment (E27: 55 in → 249 out). So we must NOT zip the
    # aligned segments back against our input segments; the authoritative, complete
    # word list is the FLAT top-level `word_segments` (every word, in spoken order,
    # each with a real start/end). That flat list is also exactly the shape the
    # boundary stage wants (buildTimeline flattens segments to a word timeline), so
    # we emit it directly as a top-level `words[]` and let lookup consume it.
    word_segments = result.get("word_segments", [])

    flat_words = []
    for w in word_segments:
        # Drop any word the aligner could not place (no timing) rather than emit a
        # NaN boundary the lookup clamp would choke on. With extended windows +
        # 'nearest' interpolation this is empty in practice (E27: 0 dropped).
        if w.get("start") is None or w.get("end") is None:
            continue
        flat_words.append({
            "word": w.get("word", ""),
            "start": float(w["start"]),
            "end": float(w["end"]),
        })

    # Also carry WhisperX's re-segmented segments (with their own per-word arrays),
    # so the file is self-describing and a human/agent can read it as prose. The
    # lookup stage ignores these and reads `words[]`; they're for inspection only.
    out_segments = []
    for al in result.get("segments", []):
        seg_words = [
            {"word": w.get("word", ""), "start": float(w["start"]), "end": float(w["end"])}
            for w in al.get("words", [])
            if w.get("start") is not None and w.get("end") is not None
        ]
        out_segments.append({
            "start": al.get("start"),
            "end": al.get("end"),
            "text": al.get("text", ""),
            "words": seg_words,
        })

    out = {
        "episode_slug": src.get("episode_slug"),
        "pass": "aligned",
        "model": src.get("model", "base"),       # transcription model (unchanged)
        "align_model": args.model or "wav2vec2-en-default",
        "word_aligned": True,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source_transcript": os.path.basename(infile),
        "words": flat_words,         # ← authoritative flat timeline for lookup
        "segments": out_segments,    # ← WhisperX re-segmentation, for human reading
    }
    json.dump(out, open(outfile, "w"), indent=2)

    aligned_word_total = len(flat_words)
    total = time.time() - t0
    print(f"[align] DONE — {len(out_segments)} segments, {aligned_word_total} aligned words → {outfile}")
    print(
        f"[align] COMPUTE: audio={audio_dur_s:.1f}s  "
        f"load_audio={t_audio:.1f}s  load_model={t_load:.1f}s  align={t_align:.1f}s  "
        f"TOTAL={total:.1f}s  (RTF={total/audio_dur_s:.3f}× real-time)"
    )


if __name__ == "__main__":
    main()
