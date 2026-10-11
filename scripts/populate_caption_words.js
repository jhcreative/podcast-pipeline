#!/usr/bin/env node
/**
 * populate_caption_words.js — fill quotes[].words[] with BRAND-CLEANED caption
 * tokens carried on the speaker's REAL word timing.
 * v1.0 · 2026-06-22 (Jim)
 *
 * WHY THIS EXISTS (the E27 caption regression)
 * --------------------------------------------
 * render_reel.js does accumulating word-by-word "karaoke" only when a quote has a
 * populated words[] array ({word,start_ms,end_ms}); an empty words[] falls back to
 * line/block captions. The local E27 render skipped word population, so it
 * block-fell-back — John flagged it.
 *
 * The naive fix (inject_word_timestamps.js) injects the RAW spoken words from
 * per-clip WhisperX. That regresses the caption TEXT: it would put fillers and the
 * host interjection (cleaned out of q02) back on screen, and show "blow up" instead
 * of the brand-corrected "glow up". The captions MUST be the cleaned text.
 *
 * So this step reconciles the CLEANED caption tokens to the RAW aligned word
 * timings (transcript_aligned.json), within each quote's already-located
 * [transcript_start_ms, transcript_end_ms] window. The reconciliation logic lives
 * in lookup_quote_timestamps.js (reconcileCleanedWords) because that module owns
 * the aligned timeline and the cleaned-vs-raw drift knowledge — see the long note
 * there. This file is the thin CLI that wires it for a render.
 *
 * WHERE THIS SHOULD LIVE ON THE SERVER PATH
 * -----------------------------------------
 * On the server, lookup_quote_timestamps.js already builds the aligned timeline and
 * returns start_idx/end_idx for every quote. The cleanest home is to call
 * reconcileCleanedWords RIGHT THERE — inside lookup's per-quote loop, using the
 * start_idx/end_idx it already computed — and emit words[] in the same pass that
 * emits transcript_start_ms/end_ms/lead_in_ms. That makes words[] a first-class
 * lookup output and retires inject_word_timestamps.js for word-aligned episodes.
 * This standalone file exists so the LOCAL render can populate words[] without
 * re-running the whole boundary pass over an already-finalized quotes.json.
 *
 * The display token is the CLEANED word (with its punctuation, e.g. "marriage,");
 * the timing is the speaker's real onset/offset (matched) or interpolated for the
 * rare cleaned-only word. Output shape matches the approved E23/E24/E25 reels.
 *
 * Usage:
 *   node populate_caption_words.js \
 *     --quotes     Episodes/E27-.../quotes-localtest.json \
 *     --aligned    Episodes/E27-.../transcript_aligned.json \
 *     [--out       <defaults to --quotes>] \
 *     [--caption-field text|quote_text_clean|reel_hook]   (default: text)
 *
 * THE CAPTION FIELD MUST BE THE SAME FIELD THE BOUNDARY FINDER USES (the E27
 * blank-band regression). lookup_quote_timestamps.js cuts the AUDIO clip to the
 * full quote `text` (findTimestamps(q.text)). The on-screen karaoke MUST be
 * sourced from that SAME `text`, so the caption span matches the audio span.
 * The earlier default (quote_text_clean, which is reel_hook || text) sourced the
 * karaoke from the SHORTER social hook: the audio ran the full thought while the
 * captions ran out at the hook's end, leaving a blank purple band for the rest of
 * the clip (e.g. reel 01 audio ends "...how am I going to get through this?" but
 * the hook stopped at "...I am too old for this."). reel_hook is ONLY the
 * social-post caption copy (reel_*_captions.md) — never the on-screen karaoke.
 */

import fs from 'node:fs';
import { buildTimelineFromWords, reconcileCleanedWords } from './lookup_quote_timestamps.js';

function parseArgs(argv) {
  const a = { quotes: null, aligned: null, out: null, captionField: 'text' };
  for (let i = 2; i < argv.length; i++) {
    if (argv[i] === '--quotes')             a.quotes = argv[++i];
    else if (argv[i] === '--aligned')       a.aligned = argv[++i];
    else if (argv[i] === '--out')           a.out = argv[++i];
    else if (argv[i] === '--caption-field') a.captionField = argv[++i];
  }
  if (!a.quotes || !a.aligned) {
    console.error('Usage: node populate_caption_words.js --quotes PATH --aligned PATH [--out PATH] [--caption-field text|quote_text_clean|reel_hook]');
    process.exit(1);
  }
  a.out = a.out || a.quotes;
  return a;
}

const args = parseArgs(process.argv);

const quotesData = JSON.parse(fs.readFileSync(args.quotes, 'utf8'));
const alignedData = JSON.parse(fs.readFileSync(args.aligned, 'utf8'));

if (!Array.isArray(alignedData.words) || alignedData.words.length === 0) {
  console.error(`[populate] ${args.aligned} has no top-level words[] — not a word-aligned transcript.`);
  process.exit(2);
}

// Same flat timeline the boundary finder uses, so ms→index mapping is exact.
const timeline = buildTimelineFromWords(alignedData.words);
console.log(`[populate] aligned timeline: ${timeline.length} words from ${args.aligned}`);

// Map a quote's [start_ms,end_ms] window onto timeline indices. The timeline came
// from THIS aligned transcript, so the located thought's words are exactly those
// whose [start,end] fall inside the window (small slop absorbs rounding).
function windowToIndices(startMs, endMs) {
  const loS = (startMs - 50) / 1000;
  const hiS = (endMs + 50) / 1000;
  let startIdx = -1, endIdx = -1;
  for (let i = 0; i < timeline.length; i++) {
    if (timeline[i].start >= loS && timeline[i].end <= hiS) {
      if (startIdx === -1) startIdx = i;
      endIdx = i;
    } else if (startIdx !== -1 && timeline[i].start > hiS) {
      break;
    }
  }
  return { startIdx, endIdx };
}

let totalWords = 0;
for (const q of quotesData.quotes) {
  // Source the karaoke from the SAME field the boundary finder cut the audio to
  // (q.text). Fall back to text before quote_text_clean/reel_hook so a missing
  // configured field can never silently re-introduce the shorter-hook regression.
  const cleaned = q[args.captionField] || q.text || q.quote_text_clean || q.reel_hook || '';
  if (typeof q.transcript_start_ms !== 'number' || typeof q.transcript_end_ms !== 'number') {
    console.warn(`[populate] q${q.index ?? q.id}: missing transcript timestamps — leaving words[] empty.`);
    q.words = q.words || [];
    continue;
  }
  const { startIdx, endIdx } = windowToIndices(q.transcript_start_ms, q.transcript_end_ms);
  if (startIdx < 0 || endIdx < startIdx) {
    console.warn(`[populate] q${q.index ?? q.id}: no aligned words in window ${q.transcript_start_ms}-${q.transcript_end_ms}ms — leaving words[] empty.`);
    q.words = q.words || [];
    continue;
  }
  const words = reconcileCleanedWords(cleaned, timeline, startIdx, endIdx);
  q.words = words;
  totalWords += words.length;

  // How many cleaned tokens got a REAL (matched) timing vs interpolated — a quick
  // honesty signal. We recompute match-vs-interp by re-reconciling is overkill;
  // instead report span coverage: first/last word ms vs the quote window.
  const firstMs = words[0]?.start_ms, lastMs = words[words.length - 1]?.end_ms;
  console.log(
    `[populate] q${q.index ?? q.id}: ${words.length} cleaned words → ${firstMs}-${lastMs}ms ` +
    `(window ${q.transcript_start_ms}-${q.transcript_end_ms}ms, raw span idx ${startIdx}-${endIdx})`
  );
}

fs.writeFileSync(args.out, JSON.stringify(quotesData, null, 2), 'utf8');
console.log(`[populate] done — ${totalWords} cleaned words written to ${args.out}`);
