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
  headless shell) is downloaded once, by `npm run setup` or the first render.

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
| `media` | one or two images or clips, with `zoom`, `highlight` boxes, `label`, `caption`; clips trimmed by `start` and `end`, looped or held on their last frame |
| `number` | one large value with its label and denominator |
| `list` | up to four rows as `bullets`, `numbered` or `pairs` (with column titles) |
| `roadmap` | the series' steps as done, current and upcoming |

Frame furniture on every scene: the series title and update number, the current
chapter, a progress line (gaps mark chapter starts), and, when `captions` is
true, a caption band at the bottom that nothing else draws into. Text never
overflows: headlines and rows step down in size until they fit, they are not
clipped. Transitions are short fades inside each scene's own length.

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
all three styles, a two-item media scene, a zoomed and highlighted image, a looped
and a held clip, and long text that has to be fitted. Rendered output goes to
`out/` (gitignored).

## Add a kind

1. `src/manifest.ts`: a zod schema for its props (strict), added to `KINDS`, to
   the scene union and to the exported types.
2. `src/kinds/<Name>.tsx`: the component. It draws inside `useFrameInfo().box`
   (the area above the caption band), takes sizes from `layout.ts` and colours
   from `theme.ts`, and fits text with `FitText` instead of clipping.
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

## Licence

Remotion is used under its [free licence for individuals and small
teams](https://www.remotion.dev/docs/license). No licence key is set anywhere,
so server-side renders send no telemetry
([telemetry](https://www.remotion.dev/docs/telemetry)). Inter is SIL OFL 1.1.
