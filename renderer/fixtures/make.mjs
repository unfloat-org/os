// Writes a complete build folder (contract C3) for the fixture series into
// fixtures/build (or the folder given as the first argument): manifest.json,
// one WAV per narrated scene, and the generated images and clips. ffmpeg makes
// the media and the audio at fixture time; nothing here is committed.
import { spawnSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { fixtureManifest, fixtureMedia } from "./manifest.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.resolve(process.argv[2] ?? path.join(here, "build"));

const ffmpeg = (args) => {
  const run = spawnSync(
    "ffmpeg",
    ["-hide_banner", "-loglevel", "error", "-y", ...args],
    { encoding: "utf8" },
  );
  if (run.error || run.status !== 0) {
    throw new Error(
      `ffmpeg failed (${args.slice(-1)[0]}): ${run.error?.message ?? run.stderr}`,
    );
  }
};

// Bars on the theme background: one bar group in the accent colour.
const bars = (w, h) => {
  const filters = [];
  const values = [0.22, 0.3, 0.52, 0.5, 0.74, 0.7, 0.88, 0.86];
  values.forEach((v, i) => {
    const bw = Math.round(w / 14);
    const x = Math.round(w * 0.1 + i * (bw * 1.5));
    const bh = Math.round(h * 0.7 * v);
    const colour = i % 2 === 1 ? "0xFF8A4C" : "0x6CC5F0";
    filters.push(`drawbox=x=${x}:y=${Math.round(h * 0.86) - bh}:w=${bw}:h=${bh}:c=${colour}:t=fill`);
  });
  filters.push(`drawbox=x=${Math.round(w * 0.08)}:y=${Math.round(h * 0.86)}:w=${Math.round(w * 0.84)}:h=8:c=0x8E8598:t=fill`);
  return filters.join(",");
};

// A grid of dots in the four data colours.
const dots = (w, h) => {
  const colours = ["0x6CC5F0", "0xFFB454", "0xC9A0FF", "0x7FD8A6"];
  const filters = [];
  for (let r = 0; r < 6; r++) {
    for (let c = 0; c < 10; c++) {
      const size = 70 + ((r * 7 + c * 13) % 5) * 12;
      filters.push(
        `drawbox=x=${Math.round(w * 0.1 + c * w * 0.08)}:y=${Math.round(h * 0.15 + r * h * 0.12)}:w=${size}:h=${size}:c=${colours[(r + c) % 4]}:t=fill`,
      );
    }
  }
  return filters.join(",");
};

rmSync(out, { recursive: true, force: true });
mkdirSync(path.join(out, "audio"), { recursive: true });
mkdirSync(path.join(out, "media"), { recursive: true });

const manifest = fixtureManifest();

for (const [rel, spec] of Object.entries(fixtureMedia)) {
  const file = path.join(out, rel);
  const size = `${spec.width}x${spec.height}`;
  if (spec.kind === "clip") {
    // a box sweeping over the background (sizes relative to the clip), 30 fps, no audio, H.264 yuv420p
    ffmpeg([
      "-f", "lavfi", "-i", `color=c=0x14101A:s=${size}:r=30:d=${spec.seconds}`,
      "-vf", `drawbox=x='${Math.round(spec.width * 0.08)}+mod(t*${Math.round(spec.width * 0.25)},${Math.round(spec.width * 0.6)})':y=${Math.round(spec.height * 0.35)}:w=${Math.round(spec.height * 0.28)}:h=${Math.round(spec.height * 0.28)}:c=${spec.shade}:t=fill`,
      "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", file,
    ]);
  } else {
    const draw = spec.kind === "bars" ? bars(spec.width, spec.height) : dots(spec.width, spec.height);
    ffmpeg([
      "-f", "lavfi", "-i", `color=c=0x14101A:s=${size}:d=1`,
      "-vf", draw, "-frames:v", "1", file,
    ]);
  }
}

for (const scene of manifest.scenes) {
  if (!scene.audio) continue;
  // a quiet tone as long as the narration: 48 kHz mono 16-bit PCM
  ffmpeg([
    "-f", "lavfi", "-i", `sine=frequency=220:sample_rate=48000:duration=${scene.audio.seconds}`,
    "-af", "volume=0.05", "-ac", "1", "-c:a", "pcm_s16le", path.join(out, scene.audio.src),
  ]);
}

writeFileSync(path.join(out, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
// props for `npm run studio`
writeFileSync(path.join(out, "props.json"), `${JSON.stringify({ manifest })}\n`);
console.log(`fixture build folder: ${out}`);
