// The fixture series: an invented meadow trial, nothing to do with any real
// project. Every kind appears at least once, a list in all three styles, a
// two-item media scene, an image with zoom and highlights, clips trimmed and
// looped or held, and long text that has to be fitted.

const IMAGE = { width: 2400, height: 1350 };
const CLIP_A = { seconds: 3, width: 1920, height: 1080 };
const CLIP_B = { seconds: 4, width: 1920, height: 1080 };

/** Files the fixture needs, written by fixtures/make.mjs (nothing is committed). */
export const fixtureMedia = {
  "media/results-bees-1.png": { kind: "bars", ...IMAGE },
  "media/results-compare-1.png": { kind: "dots", ...IMAGE },
  "media/results-compare-2.mp4": { kind: "clip", ...CLIP_B, shade: "0x6CC5F0" },
  "media/results-walk-1.mp4": { kind: "clip", ...CLIP_A, shade: "0xFFB454" },
};

const chapters = [
  { id: "title", label: "Title" },
  { id: "bottom-line", label: "Bottom line" },
  { id: "goal", label: "Goal" },
  { id: "done", label: "What was done" },
  { id: "results", label: "Results" },
  { id: "limits", label: "Limits" },
  { id: "next", label: "Next" },
  { id: "end", label: "End" },
];

const roadmap = [
  { id: "site", label: "Sites chosen" },
  { id: "sow", label: "Strips sown" },
  { id: "count", label: "Weekly counts" },
  { id: "analyse", label: "Analysis" },
  { id: "report", label: "Final report" },
];

const cap = (text, start, end) => ({ text, start, end });

/** `lines`: caption texts spread evenly over the narration; narration ends 0.9 s before the scene. */
const scene = (id, chapter, kind, props, seconds, lines = []) => {
  const spoken = Math.max(0, seconds - 1.2);
  const slot = lines.length ? (spoken - 0.4) / lines.length : 0;
  return {
    id,
    chapter,
    kind,
    props,
    audio: lines.length ? { src: `audio/${id}.wav`, seconds: spoken } : null,
    captions: lines.map((text, i) =>
      cap(text, +(0.4 + i * slot).toFixed(2), +(0.4 + (i + 1) * slot).toFixed(2)),
    ),
    seconds,
  };
};

export const fixtureManifest = () => ({
  version: 1,
  draft: "fixture",
  title: "Update 2",
  series: { name: "meadow", title: "Meadow restoration trial", number: 2 },
  date: "2026-11-16",
  fps: 30,
  width: 1920,
  height: 1080,
  captions: true,
  chapters,
  roadmap,
  scenes: [
    scene(
      "title",
      "title",
      "title",
      {
        title: "Meadow restoration trial",
        subtitle: "Do wildflower strips bring the bees back?",
        meta: "Meadow restoration trial · Update 2 · 2026-11-16",
      },
      4.5,
      ["Update two of the meadow restoration trial."],
    ),
    scene(
      "bottom-line",
      "bottom-line",
      "statement",
      {
        headline: "Wildflower strips roughly doubled the bees we counted at field margins",
        points: [
          "Twenty-four transects, counted weekly for eight weeks",
          "Clear at the margin, not yet clear inside the field",
          "One more season before we recommend anything",
        ],
      },
      9,
      [
        "Wildflower strips roughly doubled the bees we counted at the margins.",
        "That holds for twenty-four transects over eight weeks.",
        "Inside the field the picture is still unclear.",
      ],
    ),
    scene(
      "goal",
      "goal",
      "statement",
      {
        headline:
          "Test whether a four-metre strip of native wildflowers on a field margin raises the number of bees within one season",
      },
      6,
      ["The goal was a simple test of one strip, one season."],
    ),
    scene(
      "done-steps",
      "done",
      "list",
      {
        headline: "What was done this period",
        style: "bullets",
        rows: [
          "Sowed strips on twelve of the twenty-four margins",
          "Counted bees on every transect each week for eight weeks",
          "Kept the observers unaware of which margins were sown",
          "Entered every count the same day, in the shared sheet, before looking at any totals",
        ],
      },
      9,
      [
        "We sowed strips on twelve of the twenty-four margins.",
        "Observers counted every week and did not know which margins were sown.",
      ],
    ),
    scene(
      "done-roadmap",
      "done",
      "roadmap",
      { headline: "Where the trial stands", current: "analyse", done: ["site", "sow", "count"] },
      6,
      ["Counting is finished and the analysis is under way."],
    ),
    scene(
      "results-number",
      "results",
      "number",
      {
        headline: "Bees per transect",
        value: "63%",
        label: "more bees on strip margins",
        denominator: "24 margins, 8 weekly counts each",
      },
      7,
      ["Strip margins had sixty-three percent more bees.", "That is twenty-four margins, eight counts each."],
    ),
    scene(
      "results-bees",
      "results",
      "media",
      {
        headline: "Strip margins gave more bees in every week after sowing",
        items: [
          {
            src: "media/results-bees-1.png",
            type: "image",
            ...IMAGE,
            highlight: [[0.5, 0.12, 0.4, 0.8]],
            label: "Bees per transect, by week",
          },
        ],
        caption: "Weeks one to eight, strip margins in orange",
      },
      8,
      ["The gap opens in week three and stays open.", "The orange bars are the strip margins."],
    ),
    scene(
      "results-compare",
      "results",
      "media",
      {
        headline: "Same hedge, two margins",
        items: [
          {
            src: "media/results-compare-1.png",
            type: "image",
            ...IMAGE,
            zoom: [0.2, 0.15, 0.6, 0.7],
            highlight: [[0.45, 0.4, 0.2, 0.25]],
            label: "Counts, enlarged",
          },
          {
            src: "media/results-compare-2.mp4",
            type: "video",
            ...CLIP_B,
            start: 0.5,
            end: 3.5,
            loop: true,
            label: "Walk along the strip",
          },
        ],
      },
      8,
      ["On the left, the counts enlarged.", "On the right, a walk along the strip, looped."],
    ),
    scene(
      "results-walk",
      "results",
      "media",
      {
        headline: "A short clip holds on its last frame",
        items: [
          {
            src: "media/results-walk-1.mp4",
            type: "video",
            ...CLIP_A,
            loop: false,
            label: "Clip of three seconds in a scene of six",
          },
        ],
      },
      6,
      ["The clip is shorter than the scene, so it holds."],
    ),
    scene(
      "results-pairs",
      "results",
      "list",
      {
        headline: "What changed, margin by margin",
        style: "pairs",
        left_title: "Margin",
        right_title: "Bees per transect",
        rows: [
          { left: "Sown strip", right: "14.2" },
          { left: "Unsown, same hedge", right: "8.7" },
          { left: "Unsown, far hedge", right: "8.1" },
        ],
      },
      7,
      ["Sown strips averaged fourteen point two bees per transect.", "Both kinds of unsown margin sat near eight."],
    ),
    scene(
      "limits",
      "limits",
      "statement",
      {
        headline: "One season, one site, one observer team",
        points: ["Weather differed between weeks", "We did not count inside the fields"],
      },
      7,
      ["Treat this as one season at one site.", "Weather differed between weeks, and we did not count inside the fields."],
    ),
    scene(
      "next-steps",
      "next",
      "list",
      {
        headline: "Next steps",
        style: "numbered",
        rows: [
          "Finish the analysis of the eight weeks",
          "Repeat the counts next spring on the same margins",
          "Add counts inside the field, ten metres in",
        ],
      },
      8,
      ["First, finish the analysis.", "Then repeat the counts next spring and add counts inside the field."],
    ),
    scene(
      "next-asks",
      "next",
      "list",
      {
        headline: "What we need from you",
        style: "bullets",
        asks: true,
        rows: ["Access to the north field in March", "A second observer team for the inside counts"],
      },
      7,
      ["Two things would help.", "Access to the north field in March, and a second observer team."],
    ),
    scene(
      "end",
      "end",
      "statement",
      { headline: "Next update: a full season of counts" },
      4.5,
      ["The next update follows the spring counts."],
    ),
  ],
});
