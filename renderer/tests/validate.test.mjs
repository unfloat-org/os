// The manifest validator and frame arithmetic. No browser: runs anywhere node does.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import { fixtureManifest } from "../fixtures/manifest.mjs";
import { KINDS } from "../src/manifest.ts";
import { sceneFrames, sceneSpans, stillFrame, totalFrames } from "../src/timing.ts";
import { checkFiles, parseManifest } from "../src/validate.ts";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const sceneOf = (m, id) => m.scenes.find((s) => s.id === id);
const errorOf = (mutate) => {
  const m = fixtureManifest();
  mutate(m);
  try {
    parseManifest(m);
  } catch (err) {
    return err.message;
  }
  return null;
};

test("the fixture is a valid manifest that uses every kind", () => {
  const m = parseManifest(fixtureManifest());
  assert.deepEqual(
    [...new Set(m.scenes.map((s) => s.kind))].sort(),
    [...KINDS].sort(),
  );
  const styles = m.scenes.filter((s) => s.kind === "list").map((s) => s.props.style);
  assert.deepEqual([...new Set(styles)].sort(), ["bullets", "numbered", "pairs"]);
  assert.ok(m.scenes.some((s) => s.kind === "media" && s.props.items.length === 2));
});

// [what is wrong, change to the fixture, the path the error must name]
const broken = [
  ["unknown kind", (m) => (sceneOf(m, "goal").kind = "poem"), "scenes[2].kind"],
  ["missing headline", (m) => delete sceneOf(m, "goal").props.headline, "scenes[2].props.headline"],
  [
    "headline over 120 characters",
    (m) => (sceneOf(m, "goal").props.headline = "x".repeat(121)),
    "scenes[2].props.headline",
  ],
  [
    "unknown property",
    (m) => (sceneOf(m, "goal").props.colour = "red"),
    "scenes[2].props",
  ],
  [
    "more than three points",
    (m) => (sceneOf(m, "bottom-line").props.points = ["a", "b", "c", "d"]),
    "scenes[1].props.points",
  ],
  [
    "more than four rows",
    (m) => (sceneOf(m, "next-steps").props.rows = ["a", "b", "c", "d", "e"]),
    "scenes[12].props.rows",
  ],
  [
    "a pairs row that is a string",
    (m) => (sceneOf(m, "results-pairs").props.rows[1] = "just text"),
    "scenes[10].props.rows[1]",
  ],
  [
    "a bullets row that is a pair",
    (m) => (sceneOf(m, "next-asks").props.rows[0] = { left: "a", right: "b" }),
    "scenes[13].props.rows[0]",
  ],
  [
    "a column title on a bullets list",
    (m) => (sceneOf(m, "next-asks").props.left_title = "Left"),
    "scenes[13].props.left_title",
  ],
  [
    "roadmap step that does not exist",
    (m) => (sceneOf(m, "done-roadmap").props.current = "harvest"),
    "scenes[4].props.current",
  ],
  [
    "current step also listed as done",
    (m) => sceneOf(m, "done-roadmap").props.done.push("analyse"),
    "scenes[4].props.done[3]",
  ],
  [
    "scene in a chapter that is not listed",
    (m) => (sceneOf(m, "limits").chapter = "appendix"),
    "scenes[11].chapter",
  ],
  [
    "scenes out of chapter order",
    (m) => (sceneOf(m, "goal").chapter = "next"),
    "scenes[3].chapter",
  ],
  [
    "two scenes with one id",
    (m) => (sceneOf(m, "limits").id = "goal"),
    "scenes[11].id",
  ],
  ["scene id that is not a safe name", (m) => (m.scenes[0].id = "../title"), "scenes[0].id"],
  [
    "audio outside the build folder",
    (m) => (m.scenes[0].audio.src = "../outside.wav"),
    "scenes[0].audio.src",
  ],
  [
    "absolute audio path",
    (m) => (m.scenes[0].audio.src = "/etc/hosts"),
    "scenes[0].audio.src",
  ],
  [
    "audio longer than its scene",
    (m) => (m.scenes[0].audio.seconds = 9),
    "scenes[0].audio.seconds",
  ],
  [
    "caption ends after the scene",
    (m) => (m.scenes[0].captions[0].end = 99),
    "scenes[0].captions[0].end",
  ],
  [
    "caption that ends before it starts",
    (m) => Object.assign(m.scenes[0].captions[0], { start: 3, end: 2 }),
    "scenes[0].captions[0].end",
  ],
  [
    "zoom outside the media",
    (m) => (sceneOf(m, "results-compare").props.items[0].zoom = [0.6, 0.1, 0.6, 0.5]),
    "scenes[7].props.items[0].zoom",
  ],
  [
    "highlight outside the media",
    (m) => (sceneOf(m, "results-bees").props.items[0].highlight = [[0.8, 0.1, 0.4, 0.4]]),
    "scenes[6].props.items[0].highlight[0]",
  ],
  [
    "clip without its length",
    (m) => delete sceneOf(m, "results-walk").props.items[0].seconds,
    "scenes[9].props.items[0].seconds",
  ],
  [
    "clip trimmed past its end",
    (m) => (sceneOf(m, "results-walk").props.items[0].end = 30),
    "scenes[9].props.items[0].end",
  ],
  [
    "three media items",
    (m) => {
      const items = sceneOf(m, "results-compare").props.items;
      items.push(items[0]);
    },
    "scenes[7].props.items",
  ],
  ["wrong frame width", (m) => (m.width = 1280), "width"],
  ["wrong frame rate", (m) => (m.fps = 24), "fps"],
  ["unknown version", (m) => (m.version = 2), "version"],
  ["no scenes", (m) => (m.scenes = []), "scenes"],
];

for (const [what, mutate, field] of broken) {
  test(`refused: ${what}`, () => {
    const message = errorOf(mutate);
    assert.ok(message, "the manifest was accepted");
    assert.ok(
      message.startsWith(`${field}:`),
      `expected the path ${field}, got: ${message}`,
    );
  });
}

test("a missing file is named by its field", () => {
  const build = mkdtempSync(path.join(tmpdir(), "uo-renderer-test-"));
  try {
    const m = parseManifest(fixtureManifest());
    mkdirSync(path.join(build, "audio"));
    mkdirSync(path.join(build, "media"));
    for (const scene of m.scenes) {
      if (scene.audio) writeFileSync(path.join(build, scene.audio.src), "");
      if (scene.kind === "media") {
        for (const item of scene.props.items) writeFileSync(path.join(build, item.src), "");
      }
    }
    checkFiles(m, build);
    rmSync(path.join(build, "media/results-bees-1.png"));
    assert.throws(
      () => checkFiles(m, build),
      /scenes\[6\]\.props\.items\[0\]\.src: file not found in the build folder: media\/results-bees-1\.png/,
    );
  } finally {
    rmSync(build, { recursive: true, force: true });
  }
});

test("scene lengths are rounded to frames one by one, and the video is their sum", () => {
  assert.equal(sceneFrames(7.4, 30), 222);
  assert.equal(sceneFrames(0.016, 30), 1); // never an empty scene
  const scenes = [{ seconds: 1.016 }, { seconds: 1.016 }, { seconds: 2.5 }];
  // each 30.48 frames rounds to 30; summing first would give 91
  assert.equal(totalFrames(scenes, 30), 30 + 30 + 75);
  const spans = sceneSpans(scenes, 30);
  assert.deepEqual(
    spans.map((s) => s.from),
    [0, 30, 60],
  );
  assert.equal(stillFrame(spans[2]), 60 + 50);
  assert.equal(stillFrame({ from: 10, frames: 1 }), 10);
});

test("the fixture's length is the sum of its scene lengths", () => {
  const m = fixtureManifest();
  assert.equal(totalFrames(m.scenes, m.fps), 104 * 30);
});

const render = (...args) =>
  spawnSync("node", [path.join(root, "bin/render.mjs"), ...args], {
    encoding: "utf8",
    cwd: root,
  });

test("the command line refuses what it cannot render with one error line", () => {
  const build = mkdtempSync(path.join(tmpdir(), "uo-renderer-test-"));
  try {
    const cases = [
      [[], /^error: --build is required/],
      [["--build", build], /^error: one of --out, --stills, --poster/],
      [["--build", build, "--out", "x.mp4", "--quality", "best"], /^error: --quality must be draft or final/],
      [["--build", build, "--stills", "x", "--scene", "a"], /^error: --scene needs --out/],
      [["--build", path.join(build, "missing"), "--poster", "p.png"], /^error: build folder not found/],
      [["--build", build, "--poster", "p.png"], /^error: .*manifest\.json: not found/],
    ];
    for (const [args, pattern] of cases) {
      const run = render(...args);
      assert.equal(run.status, 1, args.join(" "));
      assert.equal(run.stdout, "");
      assert.equal(run.stderr.trimEnd().split("\n").length, 1, run.stderr);
      assert.match(run.stderr, pattern);
    }

    const m = fixtureManifest();
    m.scenes[1].props.headline = "";
    writeFileSync(path.join(build, "manifest.json"), JSON.stringify(m));
    const run = render("--build", build, "--poster", path.join(build, "p.png"));
    assert.equal(run.status, 1);
    assert.match(run.stderr, /^error: manifest\.json: scenes\[1\]\.props\.headline: must not be empty\n$/);
  } finally {
    rmSync(build, { recursive: true, force: true });
  }
});
