# renderer

Draws a build folder as a narrated update video. It is the renderer of the
video channel of unfloat os (Node and [Remotion](https://www.remotion.dev)
4.0.544). It knows nothing about records, series, data classes or any project:
`manifest.json` and the audio and media files next to it are its only input.

## Command line

Run from this folder, after `npm ci`:

```
node bin/render.mjs --build <build> --out <file.mp4> [--quality draft|final] [--scene <id>]
node bin/render.mjs --build <build> --stills <folder>     # <folder>/<scene id>.png, a frame at two thirds of each scene
node bin/render.mjs --build <build> --poster <file.png>   # a frame of the first scene
```

The options combine (`--out`, `--stills` and `--poster` in one call render the
browser once). Exit code 0 on success; on failure one line on stderr that starts
with `error:`. A manifest that does not validate is refused with the path of the
bad field (`error: manifest.json: scenes[3].props.rows: Too big: ...`).

- Output: H.264, yuv420p, AAC 48 kHz, `+faststart`, 1920x1080 at 30 fps. `draft`
  renders at half resolution with a higher CRF. `--scene` renders one scene.
- The video is as long as the sum of the scene lengths, each rounded to frames.
- The bundle is written to `<build>/bundle` and every temporary file goes under
  `<build>/tmp` (the script sets `TMPDIR` before Remotion starts), so a build
  folder never leaves a copy of its media elsewhere. Nothing is fetched while
  rendering: Inter comes from `@fontsource/inter`, and the browser (Remotion's
  headless shell) is downloaded once, by `npm run setup` (`uo video setup`). A
  render without it is refused with one line naming `uo video setup`.
- `--fit <texts.json>` measures the text of a storyboard (`fitInputSchema` in
  `src/manifest.ts`) and prints one JSON line, `{"problems": [...]}`, each with
  the scene id and field (`rows[2]`); `uo video check` runs it. Its bundle and
  temp files go beside the input file.

## The manifest in brief

`<build>/manifest.json`, `<build>/audio/<scene id>.wav`,
`<build>/media/<scene id>-<n>.<ext>`. The manifest lists chapters, the series'
roadmap steps and the scenes in order; each scene has a `kind`, its `props`, its
`audio`, its `captions` (seconds from the start of the scene) and its `seconds`.
The contract (C3) is written in `docs/video.md`; the renderer's statement of it is
`src/manifest.ts`. Props are strict: an unknown property is an error.

| Kind | Draws |
| --- | --- |
| `title` | title, subtitle, meta line |
| `statement` | a headline of up to 120 characters and up to three points |
| `media` | one or two images or clips in the media box (below), with `zoom`, `highlight` boxes, `label`, `caption`; clips trimmed by `start` and `end`, looped or held on their last frame |
| `number` | one large value with its label and denominator |
| `list` | up to four rows as `bullets`, `numbered` or `pairs` (with column titles) |
| `roadmap` | the series' steps as done, current and upcoming |

Frame furniture on every scene: the series title and update number, the current
chapter, a progress line (gaps mark chapter starts), and, when `captions` is
true, a caption band at the bottom that nothing else draws into. Text steps
down in size until it fits and is never clipped; text that does not fit at the
smallest size (a media headline on one line, points and rows in two lines, ...)
is refused, by `--fit` at check time and by the validator before a render, with
the path of the field. The sizes live in `src/textfit.ts` (media: `src/mediabox.ts`),
used both by the scenes that draw the text and by `src/fitcheck.ts` that refuses it. Transitions are short fades inside each scene's own length.

## Develop

| Task | Command |
| --- | --- |
| Install | `npm ci` |
| Types and lint | `npm run check` |
| Validator and command-line tests (no browser, runs in CI) | `npm test` |
| Fixture build folder (`fixtures/build`, gitignored; made with ffmpeg) | `npm run fixture` |
| Render the fixture: video, a still of every scene, poster, a draft scene; checked with `ffprobe` | `npm run test:render` |
| Look at the fixture in the Studio | `npm run studio` |

The fixture is an invented series (a meadow trial). It uses every kind, a list in
all three styles, two-item media scenes, a zoomed and highlighted image, a looped
and a held clip, long text that has to be fitted, media at the box aspects
(2400x833, 1920x666, 1200x845, 960x676) and one 16:9 item that shows the fitted case. Rendered output goes to
`out/` (gitignored).

## Add a kind

1. `src/manifest.ts`: a zod schema for its props (strict), added to `KINDS`, to
   the scene union and to the exported types.
2. `src/kinds/<Name>.tsx`: the component. It draws inside `useFrameInfo().box`
   (the area above the caption band; a media scene lays itself out with `mediabox.ts`), takes sizes from `layout.ts` and colours
   from `theme.ts`, and fits text with `FitText` instead of clipping. Put the text
   sizes in `src/textfit.ts` and the kind's texts in `src/fitcheck.ts`, so text
   that cannot fit is refused instead of overflowing.
3. `src/Scene.tsx`: one `case` in `Layout` (TypeScript flags the missing one).
4. `fixtures/manifest.mjs`: a scene that uses it; `tests/validate.test.mjs`: the
   rules it enforces. Then `npm run test:render` and look at its still.
5. The storyboard side (`unfloat_os.channels.video`) needs the same kind and
   `docs/video.md` its row.

## Theme (C7)

`theme.json` is the only source of colours and the font. The renderer draws
every video in it; the manifest carries no theme. Reporting repositories read the
file by path (`--theme <repo>/renderer/theme.json`) and keep no copy of the
values. A change of any value needs the supervisor's OK first and re-exporting
the figures made with the old one.

## Media box (C7)

`theme.json` also carries the frame geometry that exporters need:

```json
"layout": { "frame": [1920, 1080], "media": { "one": [1728, 600], "two": [852, 600], "gap": 24 } }
```

A media scene is a one-line headline, the media, and one muted info line.
`src/mediabox.ts` holds the arithmetic (pure, tested without a browser):

- The item of a one-item scene is drawn in a box of `media.one` (1728x600 frame
  pixels); each item of a two-item scene in a box of `media.two` (852x600), the
  boxes `gap` (24) apart, centred. The boxes sit at y = 208, below the headline
  row and above the info line, clear of the caption band.
- An item whose aspect equals its box's (within 1 %; with `zoom`, the aspect of
  the shown part) is drawn at exactly the box size. Any other item is fitted
  inside the box, centred, on `bg`. `zoom` and `highlight` work in box
  coordinates.
- Export sizes for reporting repositories: figures 2400x833 (one item) and
  1200x845 (two items); clips 1920x666 and 960x676 (H.264 yuv420p, 30 fps, no
  audio). A 2400 px figure is drawn at 0.72, so 44 px of text is about 32 px in
  the frame.
- One muted line under the box, 40 px high at most, joins the labels and the
  caption: one item, `label · caption`; two items, each label centred under its
  own box, or, with a caption, one line `label · label · caption` across both.
- The headline takes one line; its size steps down from 68 to 40 px. A headline
  (or a line under the box) that cannot be one line at the smallest size is
  refused with the field path (`error: manifest.json: scenes[3].props.headline:
  does not fit one line at 40 px: shorten it`). The check runs in the browser
  (`src/fitcheck.ts`, in the composition's metadata step), where Inter is
  measured, and `bin/render.mjs` reports it.

`npm test` checks that the numbers come from `theme.json` and that a box-aspect
item is placed at exactly the box size; `npm run test:render` renders solid-colour
items and measures their boxes on the pixels of the stills.

## Licence

Remotion is used under its [free licence for individuals and small
teams](https://www.remotion.dev/docs/license). No licence key is set anywhere,
so server-side renders send no telemetry
([telemetry](https://www.remotion.dev/docs/telemetry)). Inter is SIL OFL 1.1.
