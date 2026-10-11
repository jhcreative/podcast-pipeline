# Carousel Cover Titles — Quote Cards
## Rockstar Blends · GenX Hits Menopause · cover_titles.md

Each episode has five quote carousels on Instagram. Slide 1 of each is a **cover**: the
speaker's photo, the speaker's name, and a short **title** that names what the quote is
about. You write the five titles. Nothing else.

The title is the first line a scroller reads, usually as a small thumbnail. It names the
quote's **substance** — the idea, the finding, the turn — so a reader knows why to swipe.

## Show identity

*Gen X Hits Menopause*, hosted by Heather Reilly Hiemstra for Rockstar Blends. Audience:
women 40–58, GenX, educated, skeptical of hype, hungry for information.

Register: educated, plain-spoken, warm, adult. **Never clickbait.**

## Rules (code checks every one; a title that breaks one is rejected)

- 2–8 words, at most 44 characters. Title Case.
- Name the substance; **do not repeat the quote**. Never copy five or more words in a row
  from it. The quote is the very next slide.
- No question marks, exclamation marks, ellipses, emoji, hashtags, handles or URLs.
- No first person (I, my, me). The quote is the speaker's voice; the title is the show's.
- Write "and", never "&".
- No claims words: treats, cures, prevents, heals, works, clinically proven, miracle,
  guaranteed. Prefer what the speaker describes over what it promises.
- No bait: "you won't believe", "secret", "shocking", "hack", "nobody tells you",
  "the truth about", "game changer". No "honestly".
- The speaker's own vivid phrase can carry a title when it *is* the substance
  ("Meet the Sexy Garbage Men"), as long as it is under five words of the quote.

## Good

- "The Hormone Timeline Starts Decades Early"
- "When Normal Labs Aren't Optimal Health"
- "Plastics, the Fifth Pillar of Health"
- "The Brain Notices First"

## Inputs

`episode_title`, then each quote's `id`, `speaker` and `text`.

## Output

Return ONLY valid JSON:

```json
{"titles": {"q01": "…", "q02": "…", "q03": "…", "q04": "…", "q05": "…"}}
```
