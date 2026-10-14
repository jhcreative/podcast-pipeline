#!/usr/bin/env node

/**
 * test_inject_word_timestamps.js — the caption-token contract for inject_word_timestamps.js.
 * v1.0 · 2026-07-21
 *
 * Born from REEL-05. Until today this script had NO tests at all, and it was the
 * direct cause of E28 reel 2's phantom caption: the guest finished the thought, the
 * word "And" from the NEXT thought appeared on screen, and then the reel cut.
 *
 * ROOT CAUSE the matrix below locks out — v2 selected caption tokens BY TIME:
 *
 *     const windowEnd = quote.transcript_end_ms + 500;
 *     absoluteWords.filter(w => w.start_ms >= windowStart && w.end_ms <= windowEnd)
 *
 * That is a word-INCLUSION test, not a time clamp. Any next-thought word short
 * enough to FINISH inside the 500ms slop was admitted — a precise fingerprint for
 * monosyllabic conjunctions ("And", "So", "But", "Then") and the reason the defect
 * read as rare and word-specific rather than as systematic overshoot. Longer words
 * never fit, so nobody caught it for three episodes (E23 q03 shipped "So"; E24 has
 * three more; E28 was simply the first anyone noticed).
 *
 * THE INVARIANT: caption tokens come from the brand team's CLEANED text, never from
 * raw transcription. Timing comes from real WhisperX onsets. A word the brand team
 * removed — or one belonging to a neighbouring thought — must be STRUCTURALLY unable
 * to reach the screen, regardless of how the time windows are tuned.
 *
 * The matrix:
 *   1. PHANTOM NEXT-WORD — a short conjunction starts just after the quote ends.
 *                          E28 reel 2's exact shape. Must NOT appear in words[].
 *   2. CLEANED-OUT FILLER — "um"/stutter spoken INSIDE the span, absent from the
 *                          cleaned text. Must NOT appear.
 *   3. GATE B — lookup already reconciled words[] on aligned timing
 *                          (words_source: 'aligned'). inject must PRESERVE it, not
 *                          overwrite. This is what silently undid ADR 0003/0004.
 *   4. LATE-LANDING TAIL — the true final word starts beyond the old +500ms tail.
 *                          It must still be captioned (the truncation defect).
 *   5. TOKEN FIDELITY — words[] length and order equal the cleaned text's tokens.
 *   6. MONOTONIC TIMING — spans are ordered and non-degenerate.
 *
 * Runs inject as a SUBPROCESS against temp fixtures, because the script is a CLI
 * that executes on import. No audio, no WhisperX, no network.
 *
 * Usage:   node test_inject_word_timestamps.js
 * Exit:    0 = all cases pass · 1 = one or more failed
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { execFileSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const INJECT = path.join(HERE, 'inject_word_timestamps.js');

let failures = 0;
function check(name, cond, detail) {
  if (cond) {
    console.log(`  ✓ ${name}`);
  } else {
    failures++;
    console.log(`  ✗ ${name}${detail ? `\n      ${detail}` : ''}`);
  }
}

/**
 * Build a fixture episode dir and run inject against it.
 * rawWords: [{word, startMs, endMs}] in ABSOLUTE episode ms (as WhisperX would give
 * them once clip offsets are applied — we use clip_start_ms 0 so they pass through).
 */
function runInject({ quote, rawWords }) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'inject-test-'));
  const clipsDir = path.join(dir, 'clips');
  const wxDir = path.join(clipsDir, 'whisperx');
  fs.mkdirSync(wxDir, { recursive: true });

  const quotesPath = path.join(dir, 'quotes.json');
  fs.writeFileSync(quotesPath, JSON.stringify({ quotes: [quote] }, null, 2));

  fs.writeFileSync(
    path.join(clipsDir, 'clips_manifest.json'),
    JSON.stringify({ clips: [{ quote_index: quote.index, clip_start_ms: 0 }] }, null, 2)
  );

  fs.writeFileSync(
    path.join(wxDir, `quote_${String(quote.index).padStart(2, '0')}.json`),
    JSON.stringify({
      word_segments: rawWords.map(w => ({
        word: w.word, start: w.startMs / 1000, end: w.endMs / 1000,
      })),
    }, null, 2)
  );

  execFileSync('node', [INJECT, '--quotes', quotesPath, '--clips-dir', clipsDir], {
    stdio: 'pipe', encoding: 'utf8',
  });

  const out = JSON.parse(fs.readFileSync(quotesPath, 'utf8')).quotes[0];
  fs.rmSync(dir, { recursive: true, force: true });
  return out;
}

const tokens = q => (q.words || []).map(w => w.word);

// ── Case 1 — THE PHANTOM NEXT-WORD (E28 reel 2) ──────────────────────────────
// The guest lands on "healthy." at 5000ms. The next thought opens with "And" at
// 5150–5300ms — 300ms long, so it FINISHED inside the old +500ms tail and was
// admitted as a caption. It must not survive the cleaned-text contract.
console.log('\nCase 1 — phantom next-thought conjunction (E28 reel 2 shape)');
{
  const q = runInject({
    quote: {
      id: 'q02', index: 2, topic: 'remodeling',
      text: "it's going to get healthy.",
      transcript_start_ms: 4000, transcript_end_ms: 5000,
    },
    rawWords: [
      { word: "it's",    startMs: 4000, endMs: 4200 },
      { word: 'going',   startMs: 4200, endMs: 4450 },
      { word: 'to',      startMs: 4450, endMs: 4600 },
      { word: 'get',     startMs: 4600, endMs: 4800 },
      { word: 'healthy.',startMs: 4800, endMs: 5000 },
      { word: 'And',     startMs: 5150, endMs: 5300 },   // ← the phantom
    ],
  });
  check('"And" is NOT captioned', !tokens(q).includes('And'), `got: ${JSON.stringify(tokens(q))}`);
  check('last caption word is the quote\'s last word',
    tokens(q)[tokens(q).length - 1] === 'healthy.', `got: ${JSON.stringify(tokens(q))}`);
  check('token count equals cleaned text token count',
    tokens(q).length === 5, `got ${tokens(q).length}`);
}

// ── Case 2 — CLEANED-OUT INTERIOR FILLER ─────────────────────────────────────
console.log('\nCase 2 — filler spoken inside the span but cleaned from the text');
{
  const q = runInject({
    quote: {
      id: 'q01', index: 1, topic: 'bone',
      text: 'bone health is diagnosed in the emergency room.',
      transcript_start_ms: 1000, transcript_end_ms: 2400,
    },
    rawWords: [
      { word: 'bone',      startMs: 1000, endMs: 1150 },
      { word: 'health',    startMs: 1150, endMs: 1350 },
      { word: 'um',        startMs: 1350, endMs: 1500 },  // ← cleaned out
      { word: 'is',        startMs: 1500, endMs: 1600 },
      { word: 'diagnosed', startMs: 1600, endMs: 1900 },
      { word: 'in',        startMs: 1900, endMs: 1980 },
      { word: 'the',       startMs: 1980, endMs: 2060 },
      { word: 'emergency', startMs: 2060, endMs: 2250 },
      { word: 'room.',     startMs: 2250, endMs: 2400 },
    ],
  });
  check('"um" is NOT captioned', !tokens(q).includes('um'), `got: ${JSON.stringify(tokens(q))}`);
  check('all 8 cleaned tokens present', tokens(q).length === 8, `got ${tokens(q).length}`);
}

// ── Case 3 — GATE B: do not overwrite lookup's reconciled captions ────────────
console.log('\nCase 3 — Gate B: words_source "aligned" is preserved');
{
  const preexisting = [
    { word: 'Already', start_ms: 100, end_ms: 300 },
    { word: 'reconciled.', start_ms: 300, end_ms: 600 },
  ];
  const q = runInject({
    quote: {
      id: 'q03', index: 3, topic: 'gate-b',
      text: 'Already reconciled.',
      transcript_start_ms: 100, transcript_end_ms: 600,
      words_source: 'aligned',
      words: preexisting,
      audio_segments: [{ start_ms: 0, end_ms: 900 }],
    },
    rawWords: [
      { word: 'Totally',  startMs: 100, endMs: 300 },
      { word: 'different',startMs: 300, endMs: 500 },
      { word: 'raw',      startMs: 500, endMs: 600 },
    ],
  });
  check('words[] untouched', JSON.stringify(q.words) === JSON.stringify(preexisting),
    `got: ${JSON.stringify(q.words)}`);
  check('words_source still "aligned"', q.words_source === 'aligned');
  check('audio_segments preserved (ADR 0004)', Array.isArray(q.audio_segments) && q.audio_segments.length === 1);
}

// ── Case 4 — LATE-LANDING FINAL WORD (the truncation shape) ──────────────────
// The interpolated boundary said the quote ended at 3000ms, but the real final
// word "fracture." does not finish until 3900ms — beyond the old +500ms tail, so
// v2 dropped it from the captions entirely.
console.log('\nCase 4 — final word lands beyond the old +500ms tail');
{
  const q = runInject({
    quote: {
      id: 'q04', index: 4, topic: 'late-tail',
      text: "there's a fragility fracture.",
      transcript_start_ms: 2000, transcript_end_ms: 3000,
    },
    rawWords: [
      { word: "there's",   startMs: 2000, endMs: 2300 },
      { word: 'a',         startMs: 2300, endMs: 2400 },
      { word: 'fragility', startMs: 2400, endMs: 2900 },
      { word: 'fracture.', startMs: 3400, endMs: 3900 },  // ← past end+500
    ],
  });
  check('late final word IS captioned', tokens(q).includes('fracture.'),
    `got: ${JSON.stringify(tokens(q))}`);
  check('all 4 cleaned tokens present', tokens(q).length === 4, `got ${tokens(q).length}`);
}

// ── Case 5 — token fidelity + monotonic timing ───────────────────────────────
console.log('\nCase 5 — token order and timing sanity');
{
  const q = runInject({
    quote: {
      id: 'q05', index: 5, topic: 'order',
      text: 'one two three four five',
      transcript_start_ms: 0, transcript_end_ms: 1000,
    },
    rawWords: [
      { word: 'one',   startMs: 0,   endMs: 200 },
      { word: 'two',   startMs: 200, endMs: 400 },
      { word: 'three', startMs: 400, endMs: 600 },
      { word: 'four',  startMs: 600, endMs: 800 },
      { word: 'five',  startMs: 800, endMs: 1000 },
    ],
  });
  check('exact token order preserved',
    JSON.stringify(tokens(q)) === JSON.stringify(['one','two','three','four','five']),
    `got: ${JSON.stringify(tokens(q))}`);
  const w = q.words || [];
  check('spans are monotonic and non-degenerate',
    w.every((x, i) => x.end_ms > x.start_ms && (i === 0 || x.start_ms >= w[i - 1].start_ms)),
    JSON.stringify(w));
  check('words_source marked "per-clip-reconciled"', q.words_source === 'per-clip-reconciled',
    `got: ${q.words_source}`);
}

console.log(
  failures === 0
    ? '\n✅ inject_word_timestamps: all cases pass\n'
    : `\n❌ inject_word_timestamps: ${failures} check(s) failed\n`
);
process.exit(failures === 0 ? 0 : 1);
