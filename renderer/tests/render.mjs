// Renders the fixture on this machine: the video, a still of every scene, the
// poster, and one scene at draft quality; then checks the files with ffprobe and
// that nothing was left in the system temp folders. Needs node, ffmpeg and
// ffprobe; downloads Remotion's browser on the first run. Not run in CI.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import {
  existsSync,
  mkdirSync,
  readdirSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync,
} from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { fixtureManifest } from "../fixtures/manifest.mjs";
import { mediaLayout, placeInBox } from "../src/mediabox.ts";
import { sceneSpans, totalFrames } from "../src/timing.ts";
import { boundingBox, readPng } from "./png.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const build = path.join(root, "fixtures", "build");
const out = path.join(root, "out", "test-render");

const run = (cmd, args) => {
  const r = spawnSync(cmd, args, { encoding: "utf8", cwd: root });
  assert.equal(r.status, 0, `${cmd} ${args.join(" ")}\n${r.stdout}\n${r.stderr}`);
  return r.stdout;
};
const probe = (file) =>
  JSON.parse(
    run("ffprobe", [
      "-v", "error", "-print_format", "json", "-show_streams", "-show_format", file,
    ]),
  );
const pngSize = (file) => {
  const b = readFileSync(file);
  assert.equal(b.subarray(1, 4).toString(), "PNG", `${file} is not a PNG`);
  return [b.readUInt32BE(16), b.readUInt32BE(20)];
};
const listings = () =>
  [tmpdir(), "/tmp"].map((dir) => new Set(existsSync(dir) ? readdirSync(dir) : []));

run("node", ["fixtures/make.mjs"]);
rmSync(out, { recursive: true, force: true });

const manifest = fixtureManifest();
const frames = totalFrames(manifest.scenes, manifest.fps);
const before = listings();

const started = Date.now();
run("node", [
  "bin/render.mjs", "--build", build,
  "--out", path.join(out, "fixture.mp4"),
  "--stills", path.join(out, "stills"),
  "--poster", path.join(out, "poster.png"),
]);
const seconds = (Date.now() - started) / 1000;

const info = probe(path.join(out, "fixture.mp4"));
const video = info.streams.find((s) => s.codec_type === "video");
const audio = info.streams.find((s) => s.codec_type === "audio");
assert.equal(video.codec_name, "h264");
assert.equal(video.pix_fmt, "yuv420p");
assert.deepEqual([video.width, video.height], [1920, 1080]);
assert.equal(video.r_frame_rate, "30/1");
assert.equal(audio.codec_name, "aac");
assert.equal(audio.sample_rate, "48000");
assert.ok(
  Math.abs(Number(info.format.duration) - frames / 30) <= 1 / 30 + 0.01,
  `duration ${info.format.duration} s, expected ${frames / 30} s`,
);
// faststart: the moov atom comes before the media data
const head = readFileSync(path.join(out, "fixture.mp4")).subarray(0, 64 * 1024);
assert.ok(
  head.indexOf("moov") !== -1 && head.indexOf("moov") < (head.indexOf("mdat") + 1 || Infinity),
  "moov is not at the start of the file",
);

for (const scene of manifest.scenes) {
  const still = path.join(out, "stills", `${scene.id}.png`);
  assert.deepEqual(pngSize(still), [1920, 1080], still);
}
assert.equal(readdirSync(path.join(out, "stills")).length, manifest.scenes.length);
assert.deepEqual(pngSize(path.join(out, "poster.png")), [1920, 1080]);

// one scene at draft quality: half resolution, the scene's own length
const target = manifest.scenes.find((s) => s.id === "end");
const spans = sceneSpans(manifest.scenes, 30);
run("node", [
  "bin/render.mjs", "--build", build, "--quality", "draft",
  "--scene", target.id, "--out", path.join(out, "scene.mp4"),
]);
const one = probe(path.join(out, "scene.mp4"));
const oneVideo = one.streams.find((s) => s.codec_type === "video");
assert.deepEqual([oneVideo.width, oneVideo.height], [960, 540]);
assert.ok(
  Math.abs(
    Number(one.format.duration) - spans[manifest.scenes.indexOf(target)].frames / 30,
  ) <= 1 / 30 + 0.01,
);

// --- the media box, measured on the pixels of stills ---------------------------
// Solid-colour items of the box aspect must cover exactly the box of theme.json;
// an item of another aspect is fitted inside it. The measure build has no captions
// and no labels, so nothing else is drawn in these colours.
const measure = path.join(root, "out", "measure-build");
rmSync(measure, { recursive: true, force: true });
mkdirSync(path.join(measure, "media"), { recursive: true });
const solid = (rel, colour, w, h, clip) =>
  run("ffmpeg", [
    "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", `color=c=${colour}:s=${w}x${h}:r=30:d=${clip ? 2 : 1}`,
    ...(clip ? ["-an", "-c:v", "libx264", "-pix_fmt", "yuv420p"] : ["-frames:v", "1"]),
    path.join(measure, rel),
  ]);
const MAGENTA = [(r, g, b) => r > 200 && g < 60 && b > 200, "0xFF00FF"];
const GREEN = [(r, g, b) => g > 200 && r < 60 && b < 60, "0x00FF00"];
const cases = [
  // [scene id, items as [type, width, height, [matcher, ffmpeg colour]]]
  ["one-image", [["image", 2400, 833, MAGENTA]]],
  ["one-clip", [["video", 1920, 666, GREEN]]],
  ["two", [["image", 1200, 845, MAGENTA], ["video", 960, 676, GREEN]]],
  ["fitted", [["image", 2400, 1350, MAGENTA]]],
];
const measureScenes = cases.map(([id, items]) => ({
  id,
  chapter: "title",
  kind: "media",
  props: {
    headline: "Boxes",
    items: items.map(([type, width, height], i) => ({
      src: `media/${id}-${i + 1}.${type === "image" ? "png" : "mp4"}`,
      type,
      width,
      height,
      ...(type === "video" ? { seconds: 2 } : {}),
    })),
  },
  audio: null,
  captions: [],
  seconds: 3,
}));
cases.forEach(([id, items]) =>
  items.forEach(([type, w, h, [, colour]], i) =>
    solid(`media/${id}-${i + 1}.${type === "image" ? "png" : "mp4"}`, colour, w, h, type === "video"),
  ),
);
writeFileSync(
  path.join(measure, "manifest.json"),
  JSON.stringify({
    version: 1, draft: "measure", title: "Measure",
    series: { name: "measure", title: "Measure", number: 1 },
    date: "2026-01-01", fps: 30, width: 1920, height: 1080, captions: false,
    chapters: [{ id: "title", label: "Title" }], roadmap: [], scenes: measureScenes,
  }),
);
run("node", ["bin/render.mjs", "--build", measure, "--stills", path.join(out, "measure")]);
const measured = [];
cases.forEach(([id, items]) => {
  const png = readPng(readFileSync(path.join(out, "measure", `${id}.png`)));
  const { boxes } = mediaLayout(items.length);
  items.forEach(([, w, h, [match]], i) => {
    // two items have different colours, so each one's pixels are found on their own
    const own = boundingBox(png, (r, g, b) => match(r, g, b));
    const expected = placeInBox({ width: w, height: h }, boxes[i]).rect;
    assert.ok(own, `${id}[${i}]: no pixels of the item's colour`);
    // two items have different colours, so each box is found on its own
    // exact for box-aspect images; one pixel for H.264 edges and for the fitted item (fractional size)
    const tolerance = items[i][0] === "video" || id === "fitted" ? 1 : 0;
    for (const key of ["x", "y", "w", "h"]) {
      assert.ok(
        Math.abs(own[key] - expected[key]) <= tolerance + 0.5,
        `${id}[${i}] ${key}: measured ${own[key]}, expected ${expected[key]} (${JSON.stringify(own)})`,
      );
    }
    measured.push(`${id}[${i}] ${own.w}x${own.h} at (${own.x}, ${own.y})`);
  });
});
console.log(`media boxes measured: ${measured.join("; ")}`);

// --- text that cannot be one line is refused with its field path ---------------
const refused = (id, mutate, pattern) => {
  const dir = path.join(root, "out", `${id}-build`);
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  symlinkSync(path.join(measure, "media"), path.join(dir, "media"));
  const bad = JSON.parse(readFileSync(path.join(measure, "manifest.json"), "utf8"));
  mutate(bad.scenes[0].props);
  writeFileSync(path.join(dir, "manifest.json"), JSON.stringify(bad));
  const r = spawnSync("node", ["bin/render.mjs", "--build", dir, "--poster", path.join(out, "never.png")], {
    encoding: "utf8", cwd: root,
  });
  assert.equal(r.status, 1, r.stderr);
  assert.match(r.stderr, pattern, r.stderr);
  assert.equal(r.stderr.trim().split("\n").length, 1, "one error line");
  assert.ok(!existsSync(path.join(out, "never.png")));
  rmSync(dir, { recursive: true, force: true });
};
const wordy = "far too long to be one line at any size ".repeat(6).trim();
refused(
  "long-headline",
  (props) => (props.headline = wordy),
  /^error: manifest\.json: scenes\[0\]\.props\.headline: does not fit one line at 40 px/,
);
refused(
  "long-caption",
  (props) => (props.caption = wordy),
  /^error: manifest\.json: scenes\[0\]\.props\.caption: does not fit the line under the media at 24 px/,
);

const after = listings();
const left = after.map((now, i) => [...now].filter((name) => !before[i].has(name)));
assert.deepEqual(left, [[], []], `left behind in the temp folders: ${left.flat().join(", ")}`);

console.log(
  `render ok: ${manifest.scenes.length} scenes, ${info.format.duration} s, ${seconds.toFixed(0)} s to render; files in ${out}`,
);
