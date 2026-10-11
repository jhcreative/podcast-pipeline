// REEL-07 — the test that would have caught the boundary double-paint.
//
// Run:  node --test scripts/render_reel.test.mjs
//
// `buildAccumulatingWordPages` deliberately snaps each entry's endSec to the NEXT entry's
// startSec, so consecutive states SHARE an instant. That is correct — it is what kills the
// blank-frame blink. What must not happen is two states being ENABLED at that shared instant:
// accumulating captions make state i a strict prefix of i+1, so both drawing means the shorter
// text paints offset over the longer one. One frame per boundary, invisible by eye, in every
// published reel until 2026-09-21.
//
// The sweep below is the point: assert at real frame rates across every boundary, not at
// hand-picked times.

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildAccumulatingWordPages, buildDrawtextFilter } from './render_reel.js';

// A sentence long enough to cross a line break and a page wipe at these budgets.
const WORDS = [
  ['So', 0], ['here', 260], ['is', 520], ['the', 700], ['thing', 900],
  ['nobody', 1300], ['tells', 1750], ['you', 2050], ['about', 2260], ['starting', 2600],
  ['a', 3100], ['band', 3220], ['in', 3600], ['your', 3750], ['thirties', 3980],
].map(([word, start], i, a) => ({
  word,
  start_ms: 10_000 + start,
  end_ms: 10_000 + (i + 1 < a.length ? a[i + 1][1] : start + 400),
}));

const CHARS_PER_LINE = 22;
const LINES_PER_PAGE = 2;
const CLIP_START_MS = 10_000;

function pages() {
  return buildAccumulatingWordPages(WORDS, CLIP_START_MS, CHARS_PER_LINE, LINES_PER_PAGE);
}

/** Parse the enable= windows back out of the generated filter string, one per drawtext. */
function windowsFromFilter(filter) {
  const re = /enable=gte\(t\\,([\d.]+)\)\*(lt|lte)\(t\\,([\d.]+)\)/g;
  const out = [];
  let m;
  while ((m = re.exec(filter)) !== null) {
    out.push({ start: Number(m[1]), cmp: m[2], end: Number(m[3]) });
  }
  return out;
}

function filterString() {
  return buildDrawtextFilter(pages(), '/dev/null', {}, '/tmp', 0, /* dryRun */ true);
}

test('the snap is real: consecutive states share an instant', () => {
  const p = pages();
  assert.ok(p.length > 3, 'need several states to test against');
  let shared = 0;
  for (let i = 0; i < p.length - 1; i++) {
    if (Math.abs(p[i].endSec - p[i + 1].startSec) < 1e-9) shared++;
  }
  assert.ok(shared > 0, 'expected the gapless snap to produce shared boundaries');
});

test('the end bound is exclusive', () => {
  const windows = windowsFromFilter(filterString());
  assert.ok(windows.length > 0, 'no enable= windows parsed out of the filter');
  for (const w of windows) {
    assert.equal(w.cmp, 'lt', `end bound must be exclusive, got ${w.cmp} on [${w.start}, ${w.end}]`);
  }
});

for (const fps of [24, 25, 30, 50, 60]) {
  test(`no instant enables two states — swept at ${fps}fps`, () => {
    const windows = windowsFromFilter(filterString());
    const enabled = (t) =>
      windows.filter((w) => t >= w.start && (w.cmp === 'lt' ? t < w.end : t <= w.end));

    const last = Math.max(...windows.map((w) => w.end));
    const frames = Math.ceil(last * fps) + 2;
    for (let f = 0; f <= frames; f++) {
      // ffmpeg compares the filter's own truncated 3dp values against frame time; match that.
      const t = Number((f / fps).toFixed(3));
      const on = enabled(t);
      // More than one line of ONE page is legitimate (a page has linesPerPage drawtexts).
      // Two different WINDOWS overlapping is the defect.
      const distinct = new Set(on.map((w) => `${w.start}|${w.end}`));
      assert.ok(
        distinct.size <= 1,
        `t=${t}s enables ${distinct.size} distinct states: ${[...distinct].join(' , ')}`,
      );
    }
  });
}

test('every boundary instant itself is clean', () => {
  const windows = windowsFromFilter(filterString());
  const bounds = [...new Set(windows.flatMap((w) => [w.start, w.end]))].sort((a, b) => a - b);
  for (const t of bounds) {
    const on = windows.filter((w) => t >= w.start && (w.cmp === 'lt' ? t < w.end : t <= w.end));
    const distinct = new Set(on.map((w) => `${w.start}|${w.end}`));
    assert.ok(distinct.size <= 1, `boundary t=${t}s enables ${distinct.size} distinct states`);
  }
});
