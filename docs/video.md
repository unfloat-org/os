# Update videos

A progress update can go out as a short narrated video (2 to 5 minutes: bottom line, goal, what was done, results with figures, limits, next steps and asks). Video is an outbound channel like email: a draft record holds the storyboard, checks run before anything is rendered, `uo video build` writes the video into the data folder, and a person sends it. Nothing in `uo` sends a video.

## The abstraction

| Part | Lives in | Job |
| --- | --- | --- |
| Storyboard | a draft record in the records repository, `channel: video` | What is said and shown: scenes in order, each with a chapter (its role in the story), a kind (its layout), narration and media |
| Series | `config/video/<series>.toml` in the records repository | Who a line of updates is for and what it may use: audience, roadmap, voice, lexicon, allowed media, length |
| Assets | written by the reporting repository into the data folder | Figures and clips. Anything bespoke is an asset; the engine has no project-specific scene |
| Engine | `unfloat_os.channels.video` (standard library) | Parse, check, voice, time, write the build folder, call the renderer, write the outputs, list updates in the tracker |
| Renderer | `renderer/` (Node, Remotion) | Draw the manifest. Knows nothing about records, series or data classes |

A scene has two axes. Its chapter carries the story: `title`, `bottom-line`, `goal`, `done`, `results`, `limits`, `next`, `end`, in that order; the series names the chapters an update may not leave out. Its kind carries the layout: `title`, `statement`, `media`, `number`, `list`, `roadmap`. A new line of work is a series file plus assets; a new look is a new kind.

Engine modules: `storyboard.py` (parse, kinds and shape rules), `series.py` (profiles), `refs.py` (references and allow-lists), `checks.py` (`uo check` and `uo video check`), `timing.py` (scene lengths), `media.py` (image sizes, clip lengths), `build.py` (build folder, renderer, outputs), `commands.py` (the `uo video` commands). The voice package `voice/` turns narration into speech.

## Commands

| Command | What it does |
| --- | --- |
| `uo video new SERIES "title"` | A draft with the next number of the series, its task and people, the chapter skeleton (title, the roadmap, a placeholder per required chapter) and the asks of the previous update in `## notes` |
| `uo video check DR-###` | Every check, including those that read files; prints the length estimate and the output class |
| `uo video build DR-### [--quality draft\|final] [--scene ID] [--provider NAME] [--voice NAME]` | Runs the check, voices every scene in one call, writes the build folder, renders; `--scene` renders one scene to `scene-<id>.mp4` (with `scene-<id>.run_meta.json`) for a quick look, `--quality draft` at half size |
| `uo video list` | Every series with its updates |
| `uo video setup` | `npm ci` and then `npm run setup` in the renderer: its packages, then its headless browser, so no render downloads anything |

The renderer is found at `renderer/` next to the source tree of `uo`, or at `$UNFLOAT_RENDERER`. Without it, or without its `node_modules`, a build stops with an error naming `uo video setup`. It needs Node 22.18 or newer and ffmpeg.

## The storyboard

A draft with `channel: video` has the usual draft frontmatter plus `series` (a profile `config/video/<series>.toml` must exist) and `number` (a positive whole number, unique in its series among drafts not `dropped`). Both are required for video drafts and refused on other channels (checked).

The body:

- A scene starts at `## <chapter>` or `## <chapter>: <label>`. The scene id is the slug of the heading (`results: harvest` gives `results-harvest`) and is unique in the file.
- Right after the heading comes one fenced block with the info string `toml`: the scene's properties, `kind` required. Unknown properties are errors.
- Then the narration: plain paragraphs up to the next level-2 heading. Spoken text only: no Markdown markup, no URLs. A blank line between paragraphs is a longer pause. A scene may have no narration.
- `## notes` ends the storyboard. What follows is for the author and is never rendered, spoken or checked.

Properties of every kind: `kind`; `sources` (references, for the number check); `numbers_ok` (numerals exempt from it); `seconds` (the least length of the scene, or the length of a scene without narration).

| Kind | Properties |
| --- | --- |
| `title` | `title`, `subtitle` (optional), `meta` (optional; default: series title, update number and the draft's `created` date) |
| `statement` | `headline` (at most 120 characters), `points` (optional, at most 3) |
| `media` | `headline`, `items` (1 or 2 tables: `src` a PNG, JPG or MP4 reference, `label`, `zoom` `[x, y, w, h]` as fractions, `highlight` a list of such boxes, `start` and `end` in seconds for a clip, inside the clip's length, `loop` default true; at most 8 highlight boxes), `caption` |
| `number` | `value` (a string such as `"94%"`), `label`, `denominator`, `headline` (all but the first two optional) |
| `list` | `headline`, `style` (`bullets`, `numbered` or `pairs`), `rows` (at most 4: strings, or tables `left`, `right` for pairs), `left_title`, `right_title` (pairs only), `asks` (true marks the update's asks) |
| `roadmap` | `headline` (optional), `current` (a step id), `done` (step ids, may be empty); the steps come from the series |

Rules (checked). `uo check` runs those that need only the text and the series on every video draft not `sent` or `dropped`: chapters known and in order, required chapters present, kinds and properties, headline and list limits, roadmap steps of the series, media inside the series allow-list, no markup or URLs in narration, and the channel's forbidden marks (`policy.problems(text, "video")`) in narration and every on-screen string. Placeholders left by `uo video new` are warnings there. `uo video check` adds what needs files: sources exist, the number check, media exist, a clip's `start` and `end` lie inside it, placeholders (now errors), and the length estimate against the series bounds. `uo video build` refuses a draft with any error. Every error is one line naming the file, the scene and the rule:

```
error: records/drafts/DR-###-update-3.md: scene results-harvest: number: '43 percent' occurs in none of its sources; cite one or list it in numbers_ok
```

The number check: every numeral with two or more digits, a decimal point or a percent sign, in narration and on-screen text, must occur in at least one file named in the scene's `sources`, unless listed in `numbers_ok`. `94%`, `94 %` and `94 percent` count as the same; `3.6` does not match `3.62`, and `24` does not match `2024`. Generated text (the default `meta`) is not checked.

The length estimate: each narrated scene counts its words at the series' `words_per_minute`, plus the pauses between paragraphs and the scene's lead and tail; `seconds` and the length of its clips are floors (clip lengths need `ffprobe`). A scene without narration lasts `seconds`, or 4 seconds.

### A full example

An invented series about a community garden:

````markdown
---
id: DR-###
title: Update 3
status: draft
channel: video
task:
people: []
created: 2026-11-17
series: garden
number: 3
---
## title

```toml
kind = "title"
title = "Community garden trial"
subtitle = "Third update"
```

This is the third update on the garden trial.

## bottom-line

```toml
kind = "statement"
headline = "Mulch gave more tomatoes"
points = ["Decision needed: mulch every bed next year?"]
sources = ["private:garden/results/harvest.md"]
```

Beds with mulch gave more tomatoes. I need one decision from you.

## goal: roadmap

```toml
kind = "roadmap"
headline = "Where we are"
current = "harvest"
done = ["plant"]
```

Planting is done. We are at the harvest.

## results: harvest

```toml
kind = "media"
headline = "Beds with mulch gave 41% more tomatoes"
items = [{ src = "private:updates/assets/garden/u3/harvest.png", highlight = [[0.55, 0.10, 0.35, 0.80]] }]
sources = ["private:garden/results/harvest.md"]
```

The twelve beds with mulch gave 41 percent more tomatoes than the twelve without.

## results: count

```toml
kind = "number"
value = "24"
label = "beds in the trial"
sources = ["private:garden/results/harvest.md"]
seconds = 6
```

## next: asks

```toml
kind = "list"
headline = "What I need from you"
style = "numbered"
asks = true
rows = ["Mulch for every bed next spring", "Two more volunteers on Saturdays"]
```

Two things. Mulch for every bed next spring. And two more volunteers on Saturdays.

## notes

For the author only.
````

## The series profile

`config/video/<series>.toml` in the records repository. Unknown keys and bad values are errors naming the key (checked by `uo check`, which also checks that `task` and `people` exist and that the title and roadmap labels carry no forbidden marks).

```toml
title = "Community garden trial"     # on the title scene and in the frame
audience = "The garden committee."   # one sentence for authors; not rendered
task = "T-###"                       # default task of new drafts (optional)
people = []                          # default people of new drafts (P-### IDs)
captions = true                      # captions drawn in the frame

[length]                             # defaults 90, 300, 140
min_seconds = 90
max_seconds = 300
words_per_minute = 140               # for the estimate of `uo video check`

[chapters]
required = ["bottom-line", "results", "next"]   # the default

[voice]
provider = "say"                     # silent | say | kokoro | elevenlabs | file
voice = ""                           # the provider's voice name; empty is its default
speed = 1.0

[voice.lexicon]                      # spoken replacements; captions keep the written word
"mulch" = "mulsh"

[voice.options]                      # provider options, for example path = "..." for `file` (optional)

[data]
allow = ["private:updates/assets/garden/**"]    # media this series may show (globs of references)
release = []                         # { path = "restricted:...", reason = "..." }: restricted files that count as private

[[roadmap]]
id = "plant"
label = "Planting"

[[roadmap]]
id = "harvest"
label = "Harvest"
```

### Adding a series

1. Write `config/video/<name>.toml` in the records repository and run `uo check`.
2. Have the reporting repository write its figures and clips under the data folder, inside the series' `data.allow` (sizes and colours as in "Theme and the media box" below: read `renderer/theme.json` by path, keep no copy of its values).
3. `uo video new <name> "title"`, write the scenes, `uo video check`, `uo video build`.

No engine change is needed.

### Adding a kind

1. Add an entry to `KINDS` in `storyboard.py`: its properties, which are required, and a check for each value. On-screen strings are found by walking the properties, so the marks and the number check apply to the new kind at once. Add a test of its rules.
2. Add a component for it in the renderer and to the renderer's manifest validation (`renderer/README.md`, "Add a kind"). Use it in the bakery storyboard of `tests/fixtures/video/helpers.py`, so `tests/test_video_render.py` proves that the renderer accepts what the engine writes for it and draws it.
3. Document it in the table above.

## References and data classes

A reference is `<root>:<relative path>`. The root is a data class (`public`, `private`, `restricted`: the workspace's data folders) or a member of the workspace (`ops`, `os` and the other repositories). No absolute paths, no `..`, no symlink that resolves outside the root. Media must be data-class references that match a glob of the series' `data.allow` (`**` crosses folders, `*` stays inside one name); sources may use any root.

The output class is `restricted` when any media is restricted and not named in the series' `data.release`; otherwise `private`. `uo video check` and `uo video build` print which media made a video restricted. Media are copied into the build folder under scene-based names (`media/<scene id>-<n>.<ext>`), so source file names never reach a frame, a caption or an output name, and the renderer runs with `TMPDIR` inside the build folder, so no copy of restricted media lands outside the restricted folder. `uo gmail push` attaches files only from the private folder, so a restricted video cannot be attached.

## Outputs

`<class>:updates/<draft id>/`:

| File | What |
| --- | --- |
| `video.mp4` | 1920x1080 H.264 yuv420p at 30 fps, AAC narration, captions in the frame |
| `captions.srt` | The caption segments, offset by the start of their scene |
| `transcript.md` | The narration by chapter, the on-screen text, the figures named by scene, the sources of each scene |
| `poster.png` | A frame of the first scene |
| `stills/<scene id>.png` | One frame per scene, at two thirds of its length |
| `run_meta.json` | Commit and uncommitted changes of `os`, renderer package versions, voice provider, voice and speed with the number of sentences this build synthesized and the voice step's seconds, input hashes |
| `build/` | What the renderer reads: `manifest.json`, `audio/<scene id>.wav` (48 kHz mono 16-bit), `media/` |

The voice cache is `private:updates/cache/voice/`; a second build with unchanged narration synthesizes nothing. The build prints one line for the voice, for example `voice: kokoro af_heart, 0 sentence(s) synthesized, 0.0 s`.

Timing: a narrated scene lasts a lead of 0.4 seconds, its speech, and a tail of 1 second; `seconds` and the length of its clips are floors. The lead is written into the scene's WAV, so the renderer plays the audio from the first frame of the scene and caption times (seconds from the start of the scene) include it. Scene lengths are written on whole frames.

## The voice (contract C4)

`voice/` turns the narration of all scenes into speech in one call (`synthesize`), so a local model starts once per build. The unit is the sentence: each is synthesized on its own and cached by provider, voice, speed, options and the spoken text (after the lexicon), and the sentences of a scene are joined with 0.35 s pauses (0.7 s between paragraphs). That gives exact caption times without an aligner, and a changed sentence is the only one synthesized again. A caption longer than 84 characters is shown in parts, cut near the middle, at a clause boundary when one is close.

| Provider | Needs | Notes |
| --- | --- | --- |
| `silent` | nothing | Silence at `options.words_per_minute` (default 140): tests and timing drafts |
| `say` | macOS | `voice` is a `say` voice (`say -v '?'`); an unknown name is refused. `say` changes its rate in steps, so `speed` 0.9 sounds like 1.0; use 0.8 or below |
| `kokoro` | `uv` (`uvx` runs Kokoro-82M through mlx-audio, Apple silicon) | Default voice `af_heart`; the first run downloads the model and its environment. About 0.1 s per second of audio |
| `elevenlabs` | an API key in `~/.config/unfloat/elevenlabs/api_key` or `ELEVENLABS_API_KEY` | Sends each spoken sentence over the network: a cloud service, not for project content without a decision. Refuses with one line without a key |
| `file` | `options.path`: a WAV, or a folder of `<scene id>.wav` | A recorded narration; not cached, no lexicon |

`--provider` and `--voice` on `uo video build` override the series for one build; `run_meta.json` records what was used. The lexicon of a series is spelled for the provider it was written for.

## Theme and the media box (contract C7)

`renderer/theme.json` is the one source of the look: colours (`color`), the font (`font`) and the frame geometry (`layout`). The renderer draws every video in it, the manifest carries no theme, and a reporting repository reads the file by path and keeps no copy of its values. A change of any value needs the maintainer's OK and a re-export of the figures made with the old one.

```json
"layout": { "frame": [1920, 1080], "media": { "one": [1728, 600], "two": [852, 600], "gap": 24 } }
```

The media box is where a media scene's items are drawn, in frame pixels. A one-item scene has one box of `media.one`; a two-item scene has two boxes of `media.two`, `gap` apart; the boxes are centred and their row is as wide as the headline. An item whose aspect equals its box's (within 1 %; with `zoom`, the aspect of the zoomed part) is drawn at exactly the box size, so text in a figure lands at a known size in the frame. Any other item is fitted inside its box, centred, on the background colour, never cropped. `zoom` and `highlight` work in box coordinates.

Export sizes that follow from the boxes:

| Where | Figure (PNG, on the `bg` colour) | Clip (MP4 H.264 yuv420p, 30 fps, no audio) |
| --- | --- | --- |
| one-item scene | 2400x833 | 1920x666 |
| two-item scene, each item | 1200x845 | 960x676 |

Figures have no title inside (the scene headline carries the message), one message each, no text smaller than 44 px (about 32 px in the frame in a one-item scene), axis labels about 56 px, `data` colours in order, `accent` for the one thing to look at, no case id, date, stem or file name. A figure drawn for the box has no margin to give away: keep its own padding inside the figure.

Under the box runs one muted line of at most 40 px that joins the item labels and the caption: one item, `label · caption`; two items, each label centred under its own box; two items with a caption, one centred line across both boxes (`label · label · caption`), because the caption is never dropped and the line stays one line. The headline of a media scene is one line: its size steps down from 68 to 40 px, and a headline that does not fit one line at 40 px (or a line under the box that does not fit at 24 px) is refused by the renderer with the path of the field (`scenes[3].props.headline: does not fit one line at 40 px: shorten it`). It is decided in the browser, where Inter is measured, before anything is drawn.

## The build folder and the renderer (contract C3)

The engine writes a build folder and the renderer reads nothing else:

```
build/manifest.json
build/audio/<scene id>.wav           48 kHz mono 16-bit PCM, starting with the 0.4 s lead
build/media/<scene id>-<n>.<ext>     copies under scene-based names
```

`manifest.json`, as the engine writes it (the bakery fixture, one scene shown):

```json
{
  "version": 1, "draft": "DR-###", "title": "Update 2",
  "series": { "name": "bakery", "title": "Sourdough starter trial", "number": 2 },
  "date": "2026-11-17", "fps": 30, "width": 1920, "height": 1080, "captions": true,
  "chapters": [ { "id": "title", "label": "Title" }, { "id": "results", "label": "Results" } ],
  "roadmap": [ { "id": "mix", "label": "Mixing" }, { "id": "bake", "label": "Test bakes" } ],
  "scenes": [
    {
      "id": "results-rise", "chapter": "results", "kind": "media",
      "props": {
        "headline": "Rye rose 35% higher than wheat",
        "caption": "Rise measured after four hours",
        "items": [
          { "src": "media/results-rise-1.png", "label": "Rise by starter", "highlight": [[0.08, 0.15, 0.26, 0.75]],
            "type": "image", "width": 2400, "height": 1350 },
          { "src": "media/results-rise-2.mp4", "label": "Oven camera", "loop": false,
            "type": "video", "width": 1920, "height": 1080, "seconds": 6.0 }
        ]
      },
      "audio": { "src": "audio/results-rise.wav", "seconds": 7.45 },
      "captions": [ { "text": "On the left, the rise of each starter.", "start": 0.4, "end": 2.4 } ],
      "seconds": 8.433
    }
  ]
}
```

- `props` are the scene's storyboard properties minus `kind`, `sources`, `numbers_ok` and `seconds`, as written (no defaults filled in: `loop` is absent unless written), with each media `src` rewritten into the build folder and each item given `type`, `width`, `height` and, for a clip, `seconds` (its whole probed length). The title scene always carries `meta` (the default is generated). `denominator` and `value` are strings.
- `chapters` lists the chapters present, in order, with their labels; `roadmap` is the series' steps. `audio` is null for a scene without narration. `captions` are always written, also when the series sets `captions = false` (then the top-level flag tells the renderer not to draw them); their times are seconds from the start of the scene and include the lead. Every `seconds` is on whole frames, so the video is exactly the sum of its scenes.
- The renderer validates the manifest strictly (`renderer/src/manifest.ts`): an unknown property in `props` or in a media item, a missing file, audio or a caption past the end of its scene are refused with the path of the field. The engine's checks are at least as strict, so a draft that passes `uo video check` always renders: `tests/test_video_render.py` runs the renderer's own validation on the manifests the engine writes.

The command, run by `build.py` in the renderer folder with `TMPDIR`, `TMP` and `TEMP` set to `build/tmp` and any Remotion licence variable removed (no licence key is ever passed, so a render sends no telemetry):

```
node bin/render.mjs --build <build> --out <out>/video.mp4 --quality final --stills <out>/stills --poster <out>/poster.png
node bin/render.mjs --build <build> --out <out>/scene-<id>.mp4 --quality draft|final --scene <id>
```

A full build is one call, so the renderer bundles and starts its browser once. The bundle goes to `build/bundle`; its public folder is `build/tmp/public`, links to `build/audio` and `build/media` that are removed after bundling. So the media, the bundle and every temporary file stay inside the build folder, which is inside the output class's folder. On failure the renderer prints one line starting with `error:`, which `uo video build` reports. The renderer plays each scene's WAV from the scene's first frame, so speech starts 0.4 s into the scene and the drawn captions follow the caption times.

## Checking the whole chain

`tests/test_video_render.py` builds an invented series (a sourdough starter trial: every kind, a media scene with an image and a restricted clip, captions on) through `uo video build` with the real renderer and a stand-in voice that writes a tone per sentence in the voice package's WAV format (deterministic and offline; the real providers are proved by hand, `tests/test_video_voice.py` covers the package). It checks with `ffprobe` that `video.mp4` is 1920x1080 H.264 yuv420p at 30 fps with AAC and has exactly as many frames as its scenes, that sound starts 0.4 s into every narrated scene, that `captions.srt` matches the manifest, that the restricted clip and the bundle stay inside the restricted folder (nothing new in the user temp folder or `/tmp` holds them, and `build/tmp` is empty), that the clip's file name reaches no output, and that a second build with `--scene` and `--quality draft` synthesizes nothing and renders one scene at half size.

It runs as part of `uv run pytest` where the renderer is installed (`uo video setup`) and ffmpeg is on PATH, and takes about 30 seconds; elsewhere, and on CI, it is skipped (`UO_SKIP_RENDER=1` skips it too). CI runs the renderer's own checks in the job `renderer` (`npm ci`, `npm run check`, `npm test`: no browser, no render). To keep the outputs and look at the stills:

```
uv run pytest tests/test_video_render.py --basetemp=renderer/out/e2e
# outputs: renderer/out/e2e/test_a_storyboard_becomes_a_vi0/workspace/data/restricted/updates/DR-###/
```
