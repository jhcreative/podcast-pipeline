# IBM Plex (vendored for CARD-05)

SIL Open Font License 1.1 (LICENSE.txt). Self-hosted: cards load these as @font-face data URIs and never call Google Fonts.
The reels draw their captions and opener with `IBMPlexSans-SemiBold.ttf` (render_reel.js, ffmpeg drawtext; 2026-10-12).

Source: npm @ibm/plex-sans@1.1.0 and @ibm/plex-sans-condensed@1.1.0 (both published 2024-11-11), fonts/complete/woff2/, fetched 2026-10-08 via cdn.jsdelivr.net.

```
fa7130d854a660b39a7fc9e6e0f2dc23dba5f1346e2adea3e1fe37b6d884133d  IBMPlexSans-Bold.woff2
5660f8a658f8bb50dbc005232f885eadffd2bc1c235c4f6fbb63469d1f9cde6d  IBMPlexSans-Medium.woff2
ba711a3085ff9f27440b6b9c4550cfc47c97bf36591d5da958b975bb3add8c1a  IBMPlexSans-Regular.woff2
f78048030eab62e860efa39a0df79e2e5581bf122eb95b9bc42c0b8a4988d205  IBMPlexSans-SemiBold.woff2
7ec435d503813d866db75da8fcc8fb4b1c1ce853475578e7a409c50bf9ab0516  IBMPlexSansCondensed-Bold.woff2
ec28b4f7f62878e81f86936dee2c49dd688bf0a654469e088d5cb758ee1fbbff  IBMPlexSansCondensed-SemiBold.woff2
```

`IBMPlexSans-SemiBold.ttf` is `IBMPlexSans-SemiBold.woff2` above, decompressed (WOFF2 is a lossless wrapper; the npm
release ships no TTF): fontTools 4.63.0, `TTFont(woff2); flavor = None; save(ttf)`, 2026-10-12. ffmpeg's drawtext reads
TTF in every build, WOFF2 only when FreeType has brotli.

```
8efcbb87bffa77e7dd5b3a0dbddb3396080b8fca8678a748bbdff2fc41589d47  IBMPlexSans-SemiBold.ttf
```
