"""
quote_tape.py — is every word of a quote on the tape, in the order said, by the speaker? The one tape check
(QUOTE-01 B, QUOTE-03 P0/P6, CLIP-01c).

ST-10: never edit a direct quote. The brand team may cut and may join passages from the same conversation (John,
2026-10-12: "joined passages are fine - thats actual editing, not inventing new words they didnt say"). It may never
add, replace or reorder a word, or add a bridging word to make a join read. This module proves that against the
transcript and places the quote while it does (its runs give CLIP-01c the quote's spoken length).

  place(quote_text, segments, ear_corrections=None, speaker=None) -> Placement
  Placement.verdict: "verbatim" | "listen" | "not_verbatim", with .reasons a person can read

How a quote is placed: from the first unplaced quote word, the longest stretch of the quote that appears word for word
on the tape is a RUN. Between consecutive runs the tape either CONTINUES (the next run starts within CUT_WORDS tape
words of the last: a filler cut), JOINS (a jump forward: another passage, or a longer cut), or goes BACK (a reorder).
- A run of fewer than JOIN_MIN_RUN words with no neighbour within NEAR_WORDS forward is STRANDED: a stock phrase ("you
  know what") found somewhere else and wedged in as a bridge. It counts as ADDED. (One taken from further on sends the
  next run back, which fails as a reorder.)
- Words that can't be placed are classified against the tape next to them: NORMALISED (the same words in another form,
  "don't" / "do not", "gonna" / "going to": restore the spoken form), MISHEAR (up to MISHEAR_MAX words whose letters
  nearly match: the fast transcript mishears), or REPLACED / ADDED.
- A mishear never changes polarity: a difference in not / n't / never / no, or un-, in-, dis- ... is a rewrite.

The verdict (Toby's three states, 2026-10-12):
  not_verbatim  anything ADDED, REPLACED, NORMALISED, REORDERED or STRANDED; a run in another speaker's turn (when
                `speaker` is given); or not found
  listen        otherwise, if there is a join, a mishear nobody has ruled on (not in ear_corrections), more than one
                diarised speaker under the quote, a negation the cuts left out (next to the quote too), or a quote
                under MIN_RUN words found whole: a person listens before it goes anywhere
  verbatim      cuts only, and every mishear ruled on

Run times are whole-segment bounds (the fast transcript has no word timings): segment precision. `spoken_ms` sums the
runs' spans, so a join across minutes isn't read as a minutes-long clip.
Known limits (named, not fixed: no word list can catch them; joins going to listen and Lisa's read are the check):
- a negation more than CUT_WORDS before the quote, or one that isn't a negation word ("the idea that cannabis cures
  anxiety is a myth" -> "cannabis cures anxiety" passes).

Normalisation folds case, punctuation, apostrophe and dash styles, thousands separators, "per cent", and number words
against digits. It never expands contractions. The fast transcript mishears: this is evidence for a gate and a person,
never the last word. The recording is.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field
from itertools import pairwise

MIN_RUN = 3  # words: a run this long may be placed anywhere on the tape
JOIN_MIN_RUN = 6  # words: a run with no continuing neighbour must be this long, or it's a stranded insertion
CUT_WORDS = 8  # tape words: a gap this small between runs is a filler cut; longer is a join (a person listens)
TAIL_WORDS = 1  # tape word read after the quote's end for a dropped negation ("…, not."); the lead-in reads CUT_WORDS
NEAR_WORDS = 60  # tape words: a short run with no neighbour this close (forward) came from elsewhere: stranded
MISHEAR_RATIO = 0.6
SHORT_WORD_RATIO = 0.8  # five letters or fewer on either side
MISHEAR_MAX = 4  # words: only a short difference can be a mishear; a longer one is a rewrite

_ONES = (
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen".split()
)
_TENS = "twenty thirty forty fifty sixty seventy eighty ninety".split()
_NUMBER_WORDS = {w: i for i, w in enumerate(_ONES)} | {w: 20 + 10 * i for i, w in enumerate(_TENS)}
_FORMS = {"gonna": "going to", "wanna": "want to", "gotta": "got to", "kinda": "kind of", "cause": "because"}
_NEGATIONS = {"not", "no", "never", "nothing", "none", "nor", "neither", "nobody", "nowhere", "cannot"}
_NEG_PREFIXES = ("un", "in", "im", "il", "ir", "dis", "non")


def words(text: str) -> list[str]:
    """Normalised words: lower case, straight apostrophes, no punctuation, numbers as digits, "%" as percent."""
    text = (text or "").lower().replace("’", "'").replace("‘", "'").replace("%", " percent ")
    text = re.sub(r"(\d),(\d{3})\b", r"\1\2", text)  # 1,000 -> 1000
    text = re.sub(r"\bper cent\b", "percent", text)
    text = re.sub(r"[—–-]", " ", text)
    raw = [w.strip("'") for w in re.findall(r"[a-z0-9']+", text)]
    return _fold_numbers([w for w in raw if w])


def _fold_numbers(ws: list[str]) -> list[str]:
    """'twenty five' -> '25', 'one hundred' -> '100', 'two thousand' -> '2000'; digits stay digits."""
    out: list[str] = []
    for w in ws:
        n = _NUMBER_WORDS.get(w)
        prev = int(out[-1]) if out and out[-1].isdigit() else None
        if w in ("hundred", "thousand"):
            mult = 100 if w == "hundred" else 1000
            if prev is not None and prev < mult:
                out[-1] = str(prev * mult)
            else:
                out.append(str(mult))
        elif n is not None:
            if prev is not None and 20 <= prev < 100 and prev % 10 == 0 and n < 10:
                out[-1] = str(prev + n)  # twenty five
            elif prev is not None and prev >= 100 and prev % 100 == 0 and n < 100:
                out[-1] = str(prev + n)  # one hundred twenty
            else:
                out.append(str(n))
        else:
            out.append(w)
    return out


@dataclass
class Run:
    quote_start: int  # index into the quote's words
    length: int
    tape_start: int  # index into the tape's words
    start_s: float  # segment precision
    end_s: float
    speakers: list[str]

    @property
    def tape_end(self) -> int:
        return self.tape_start + self.length


@dataclass
class Placement:
    quote_words: list[str]
    runs: list[Run] = field(default_factory=list)
    added: list[str] = field(default_factory=list)
    replaced: list[tuple[str, str, str]] = field(default_factory=list)  # (tape, quote, kind: rewrite|normalised)
    mishears: list[tuple[str, str, bool]] = field(default_factory=list)  # (tape, quote, ruled on by ear)
    stranded: list[str] = field(default_factory=list)  # short runs wedged in from elsewhere
    reordered: list[str] = field(default_factory=list)  # runs placed before the previous one
    off_speaker: list[tuple[str, str]] = field(default_factory=list)  # (quote words, the label they sit under)
    cut_negations: list[str] = field(default_factory=list)  # negations the cuts left out
    short_whole: bool = False  # a quote under MIN_RUN words, found whole

    def _links(self) -> list[str]:
        """How each run follows the one before: continues | join | back."""
        links = []
        for a, b in pairwise(self.runs):
            gap = b.tape_start - a.tape_end
            links.append("back" if gap < 0 else "continues" if gap <= CUT_WORDS else "join")
        return links

    @property
    def joins(self) -> int:
        return self._links().count("join")

    @property
    def speakers(self) -> list[str]:
        return sorted({s for r in self.runs for s in r.speakers})

    @property
    def mixed_speakers(self) -> bool:
        return len(self.speakers) > 1

    @property
    def found(self) -> bool:
        return bool(self.runs)

    @property
    def reasons(self) -> list[str]:
        """Why it isn't verbatim, failures first: what a gate shows Lisa and a person."""
        r = []
        if not self.found:
            r.append(f"not found on the tape (no run of {MIN_RUN}+ words)")
        r += [f'added: "{a}"' for a in self.added]
        r += [f'stranded (a phrase from elsewhere wedged in): "{s}"' for s in self.stranded]
        r += [
            f'{"normalised" if k == "normalised" else "replaced"}: tape "{t}" -> quote "{q}"'
            for t, q, k in self.replaced
        ]
        r += [f'reordered: "{x}" comes earlier on the tape' for x in self.reordered]
        r += [f'another speaker ({lab}): "{q}"' for q, lab in self.off_speaker]
        r += [f'a cut left out "{n}": listen that the meaning holds' for n in self.cut_negations]
        r += [f"under {MIN_RUN} words, found whole: listen that it's the right place"] if self.short_whole else []
        r += [f'mishear to rule on: tape "{t}" -> quote "{q}"' for t, q, ok in self.mishears if not ok]
        r += [f"{self.joins} join(s): listen that the passages read as said"] if self.joins else []
        r += [f"mixed speakers {self.speakers}: listen"] if self.mixed_speakers else []
        return r

    @property
    def verdict(self) -> str:
        if not self.found or self.added or self.stranded or self.replaced or self.reordered or self.off_speaker:
            return "not_verbatim"
        if (
            self.joins
            or self.mixed_speakers
            or self.cut_negations
            or self.short_whole
            or any(not ok for _, _, ok in self.mishears)
        ):
            return "listen"
        return "verbatim"

    @property
    def verbatim(self) -> bool:
        return self.verdict == "verbatim"

    @property
    def spoken_ms(self) -> int:
        """The audio the quote's runs come from, summed (segment precision; overlapping segments counted once)."""
        total, cur = 0.0, None
        for s, e in sorted({(r.start_s, r.end_s) for r in self.runs}):
            if cur and s <= cur[1]:
                cur = (cur[0], max(cur[1], e))
            else:
                total += (cur[1] - cur[0]) if cur else 0
                cur = (s, e)
        total += (cur[1] - cur[0]) if cur else 0
        return round(total * 1000)

    @property
    def start_ms(self) -> int:
        return round(min(r.start_s for r in self.runs) * 1000) if self.runs else 0

    @property
    def end_ms(self) -> int:
        return round(max(r.end_s for r in self.runs) * 1000) if self.runs else 0


class Tape:
    """The episode's words, with each word's segment time and speaker label (transcript_fast.json segments)."""

    def __init__(self, segments: list[dict]):
        self.words: list[str] = []
        self.times: list[tuple[float, float, str]] = []
        for s in segments:
            ws = words(s.get("text", ""))
            self.words += ws
            self.times += [(float(s.get("start", 0)), float(s.get("end", 0)), str(s.get("speaker", "?")))] * len(ws)
        self._at: dict[str, list[int]] = {}
        for i, w in enumerate(self.words):
            self._at.setdefault(w, []).append(i)

    def longest(self, q: list[str], i: int, near: int | None) -> tuple[int, int]:
        """(length, tape position) of the longest stretch of q[i:] on the tape. A tie goes forward from `near` (the
        previous run's end) first, then to the nearest: speakers repeat themselves, and the copy said next is the one
        the quote continues with (a repeat said earlier would read as a reorder)."""
        best, rank = (0, -1), None
        for p in self._at.get(q[i], []):
            n = 0
            while i + n < len(q) and p + n < len(self.words) and self.words[p + n] == q[i + n]:
                n += 1
            r = (0 if near is None or p >= near else 1, abs(p - near) if near is not None else 0)
            if n > best[0] or (n == best[0] and rank is not None and r < rank):
                best, rank = (n, p), r
        return best

    def run(self, qi: int, n: int, p: int) -> Run:
        span = self.times[p : p + n]
        return Run(qi, n, p, span[0][0], span[-1][1], sorted({t[2] for t in span}))


def _letters(ws: list[str]) -> str:
    return "".join(ws).replace("'", "")


def _negative(ws: list[str]) -> bool:
    return any(w in _NEGATIONS or w.endswith("n't") for w in ws)


def _flips(tape_w: list[str], quote_w: list[str]) -> bool:
    """A difference in polarity is never a mishear: never/ever, can/can't, safe/unsafe."""
    if _negative(tape_w) != _negative(quote_w):
        return True
    return any(t != q and any(t == p + q or q == p + t for p in _NEG_PREFIXES) for t in tape_w for q in quote_w)


def _needed(tape_w: list[str], quote_w: list[str]) -> float:
    """Short words share letters too easily ('death' / 'dexa' is 0.67): they need a closer match."""
    return SHORT_WORD_RATIO if min(len(_letters(tape_w)), len(_letters(quote_w))) <= 5 else MISHEAR_RATIO


def _best_slice(slices: list[list[str]], quote_w: list[str]) -> tuple[float, list[str]]:
    """Of the tape stretches next to the gap, the one that best matches the quote words by letters, and how well."""
    best = (0.0, slices[min(len(quote_w), len(slices)) - 1] if slices else [])
    for sl in slices[: len(quote_w) + 3]:
        r = difflib.SequenceMatcher(a=_letters(sl), b=_letters(quote_w), autojunk=False).ratio()
        if r > best[0]:
            best = (r, sl)
    return best


def _same_form(tape_w: list[str], quote_w: list[str]) -> bool:
    """The same words in another form: a contraction expanded or made, or 'gonna' / 'going to'."""

    def forms(ws):
        return " ".join(_FORMS.get(w, w) for w in ws).split()

    return tape_w != quote_w and (forms(tape_w) == forms(quote_w) or _same_letters(tape_w, quote_w))


def _same_letters(a: list[str], b: list[str]) -> bool:
    """'don't' vs 'do not', 'it's' vs 'it is': the same words in another form (the sheet's NORMALISED)."""

    def expand(ws):
        return re.sub(r"n't\b", " not", re.sub(r"'s\b", " is", re.sub(r"'re\b", " are", " ".join(ws))))

    def fix(s):
        return s.replace("wo not", "will not").replace("ca not", "can not").split()

    return fix(expand(a)) == fix(expand(b))


def place(
    quote_text: str,
    segments: list[dict] | Tape,
    ear_corrections: list[tuple[str, str]] | None = None,
    speaker: str | None = None,
) -> Placement:
    """Place a quote on the tape.
    ear_corrections: (tape words, quote words) pairs a person has ruled on by listening.
    speaker: the diarised label the quote's speaker has (e.g. "SPEAKER_01"); a run under another label fails."""
    tape = segments if isinstance(segments, Tape) else Tape(segments)
    q = words(quote_text)
    ear = {(" ".join(words(a)), " ".join(words(b))) for a, b in (ear_corrections or [])}
    out = Placement(q)
    gaps: list[tuple[list[int], int | None, int | None]] = []  # (quote indices, prev run's tape end, next run's start)
    i, prev_end, pending = 0, None, []

    while i < len(q):
        n, p = tape.longest(q, i, prev_end)
        continues = prev_end is not None and 0 <= p - prev_end <= CUT_WORDS
        whole = n == len(q)  # a quote under MIN_RUN words, found whole
        if n >= MIN_RUN or (n >= 1 and continues) or whole:
            if pending:
                gaps.append((pending, prev_end, p))
                pending = []
            out.runs.append(tape.run(i, n, p))
            i, prev_end = i + n, p + n
        else:
            pending.append(i)
            i += 1
    if pending:
        gaps.append((pending, prev_end, None))

    _strand(out, q)
    for idx, before, after in gaps:
        _classify(out, tape, [q[k] for k in idx], before, after, ear)
    for a, b in pairwise(out.runs):
        if b.tape_start < a.tape_end:
            out.reordered.append(" ".join(q[b.quote_start : b.quote_start + b.length]))
    _cut_negations(out, tape)
    out.short_whole = len(out.runs) == 1 and out.runs[0].length == len(q) < MIN_RUN
    if speaker:
        for r in out.runs:
            others = [s for s in r.speakers if s != speaker]
            if others and (r.length >= MIN_RUN or out.short_whole):
                out.off_speaker.append((" ".join(q[r.quote_start : r.quote_start + r.length]), ", ".join(others)))
    return out


def _cut_negations(out: Placement, tape: Tape) -> None:
    """A cut is allowed, but one that drops a negation can invert the meaning ("cannabis is [not] a cure"; "[it is not
    true that] cannabis cures anxiety"): the words cut between continuing runs, and the words just before the first
    run and after the last in the same segment, are read for not / n't / never / no (Toby, 2026-10-12)."""
    if not out.runs:
        return
    cut: list[str] = []
    for a, b in pairwise(out.runs):
        if 0 <= b.tape_start - a.tape_end <= CUT_WORDS:
            cut += tape.words[a.tape_end : b.tape_start]
    first, last = out.runs[0], out.runs[-1]

    # The same speaker, not the same segment: the fast transcript splits one sentence across segments, so "People
    # think it is not true that | cannabis cures anxiety" has its "not" in the segment before (Toby, limit 1).
    def who(k):
        return tape.times[k][2]

    lead = [k for k in range(max(0, first.tape_start - CUT_WORDS), first.tape_start) if who(k) == who(first.tape_start)]
    # After the quote only a word tacked straight on can invert it ("…, not."); further on is the next clause.
    tail = [
        k
        for k in range(last.tape_end, min(len(tape.words), last.tape_end + TAIL_WORDS))
        if who(k) == who(last.tape_end - 1)
    ]
    cut += [tape.words[k] for k in lead + tail]
    out.cut_negations = sorted({w for w in cut if _negative([w])})


def _strand(out: Placement, q: list[str]) -> None:
    """A short run with no neighbour near it on the tape is a phrase from elsewhere: ADDED, not a join."""
    keep = []
    for k, r in enumerate(out.runs):
        prev = out.runs[k - 1] if k else None
        nxt = out.runs[k + 1] if k + 1 < len(out.runs) else None
        linked = (prev is not None and 0 <= r.tape_start - prev.tape_end <= NEAR_WORDS) or (
            nxt is not None and 0 <= nxt.tape_start - r.tape_end <= NEAR_WORDS
        )
        if r.length < JOIN_MIN_RUN and not linked and len(out.runs) > 1:
            out.stranded.append(" ".join(q[r.quote_start : r.quote_start + r.length]))
        else:
            keep.append(r)
    out.runs = keep


def _classify(
    out: Placement, tape: Tape, quote_w: list[str], before: int | None, after: int | None, ear: set[tuple[str, str]]
) -> None:
    """Unplaced quote words, read against the tape next to them."""
    reach = len(quote_w) + 3
    if before is not None:  # read forward from the previous run
        stop = after if after is not None and before <= after <= before + reach + CUT_WORDS else before + reach
        near = tape.words[before:stop]
        slices = [near[:n] for n in range(1, len(near) + 1)]
    elif after is not None:  # the quote's first words: read back from the first run
        near = tape.words[max(0, after - reach) : after]
        slices = [near[-n:] for n in range(1, len(near) + 1)]
    else:
        near, slices = [], []
    qs = " ".join(quote_w)
    normal = next((sl for sl in slices[: len(quote_w) + 2] if _same_form(sl, quote_w)), None)
    ratio, best = _best_slice(slices, quote_w)
    ts = " ".join(best)
    if normal is not None:
        out.replaced.append((" ".join(normal), qs, "normalised"))
    elif near and (ts, qs) in ear:
        out.mishears.append((ts, qs, True))
    elif near and len(quote_w) <= MISHEAR_MAX and ratio >= _needed(best, quote_w) and not _flips(best, quote_w):
        out.mishears.append((ts, qs, False))
    elif near:
        out.replaced.append((" ".join(near[: max(len(quote_w) + 3, 12)]), qs, "rewrite"))
    else:
        out.added.append(qs)


def report(quote_id: str, speaker: str, p: Placement) -> list[str]:
    """Lines for a person: the verdict and its reasons."""
    label = (
        {"verbatim": "VERBATIM (cuts only)", "listen": "LISTEN", "not_verbatim": "NOT VERBATIM"}[p.verdict]
        if p.found
        else "NOT FOUND ON THE TAPE: listen for it, or it is constructed"
    )
    head = f"== {quote_id} {speaker}"
    if p.found:
        head += (
            f" [{p.start_ms / 1000:.1f}-{p.end_ms / 1000:.1f}s, spoken {p.spoken_ms / 1000:.0f}s] runs {len(p.runs)}, "
            f"joins {p.joins}, tape speakers {p.speakers}"
        )
    return (
        [f"{head}: {label}"]
        + [f"   {r}" for r in p.reasons]
        + [f'   mishear (ruled on by ear): tape "{t}" -> quote "{qq}"' for t, qq, ok in p.mishears if ok]
    )
