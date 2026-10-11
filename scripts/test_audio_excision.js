#!/usr/bin/env node

/**
 * test_audio_excision.js — locks the mid-quote audio-excision logic (ADR 0004).
 * v1.0 · 2026-06-23 (Jim, Jen co-author)
 *
 * ADR 0004 removes LARGE cleaned-out speech spans (a host interjection, a dropped
 * clause) from the clip audio and re-stitches click-free, then remaps the caption
 * words[] onto the post-excision continuous local timeline so captions stay synced
 * and the big freeze-holds vanish. The intelligence lives in
 * lookup_quote_timestamps.js (findExciseSpans / buildAudioSegments / remapWordsToLocal);
 * render_reel.js stays dumb and concatenates the kept segments.
 *
 * These fixtures are tiny hand-built timelines and word lists so every assertion is
 * exact and deterministic — no audio, no WhisperX, no network. Same loud PASS/FAIL +
 * exit-code convention as test_clip_boundaries.js / test_caption_reconcile.js.
 *
 * The contract (the shapes ADR 0004 names):
 *   1. SPAN DETECTION — a contiguous run of cleaned-out (LCS-unmatched) raw words
 *      whose duration ≥ EXCISE_MIN_MS is detected; small fillers below it are NOT.
 *   2. THRESHOLD — a run just under the threshold is kept; just over is excised.
 *   3. SEGMENTS — buildAudioSegments returns the complement of the cut within the
 *      padded window; lead-in/tail attach to the first/last kept segment.
 *   4. REMAP MATH — a word AFTER an excision shifts earlier by EXACTLY the excised
 *      duration that precedes it; a word before it is unchanged (minus clip start).
 *   5. BACK-COMPAT — no excision → exactly one segment == the whole window, and the
 *      remap is the identity shift (word_ms - clipStart), so render's single-cut path
 *      is byte-for-byte the pre-ADR-0004 behavior.
 *
 * Usage:  node test_audio_excision.js     Exit: 0 all pass · 1 any fail
 */

import {
  buildTimelineFromWords, findTimestamps,
  findExciseSpans, buildAudioSegments, originalMsToLocalMs, remapWordsToLocal,
  reconcileCleanedWords, EXCISE_MIN_MS,
} from './lookup_quote_timestamps.js';

let failures = 0;
function check(name, cond, detail) {
  console.log(`[excise] ${cond ? 'PASS' : 'FAIL'}  ${name}`);
  if (!cond) { failures++; if (detail) console.error(`[excise]   ✗ ${detail}`); }
}

// ── Case 1: SPAN DETECTION — large cleaned-out run found, small filler kept ────
{
  // Spoken: "we are not old <13s cleaned-out clause> we are not too old" — the
  // cleaned text keeps only the open + close, dropping the middle clause entirely.
  const raw = [
    { word: 'we',  start: 0.0, end: 0.2 }, { word: 'are', start: 0.2, end: 0.4 },
    { word: 'not', start: 0.4, end: 0.6 }, { word: 'old', start: 0.6, end: 0.9 },
    // 13s cleaned-out clause (one contiguous unmatched run):
    { word: 'and',   start: 1.0,  end: 1.3 },  { word: 'i',      start: 1.3,  end: 1.5 },
    { word: 'began', start: 1.5,  end: 1.9 },  { word: 'focusing', start: 1.9, end: 14.0 },
    // the true closing — note a tiny 0.4s "um" filler sits BETWEEN matched closing
    // words ("we ... um ... are"), so it is its own short unmatched run, NOT part of
    // the big clause, and must stay (below threshold):
    { word: 'we',  start: 15.0, end: 15.2 },
    { word: 'um',  start: 15.2, end: 15.6 },   // 0.4s filler between matched words
    { word: 'are', start: 15.6, end: 15.8 }, { word: 'not', start: 15.8, end: 16.0 },
    { word: 'too', start: 16.0, end: 16.2 }, { word: 'old', start: 16.2, end: 16.5 },
  ];
  const tl = buildTimelineFromWords(raw);
  const cleaned = 'We are not old. We are not too old.';
  const spans = findExciseSpans(cleaned, tl, 0, tl.length - 1);
  check('detect: exactly one excision span found (the big clause)', spans.length === 1,
    `got ${spans.length}: ${JSON.stringify(spans)}`);
  check('detect: the span is the cleaned-out clause (~13s, ≥ threshold)',
    spans[0] && spans[0].dur_ms >= EXCISE_MIN_MS && spans[0].dur_ms > 12000,
    spans[0] && `dur=${spans[0].dur_ms}`);
  check('detect: the small "um" filler is NOT excised (below threshold)',
    !spans.some(s => s.text.includes('um')));
  check('detect: span text names the dropped clause words',
    spans[0] && spans[0].text.includes('began') && spans[0].text.includes('focusing'));
}

// ── Case 2: THRESHOLD — just-under kept, just-over excised ────────────────────
{
  // A single unmatched run we can size precisely. "keep <run> end".
  function spanFor(runDurSec) {
    const raw = [
      { word: 'keep', start: 0.0, end: 0.5 },
      { word: 'gap',  start: 1.0, end: 1.0 + runDurSec }, // the unmatched run (one word)
      { word: 'end',  start: 1.0 + runDurSec + 0.2, end: 1.0 + runDurSec + 0.7 },
    ];
    const tl = buildTimelineFromWords(raw);
    return findExciseSpans('keep end', tl, 0, tl.length - 1);
  }
  // EXCISE_MIN_MS is 2000ms. A 1.9s run is kept; a 2.1s run is cut.
  const under = spanFor(1.9);
  const over  = spanFor(2.1);
  check(`threshold: run just UNDER ${EXCISE_MIN_MS}ms is kept (no span)`, under.length === 0,
    `got ${JSON.stringify(under)}`);
  check(`threshold: run just OVER ${EXCISE_MIN_MS}ms is excised (one span)`, over.length === 1,
    `got ${JSON.stringify(over)}`);
}

// ── Case 3: SEGMENTS — complement within the padded window ────────────────────
{
  // Window [1000, 11000] (lead-in already baked into 1000; tail into 11000).
  // One excision [4000, 7000] → kept [1000,4000] and [7000,11000].
  const segments = buildAudioSegments(1000, 11000, [{ start_ms: 4000, end_ms: 7000, dur_ms: 3000 }]);
  check('segments: two kept segments around one cut', segments.length === 2);
  check('segments: first kept segment starts at window start (lead-in preserved)',
    segments[0].start_ms === 1000 && segments[0].end_ms === 4000,
    JSON.stringify(segments[0]));
  check('segments: last kept segment ends at window end (tail preserved)',
    segments[1].start_ms === 7000 && segments[1].end_ms === 11000,
    JSON.stringify(segments[1]));
  const kept = segments.reduce((s, x) => s + (x.end_ms - x.start_ms), 0);
  check('segments: kept total = window - cut (10000 - 3000 = 7000ms)', kept === 7000, `kept=${kept}`);

  // Adjacent/overlapping cuts merge, and a cut touching the window edge clips.
  const merged = buildAudioSegments(0, 10000, [
    { start_ms: 2000, end_ms: 4000 }, { start_ms: 4000, end_ms: 5000 }, // touching → one hole
  ]);
  check('segments: touching cuts merge into a single hole (2 kept segments)', merged.length === 2,
    JSON.stringify(merged));
}

// ── Case 4: REMAP MATH — a word after an excision shifts by EXACTLY the cut ────
{
  // Kept segments [1000,4000] and [7000,11000] (cut of 3000ms at [4000,7000]).
  const segments = [{ start_ms: 1000, end_ms: 4000 }, { start_ms: 7000, end_ms: 11000 }];
  // originalMsToLocalMs maps an original ms to clip-local ms (0 = clip start = 1000).
  check('remap: clip start maps to local 0', originalMsToLocalMs(1000, segments) === 0);
  check('remap: a word INSIDE the first segment shifts by clipStart only (2000→1000)',
    originalMsToLocalMs(2000, segments) === 1000);
  check('remap: the cut boundary (4000) maps to the end of segment 1 (3000)',
    originalMsToLocalMs(4000, segments) === 3000);
  // A word at 8000 is 1000ms into segment 2; its local time is seg1 length (3000) + 1000.
  check('remap: a word AFTER the cut shifts earlier by EXACTLY the excised 3000ms',
    originalMsToLocalMs(8000, segments) === 4000,
    `8000 original = 1000 into seg2; seg1=3000 → local 4000; got ${originalMsToLocalMs(8000, segments)}`);
  // The shift is exactly the cut: original 8000 would be (8000-1000)=7000 local with NO
  // excision; with the 3000 cut before it, local = 7000 - 3000 = 4000.
  const noExcisionLocal = 8000 - 1000;       // 7000
  const withExcisionLocal = originalMsToLocalMs(8000, segments); // 4000
  check('remap: shift equals the excised duration before the word (7000 - 4000 = 3000)',
    noExcisionLocal - withExcisionLocal === 3000);

  // remapWordsToLocal applied to a words[] stream: word before cut unchanged-but-shifted,
  // word after cut shifted by the cut.
  const words = [
    { word: 'before', start_ms: 2000, end_ms: 2400 }, // in seg1
    { word: 'after',  start_ms: 8000, end_ms: 8400 }, // in seg2
  ];
  const local = remapWordsToLocal(words, segments);
  check('remap(words): "before" → local 1000', local[0].start_ms === 1000);
  check('remap(words): "after" → local 4000 (shifted by the 3000ms cut)', local[1].start_ms === 4000);
  check('remap(words): the gap between them collapsed from 6000ms to 3000ms (the cut)',
    (local[1].start_ms - local[0].start_ms) === 3000);
  check('remap(words): word strings unchanged', local.map(w => w.word).join(' ') === 'before after');
}

// ── Case 5: BACK-COMPAT — no excision → identity ──────────────────────────────
{
  // A quote with only small fillers: no run ≥ threshold.
  const raw = [
    { word: 'i',   start: 0.0, end: 0.2 }, { word: 'am',  start: 0.2, end: 0.5 },
    { word: 'um',  start: 0.5, end: 0.9 },  // 0.4s filler — below threshold
    { word: 'too', start: 0.9, end: 1.2 }, { word: 'old', start: 1.2, end: 1.6 },
  ];
  const tl = buildTimelineFromWords(raw);
  const cleaned = 'I am too old.';
  const spans = findExciseSpans(cleaned, tl, 0, tl.length - 1);
  check('back-compat: no excision spans on a small-filler-only quote', spans.length === 0,
    JSON.stringify(spans));

  // buildAudioSegments with no cuts → exactly the whole window (single segment).
  const segments = buildAudioSegments(500, 2100, spans);
  check('back-compat: one segment == the whole window', segments.length === 1 &&
    segments[0].start_ms === 500 && segments[0].end_ms === 2100, JSON.stringify(segments));

  // remapWordsToLocal over a single full-window segment is the identity shift
  // (word_ms - clipStart) — the same coordinate the single-cut render path uses.
  const single = [{ start_ms: 500, end_ms: 2100 }];
  const words = [{ word: 'old', start_ms: 1200, end_ms: 1600 }];
  const local = remapWordsToLocal(words, single);
  check('back-compat: single-segment remap is the identity shift (1200 - 500 = 700)',
    local[0].start_ms === 700, `got ${local[0].start_ms}`);
}

// ── Case 6: INTEGRATION — full lookup pipeline on a realistic q02-shaped fixture ─
{
  // Open thought, a long host-interjection-shaped cleaned-out run, then the close.
  const raw = [
    { word: 'I',     start: 10.0, end: 10.2 }, { word: 'came',  start: 10.2, end: 10.6 },
    { word: 'through',start: 10.6, end: 11.0 }, { word: 'lighter', start: 11.0, end: 11.6 },
    // 6s host interjection (cleaned out):
    { word: 'and',   start: 12.0, end: 12.3 }, { word: 'heather', start: 12.3, end: 12.9 },
    { word: 'were',  start: 12.9, end: 13.3 }, { word: 'so',    start: 13.3, end: 13.6 },
    { word: 'young', start: 13.6, end: 18.0 },
    // the close:
    { word: 'we',    start: 18.5, end: 18.7 }, { word: 'start', start: 18.7, end: 19.1 },
    { word: 'over',  start: 19.1, end: 19.6 },
  ];
  const tl = buildTimelineFromWords(raw);
  const cleaned = 'I came through lighter. We start over.';
  const r = findTimestamps(cleaned, tl);
  const spans = findExciseSpans(cleaned, tl, r.start_idx, r.end_idx);
  check('integration: one excision (the host interjection) detected', spans.length === 1,
    JSON.stringify(spans));
  // Build segments over a padded window and remap the captions.
  const clipStart = Math.max(0, r.start_ms - 200);
  const clipEnd   = r.end_ms + 500;
  const segments  = buildAudioSegments(clipStart, clipEnd, spans);
  check('integration: two kept audio segments', segments.length === 2);
  const reconciled = reconcileCleanedWords(cleaned, tl, r.start_idx, r.end_idx);
  const local = remapWordsToLocal(reconciled, segments);
  // The last caption word must land inside the SHORTER clip (kept-total), not the
  // original window — proof the big hold collapsed.
  const keptTotal = segments.reduce((s, x) => s + (x.end_ms - x.start_ms), 0);
  const lastEnd = local[local.length - 1].end_ms;
  check('integration: last caption word fits inside the shortened clip', lastEnd <= keptTotal + 50,
    `lastEnd=${lastEnd} keptTotal=${keptTotal}`);
  let mono = true; for (let i = 1; i < local.length; i++) if (local[i].start_ms < local[i - 1].start_ms) mono = false;
  check('integration: remapped captions monotonic', mono);
  check('integration: no interjection word ("young"/"heather") leaked into captions',
    !local.map(w => w.word.toLowerCase()).join(' ').match(/young|heather/));
}

console.log(`[excise] ${failures === 0 ? 'done — all checks passed' : failures + ' check(s) FAILED'}`);
process.exit(failures === 0 ? 0 : 1);
