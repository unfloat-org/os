// Renders the fixture on this machine: the video, a still of every scene, the
// poster, and one scene at draft quality; then checks the files with ffprobe and
// that nothing was left in the system temp folders. Needs node, ffmpeg and
// ffprobe; downloads Remotion's browser on the first run. Not run in CI.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { existsSync, readdirSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { fixtureManifest } from "../fixtures/manifest.mjs";
import { sceneSpans, totalFrames } from "../src/timing.ts";

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

const after = listings();
const left = after.map((now, i) => [...now].filter((name) => !before[i].has(name)));
assert.deepEqual(left, [[], []], `left behind in the temp folders: ${left.flat().join(", ")}`);

console.log(
  `render ok: ${manifest.scenes.length} scenes, ${info.format.duration} s, ${seconds.toFixed(0)} s to render; files in ${out}`,
);
