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
| `uo video build DR-### [--quality draft\|final] [--scene ID] [--provider NAME] [--voice NAME]` | Runs the check, voices every scene in one call, writes the build folder, renders; `--scene` renders one scene to `scene-<id>.mp4` for a quick look |
| `uo video list` | Every series with its updates |
| `uo video setup` | `npm ci` in the renderer |

The renderer is found at `renderer/` next to the source tree of `uo`, or at `$UNFLOAT_RENDERER`. Without it, or without its `node_modules`, a build stops with an error naming `uo video setup`.

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
| `media` | `headline`, `items` (1 or 2 tables: `src` a PNG, JPG or MP4 reference, `label`, `zoom` `[x, y, w, h]` as fractions, `highlight` a list of such boxes, `start` and `end` in seconds for a clip, `loop` default true), `caption` |
| `number` | `value` (a string such as `"94%"`), `label`, `denominator`, `headline` (all but the first two optional) |
| `list` | `headline`, `style` (`bullets`, `numbered` or `pairs`), `rows` (at most 4: strings, or tables `left`, `right` for pairs), `left_title`, `right_title` (pairs only), `asks` (true marks the update's asks) |
| `roadmap` | `headline`, `current` (a step id), `done` (step ids); the steps come from the series |

Rules (checked). `uo check` runs those that need only the text and the series on every video draft not `sent` or `dropped`: chapters known and in order, required chapters present, kinds and properties, headline and list limits, roadmap steps of the series, media inside the series allow-list, no markup or URLs in narration, and the channel's forbidden marks (`policy.problems(text, "video")`) in narration and every on-screen string. Placeholders left by `uo video new` are warnings there. `uo video check` adds what needs files: sources exist, the number check, media exist, placeholders (now errors), and the length estimate against the series bounds. `uo video build` refuses a draft with any error. Every error is one line naming the file, the scene and the rule:

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
2. Have the reporting repository write its figures and clips under the data folder, inside the series' `data.allow` (figures 2400x1350 PNG on the theme's background, clips 1920x1080 H.264 at 30 fps without audio, colours from `renderer/theme.json` read by path).
3. `uo video new <name> "title"`, write the scenes, `uo video check`, `uo video build`.

No engine change is needed.

### Adding a kind

1. Add an entry to `KINDS` in `storyboard.py`: its properties, which are required, and a check for each value. On-screen strings are found by walking the properties, so the marks and the number check apply to the new kind at once. Add a test of its rules.
2. Add a component for it in the renderer and to the renderer's manifest validation.
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
| `run_meta.json` | Commit and uncommitted changes of `os`, renderer package versions, voice provider and voice, input hashes |
| `build/` | What the renderer reads: `manifest.json`, `audio/<scene id>.wav` (48 kHz mono 16-bit), `media/` |

The voice cache is `private:updates/cache/voice/`; a second build with unchanged narration synthesizes nothing.

Timing: a narrated scene lasts a lead of 0.4 seconds, its speech, and a tail of 1 second; `seconds` and the length of its clips are floors. The lead is written into the scene's WAV, so the renderer plays the audio from the first frame of the scene and caption times (seconds from the start of the scene) include it. Scene lengths are written on whole frames.
