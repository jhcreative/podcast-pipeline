#!/usr/bin/env node

/**
 * test_reel_selection.js — the reel_candidate selection contract (REEL-06).
 * v1.0 · 2026-07-21
 *
 * The Brand Team marks each quote with `reel_candidate`. Until 2026-07-21 that verdict
 * was written to quotes.json, shipped to Drive, and read by NOTHING — render_reel.js
 * rendered `quotesData.quotes` unfiltered. E28 q05 was set false, with Lisa's reasoning
 * recorded in lisa_note, and was published as a standalone Reel anyway.
 *
 * These cases lock the selection rule so the editor's verdict can't be silently
 * discarded again:
 *
 *   1. explicit false            → excluded from an --reel all run
 *   2. field absent              → rendered (back-compat with pre-field episodes)
 *   3. explicit true             → rendered
 *   4. explicit --reel NN        → renders even when excluded, with a warning
 *      (a human naming one reel is overriding on purpose)
 *   5. exclusions are REPORTED   → the skip is logged with its index, never silent
 *   6. below minReelCount        → warns on the post-filter count
 *
 * Runs render_reel.js as a subprocess in --dry-run, so no ffmpeg, no audio, no assets.
 *
 * Usage:   node test_reel_selection.js
 * Exit:    0 = all cases pass · 1 = one or more failed
 */

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const RENDER = path.join(HERE, 'render_reel.js');

let failures = 0;
function check(name, cond, detail) {
  if (cond) console.log(`  ✓ ${name}`);
  else { failures++; console.log(`  ✗ ${name}${detail ? `\n      ${detail}` : ''}`); }
}

function quote(index, candidate, extra = {}) {
  const q = {
    id: `q0${index}`,
    index,
    topic: `topic ${index}`,
    topic_slug: `topic-${index}`,
    text: `Sentence number ${index} for the reel.`,
    quote_text_clean: `Sentence number ${index} for the reel.`,
    speaker: 'Guest Person',
    speaker_role: 'guest',
    transcript_start_ms: index * 10000,
    transcript_end_ms: index * 10000 + 8000,
    ...extra,
  };
  if (candidate !== undefined) q.reel_candidate = candidate;
  return q;
}

/** Run render_reel.js --dry-run against a fixture; return combined output. */
function run(quotes, reelArg) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'reelsel-'));
  const quotesPath = path.join(dir, 'quotes.json');
  fs.writeFileSync(quotesPath, JSON.stringify({
    episode: { number: 99, title: 'Test Episode | Selection', slug: 'test' },
    quotes,
  }, null, 2));

  // --dry-run validates and logs without invoking ffmpeg, so these paths need only exist.
  const audio = path.join(dir, 'audio.m4a'); fs.writeFileSync(audio, '');
  const bg = path.join(dir, 'bg.jpg'); fs.writeFileSync(bg, '');

  const args = [RENDER, '--quotes-json', quotesPath, '--audio', audio,
                '--background', bg, '--output-dir', path.join(dir, 'out'),
                '--dry-run', '--verbose'];
  if (reelArg) args.push('--reel', reelArg);

  const r = spawnSync('node', args, { encoding: 'utf8' });
  fs.rmSync(dir, { recursive: true, force: true });
  return (r.stdout || '') + (r.stderr || '');
}

const rendered = (out, idx) =>
  new RegExp(`Rendering reel_${String(idx).padStart(2, '0')}\\b`).test(out);

// ── Case 1 — explicit false is excluded (E28 q05 / E30 q05 shape) ────────────
console.log('\nCase 1 — reel_candidate: false is excluded from an --reel all run');
{
  const out = run([
    quote(1, true), quote(2, true), quote(3, true), quote(4, true),
    quote(5, false, { lisa_note: 'needs visual support; better in blog than standalone' }),
  ]);
  check('reel_05 NOT rendered', !rendered(out, 5));
  check('reels 01–04 all rendered', [1, 2, 3, 4].every(i => rendered(out, i)));
  check('exclusion is reported, not silent', /Skipping reel_05/.test(out),
    out.split('\n').filter(l => /reel_05/.test(l)).join(' | ') || '(no reel_05 line at all)');
  check("editor's reasoning surfaced in verbose", /needs visual support/.test(out));
}

// ── Case 2 — absent field renders (back-compat) ──────────────────────────────
console.log('\nCase 2 — quotes predating the field are unaffected');
{
  const out = run([quote(1), quote(2), quote(3), quote(4)]);
  check('all 4 rendered when reel_candidate is absent',
    [1, 2, 3, 4].every(i => rendered(out, i)));
  check('nothing reported as skipped', !/Skipping reel_/.test(out));
}

// ── Case 3 — explicit --reel NN overrides the flag ───────────────────────────
console.log('\nCase 3 — an explicitly named reel renders even if excluded');
{
  const out = run([quote(1, true), quote(2, true), quote(3, true), quote(5, false)], '05');
  check('reel_05 IS rendered when named explicitly', rendered(out, 5));
  check('the override is warned about', /reel_candidate: false — rendering anyway/.test(out));
}

// ── Case 4 — post-filter count is checked against minReelCount ───────────────
console.log('\nCase 4 — falling below minReelCount after filtering is reported');
{
  const out = run([quote(1, true), quote(2, true), quote(3, false), quote(4, false), quote(5, false)]);
  check('only the 2 candidates rendered', rendered(out, 1) && rendered(out, 2)
    && ![3, 4, 5].some(i => rendered(out, i)));
  check('short-on-reels warning emitted', /after applying reel_candidate/.test(out),
    out.split('\n').filter(l => /minimum|short on reels/.test(l)).join(' | ') || '(no warning)');
}

console.log(
  failures === 0
    ? '\n✅ reel selection: all cases pass\n'
    : `\n❌ reel selection: ${failures} check(s) failed\n`
);
process.exit(failures === 0 ? 0 : 1);
