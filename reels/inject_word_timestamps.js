#!/usr/bin/env node
// inject_word_timestamps.js — v2 (2026-05-20)
//
// Smart pipeline stage 4 of 4. Reads per-clip WhisperX JSONs produced by
// transcribe_clips.sh and injects word-level timestamps into quotes.json.
//
// The big difference from v1: v1 read ONE WhisperX JSON for the full episode
// and filtered by per-quote timestamps. v2 reads N per-clip JSONs (one per
// quote) and translates clip-local timestamps to absolute episode time using
// the offsets recorded in clips_manifest.json.
//
// Usage:
//   node inject_word_timestamps.js \
//     --quotes        Episodes/E24-.../quotes.json \
//     --clips-dir     Episodes/E24-.../clips \
//     [--out          Episodes/E24-.../quotes.json]   (defaults to --quotes)
//
// Inputs expected:
//   <clips-dir>/clips_manifest.json
//   <clips-dir>/whisperx/quote_NN.json  (one per quote)
//
// Backward compat: if --whisperx PATH is passed (legacy single-JSON mode),
// fall back to v1 behavior (filter words from one full-episode WhisperX JSON
// by quote.transcript_start_ms / transcript_end_ms). This keeps the old
// transcribe_episode.sh path working for fallback runs.

// ── v3 (2026-07-21, REEL-05) — reconcile, don't overwrite ────────────────────
//
// WHAT CHANGED AND WHY IT MATTERS
//
// v2 took the raw per-clip WhisperX words, filtered them to a time window, and
// assigned them straight to quote.words. Two defects followed, both shipped:
//
//  1. GATE B — this stage runs AFTER lookup_quote_timestamps.js in
//     publish_finalize. When lookup has a word-aligned transcript it reconciles
//     captions against the brand team's CLEANED text (ADR 0003) and may excise
//     interior audio (ADR 0004). v2 then overwrote all of that with raw words,
//     silently undoing both ADRs. We now refuse to touch words[] that lookup has
//     marked `words_source: 'aligned'`.
//
//  2. THE PHANTOM WORD — the v2 tail filter admitted any raw word that ENDED
//     within +500ms of the quote's end. That is a word-inclusion test, not a time
//     clamp, so it let in short next-thought conjunctions ("And", "So", "But")
//     whose whole duration fit in the slop. On E28 reel 2 the guest finished the
//     thought and the caption "And…" from the NEXT thought appeared on screen
//     before the cut. Longer next-words never fit, which is why the defect looked
//     rare and word-specific instead of systematic.
//
// THE FIX for (2) is not a smaller number — it is to stop selecting caption tokens
// by time at all. We now reconcile the raw words against the cleaned quote text
// using the same LCS aligner lookup uses (reconcileCleanedWords). The output token
// list IS the cleaned text, so a word the brand team removed — or a word belonging
// to the next thought — is STRUCTURALLY UNABLE to reach the screen. Timing still
// comes from the real per-clip WhisperX onsets; only the token list is constrained.
//
// This also lets the raw window be deliberately GENEROUS (see WINDOW_* below): LCS
// discards unmatched raw words, so extra context costs nothing, while a too-tight
// window would drop a genuinely late-landing final word. Generous-in, exact-out.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  buildTimelineFromWords,
  reconcileCleanedWords,
  smoothCadence,
} from './lookup_quote_timestamps.js';

// Raw-word window fed to the LCS reconciler, relative to the located quote span.
// Deliberately wider than the old ±100/+500 filter: the reconciler decides which
// tokens survive, so extra raw context is free, and the old tight tail is exactly
// what truncated late-landing final words. These are NOT caption boundaries.
const WINDOW_HEAD_MS = 500;
const WINDOW_TAIL_MS = 1500;

function parseArgs(argv) {
  const a = { quotes: null, clipsDir: null, whisperx: null, out: null };
  for (let i = 2; i < argv.length; i++) {
    if (argv[i] === '--quotes')           a.quotes   = argv[++i];
    else if (argv[i] === '--clips-dir')   a.clipsDir = argv[++i];
    else if (argv[i] === '--whisperx')    a.whisperx = argv[++i];
    else if (argv[i] === '--out')         a.out      = argv[++i];
  }
  if (!a.quotes) {
    console.error('Usage: node inject_word_timestamps.js --quotes PATH (--clips-dir PATH | --whisperx PATH) [--out PATH]');
    process.exit(1);
  }
  if (!a.clipsDir && !a.whisperx) {
    console.error('Must provide either --clips-dir (smart pipeline v1.6.2+) or --whisperx (legacy single-JSON mode)');
    process.exit(1);
  }
  a.out = a.out || a.quotes;
  return a;
}

const args = parseArgs(process.argv);

const quotesData = JSON.parse(fs.readFileSync(args.quotes, 'utf8'));
let totalInjected = 0;

if (args.clipsDir) {
  // ---------- SMART PIPELINE (v2) ----------
  const manifestPath = path.join(args.clipsDir, 'clips_manifest.json');
  if (!fs.existsSync(manifestPath)) {
    console.error(`[inject] clips_manifest.json missing at ${manifestPath}. Run extract_quote_clips.sh first.`);
    process.exit(2);
  }
  const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
  const whisperxDir = path.join(args.clipsDir, 'whisperx');

  console.log(`[inject] Smart pipeline mode — reading ${manifest.clips.length} per-clip WhisperX JSONs from ${whisperxDir}`);

  for (const quote of quotesData.quotes) {
    // ── Gate B guard (REEL-05) ────────────────────────────────────────────────
    // lookup_quote_timestamps.js already reconciled these captions against the
    // cleaned text on real aligned timing, and may have remapped them onto a
    // post-excision local timeline (ADR 0004). Overwriting that with raw words is
    // a regression, not an injection. Leave it alone.
    if (quote.words_source === 'aligned' && Array.isArray(quote.words) && quote.words.length > 0) {
      console.log(
        `[inject] Quote ${String(quote.index).padStart(2,'0')} "${quote.topic}": ` +
        `${quote.words.length} words already reconciled by lookup (words_source=aligned) — preserving.`
      );
      continue;
    }

    const clipEntry = manifest.clips.find(c => c.quote_index === quote.index);
    if (!clipEntry) {
      console.warn(`[inject] WARN: no clip manifest entry for quote ${quote.index} (${quote.topic}). Leaving words[] empty.`);
      quote.words = quote.words || [];
      continue;
    }

    const clipJsonName = `quote_${String(quote.index).padStart(2, '0')}.json`;
    const clipJsonPath = path.join(whisperxDir, clipJsonName);

    if (!fs.existsSync(clipJsonPath)) {
      console.warn(`[inject] WARN: missing ${clipJsonName} for quote ${quote.index}. Leaving words[] empty.`);
      quote.words = quote.words || [];
      continue;
    }

    const clipData = JSON.parse(fs.readFileSync(clipJsonPath, 'utf8'));

    // Collect words from clip WhisperX output
    let clipWords = [];
    if (Array.isArray(clipData.word_segments) && clipData.word_segments.length > 0) {
      clipWords = clipData.word_segments;
    } else if (Array.isArray(clipData.segments)) {
      for (const seg of clipData.segments) {
        if (Array.isArray(seg.words)) clipWords.push(...seg.words);
      }
    }

    // Filter out words without timing data
    clipWords = clipWords
      .filter(w => typeof w.start === 'number' && typeof w.end === 'number' && w.word)
      .map(w => ({ ...w, word: w.word.trim() }))
      .filter(w => w.word);

    // Translate clip-local timestamps to absolute episode timestamps.
    // Clip starts at clipEntry.clip_start_ms in the source episode.
    const clipOffsetMs = clipEntry.clip_start_ms;
    const absoluteWords = clipWords.map(w => ({
      word:     w.word,
      start_ms: Math.round(w.start * 1000) + clipOffsetMs,
      end_ms:   Math.round(w.end   * 1000) + clipOffsetMs,
    }));

    // Gather a GENEROUS raw window around the located quote span. The reconciler
    // below — not this filter — decides which tokens become captions, so extra
    // context is harmless and a tight tail is actively harmful (it truncates a
    // final word that lands later than the interpolated boundary predicted).
    const windowStart = quote.transcript_start_ms - WINDOW_HEAD_MS;
    const windowEnd   = quote.transcript_end_ms   + WINDOW_TAIL_MS;
    const windowed = absoluteWords.filter(w => w.start_ms >= windowStart && w.end_ms <= windowEnd);

    const cleanedText = quote.text || quote.quote_text || quote.quote_text_clean || '';

    if (!cleanedText.trim() || windowed.length === 0) {
      // No cleaned text to reconcile against (or nothing spoken in range). Fail
      // visibly rather than emitting raw words that may not be this thought's.
      console.warn(
        `[inject] WARN: quote ${quote.index} has ${windowed.length} raw words and ` +
        `${cleanedText.trim() ? 'cleaned text' : 'NO cleaned text'} — leaving words[] empty ` +
        `(renderer falls back to block captions).`
      );
      quote.words = [];
      continue;
    }

    // Reconcile: tokens come from the CLEANED text, timings from the real per-clip
    // WhisperX onsets. buildTimelineFromWords wants seconds. A word the brand team
    // removed, or one belonging to the next thought, cannot appear in the output —
    // that is the structural fix for the E28 phantom "And".
    const timeline = buildTimelineFromWords(
      windowed.map(w => ({ word: w.word, start: w.start_ms / 1000, end: w.end_ms / 1000 }))
    );
    const reconciled = reconcileCleanedWords(cleanedText, timeline, 0, timeline.length - 1)
      .map(w => ({
        word: w.word,
        start_ms: Math.round(w.start_ms),
        end_ms: Math.round(w.end_ms),
      }));

    quote.words = smoothCadence(reconciled);
    quote.words_source = 'per-clip-reconciled';
    console.log(
      `[inject] Quote ${String(quote.index).padStart(2,'0')} "${quote.topic}": ` +
      `${quote.words.length} caption words from cleaned text ` +
      `(raw in window: ${windowed.length}, clip raw: ${absoluteWords.length})`
    );
    totalInjected += quote.words.length;
  }

  quotesData.selection_notes = (quotesData.selection_notes || '')
    + ` | Word timestamps injected from per-clip WhisperX runs ${new Date().toISOString()}.`;
} else {
  // ---------- LEGACY MODE (v1 — full-episode WhisperX JSON) ----------
  console.log(`[inject] Legacy single-JSON mode — reading words from full-episode WhisperX output`);
  const wxData = JSON.parse(fs.readFileSync(args.whisperx, 'utf8'));

  let allWords = [];
  if (Array.isArray(wxData.word_segments) && wxData.word_segments.length > 0) {
    allWords = wxData.word_segments;
  } else if (Array.isArray(wxData.segments)) {
    for (const seg of wxData.segments) {
      if (Array.isArray(seg.words)) allWords.push(...seg.words);
    }
  }

  allWords = allWords
    .filter(w => typeof w.start === 'number' && typeof w.end === 'number' && w.word)
    .map(w => ({ ...w, word: w.word.trim() }))
    .filter(w => w.word);

  console.log(`[inject] WhisperX words available: ${allWords.length}`);

  for (const quote of quotesData.quotes) {
    const startSec = quote.transcript_start_ms / 1000;
    const endSec   = quote.transcript_end_ms   / 1000;

    const words = allWords
      .filter(w => w.start >= startSec - 0.1 && w.end <= endSec + 0.5)
      .map(w => ({
        word:     w.word,
        start_ms: Math.round(w.start * 1000),
        end_ms:   Math.round(w.end   * 1000),
      }));

    quote.words = words;
    console.log(`[inject] Quote ${String(quote.index).padStart(2,'0')} "${quote.topic}": ${words.length} words`);
    totalInjected += words.length;
  }
}

fs.writeFileSync(args.out, JSON.stringify(quotesData, null, 2), 'utf8');
console.log(`[inject] Done. ${totalInjected} words written to ${args.out}`);
