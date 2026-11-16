#!/usr/bin/env node
// The renderer's command line (contract C3):
//   node bin/render.mjs --build <build> --out <file.mp4> [--quality draft|final] [--scene <id>]
//   node bin/render.mjs --build <build> --stills <folder>
//   node bin/render.mjs --build <build> --poster <file.png>
//   node bin/render.mjs --fit <texts.json>
// `--fit` measures the text of a storyboard (fitInputSchema in src/manifest.ts)
// with the fonts the video uses and prints one JSON line, {"problems": [...]},
// for `uo video check`; its bundle and temp files go beside the input file.
// Reads <build>/manifest.json and nothing else. The bundle goes to <build>/bundle
// and every temporary file under TMPDIR (set to <build>/tmp before Remotion
// starts), so a build folder never leaves a copy of its media anywhere else.
// No licence key is ever passed: a render sends no telemetry (Remotion's
// licensing page, https://www.remotion.dev/docs/telemetry).
import {
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  rmSync,
  statSync,
  symlinkSync,
} from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { parseArgs } from "node:util";
import { checkFiles, parseFitInput, parseManifest } from "../src/validate.ts";
import { sceneSpans, stillFrame, totalFrames } from "../src/timing.ts";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const COMPOSITION = "Update";
const USAGE =
  "usage: node bin/render.mjs --build <build> (--out <file.mp4> [--quality draft|final] [--scene <id>] | --stills <folder> | --poster <file.png>) | --fit <texts.json>";

class UsageError extends Error {}

// Nothing is fetched at render time (C3): the browser comes from `npm run setup`
// (`uo video setup`). Remotion calls this only when its browser is missing.
const noDownload = () => {
  throw new Error("the renderer's browser is not installed: run `uo video setup`");
};

const fail = (message) => {
  const line = String(message)
    .split("\n")[0]
    .replace(/^error:\s*/i, "");
  process.stderr.write(`error: ${line}\n`);
  process.exit(1);
};

const options = () => {
  let parsed;
  try {
    parsed = parseArgs({
      args: process.argv.slice(2),
      options: {
        build: { type: "string" },
        out: { type: "string" },
        quality: { type: "string", default: "final" },
        scene: { type: "string" },
        stills: { type: "string" },
        poster: { type: "string" },
        fit: { type: "string" },
        help: { type: "boolean", short: "h" },
      },
      allowPositionals: false,
    });
  } catch (err) {
    throw new UsageError(`${err.message}. ${USAGE}`);
  }
  const v = parsed.values;
  if (v.help) {
    process.stdout.write(`${USAGE}\n`);
    process.exit(0);
  }
  if (v.fit) {
    if (v.build || v.out || v.stills || v.poster || v.scene) {
      throw new UsageError(`--fit takes no other option. ${USAGE}`);
    }
    return v;
  }
  if (!v.build) throw new UsageError(`--build is required. ${USAGE}`);
  if (!v.out && !v.stills && !v.poster) {
    throw new UsageError(
      `one of --out, --stills, --poster is required. ${USAGE}`,
    );
  }
  if (!["draft", "final"].includes(v.quality)) {
    throw new UsageError(
      `--quality must be draft or final, not "${v.quality}"`,
    );
  }
  if (v.scene && !v.out) throw new UsageError("--scene needs --out");
  return v;
};

const readManifest = (build) => {
  const file = path.join(build, "manifest.json");
  if (!existsSync(file)) throw new Error(`${file}: not found`);
  let data;
  try {
    data = JSON.parse(readFileSync(file, "utf8"));
  } catch (err) {
    throw new Error(`manifest.json is not valid JSON: ${err.message}`);
  }
  try {
    const manifest = parseManifest(data);
    checkFiles(manifest, build);
    return manifest;
  } catch (err) {
    throw new Error(`manifest.json: ${err.message}`);
  }
};

/**
 * The public folder of the bundle: the build folder's audio/ and media/, linked
 * (not copied) into <build>/tmp, because the build folder also holds the bundle
 * and the temp files themselves. Remotion keeps the links in the bundle.
 */
const stagePublicDir = (build) => {
  const dir = path.join(build, "tmp", "public");
  rmSync(dir, { recursive: true, force: true });
  mkdirSync(dir, { recursive: true });
  for (const name of ["audio", "media"]) {
    const target = path.join(build, name);
    if (existsSync(target)) symlinkSync(target, path.join(dir, name));
  }
  return dir;
};

const progress = (label) => {
  let last = -1;
  return (fraction) => {
    const pct = Math.floor(fraction * 10) * 10;
    if (pct !== last) {
      last = pct;
      process.stdout.write(`${label} ${pct}%\n`);
    }
  };
};

/** `--fit`: bundle, start the browser, measure every text; prints {"problems": [...]}. */
const fit = async (file) => {
  const input = path.resolve(file);
  if (!existsSync(input)) throw new Error(`${input}: not found`);
  let data;
  try {
    data = JSON.parse(readFileSync(input, "utf8"));
  } catch (err) {
    throw new Error(
      `${path.basename(input)} is not valid JSON: ${err.message}`,
    );
  }
  let texts;
  try {
    texts = parseFitInput(data);
  } catch (err) {
    throw new Error(`${path.basename(input)}: ${err.message}`);
  }
  const dir = path.dirname(input);
  const tmp = path.join(dir, "tmp");
  mkdirSync(tmp, { recursive: true });
  for (const key of ["TMPDIR", "TMP", "TEMP"]) process.env[key] = tmp;
  const { bundle } = await import("@remotion/bundler");
  const { selectComposition, openBrowser, ensureBrowser } =
    await import("@remotion/renderer");
  await ensureBrowser({ logLevel: "error", onBrowserDownload: noDownload });
  const bundleDir = path.join(dir, "bundle");
  const publicDir = path.join(tmp, "public");
  rmSync(bundleDir, { recursive: true, force: true });
  mkdirSync(publicDir, { recursive: true });
  const serveUrl = await bundle({
    entryPoint: path.join(root, "src", "index.ts"),
    rootDir: root,
    outDir: bundleDir,
    publicDir,
  });
  const browser = await openBrowser("chrome", { logLevel: "error" });
  try {
    const composition = await selectComposition({
      serveUrl,
      id: "Fit",
      inputProps: { input: texts },
      logLevel: "error",
      puppeteerInstance: browser,
    });
    process.stdout.write(
      `${JSON.stringify({ problems: composition.props.problems ?? [] })}\n`,
    );
  } finally {
    await browser.close({ silent: true }).catch(() => {});
  }
};

const main = async () => {
  const opts = options();
  if (opts.fit) return fit(opts.fit);
  const build = path.resolve(opts.build);
  if (!existsSync(build) || !statSync(build).isDirectory()) {
    throw new Error(`build folder not found: ${build}`);
  }
  const manifest = readManifest(build);

  // Everything Remotion and its browser write goes here, before they start.
  const tmp = path.join(build, "tmp");
  mkdirSync(tmp, { recursive: true });
  for (const key of ["TMPDIR", "TMP", "TEMP"]) process.env[key] = tmp;

  const { bundle } = await import("@remotion/bundler");
  const renderer = await import("@remotion/renderer");
  const {
    selectComposition,
    renderMedia,
    renderStill,
    openBrowser,
    ensureBrowser,
  } = renderer;

  await ensureBrowser({ logLevel: "error", onBrowserDownload: noDownload });

  const bundleDir = path.join(build, "bundle");
  rmSync(bundleDir, { recursive: true, force: true });
  const publicDir = stagePublicDir(build);
  const serveUrl = await bundle({
    entryPoint: path.join(root, "src", "index.ts"),
    rootDir: root,
    outDir: bundleDir,
    publicDir,
    onSymlinkDetected: () => {},
  });
  rmSync(publicDir, { recursive: true, force: true });
  process.stdout.write(
    `bundle: ${path.relative(process.cwd(), bundleDir) || bundleDir}\n`,
  );

  const inputProps = { manifest };
  const common = { serveUrl, inputProps, logLevel: "error" };
  const browser = await openBrowser("chrome", { logLevel: "error" });
  try {
    const composition = await selectComposition({
      ...common,
      id: COMPOSITION,
      puppeteerInstance: browser,
    });
    // text that cannot be one line is decided in the browser, where Inter is measured
    if (composition.props.fitError) {
      throw new Error(`manifest.json: ${composition.props.fitError}`);
    }
    const spans = sceneSpans(manifest.scenes, manifest.fps);
    if (
      composition.durationInFrames !==
      totalFrames(manifest.scenes, manifest.fps)
    ) {
      throw new Error(
        "composition length differs from the sum of scene lengths",
      );
    }

    if (opts.stills) {
      const dir = path.resolve(opts.stills);
      mkdirSync(dir, { recursive: true });
      for (const [i, scene] of manifest.scenes.entries()) {
        await renderStill({
          ...common,
          composition,
          puppeteerInstance: browser,
          frame: stillFrame(spans[i]),
          imageFormat: "png",
          output: path.join(dir, `${scene.id}.png`),
        });
      }
      process.stdout.write(`stills: ${manifest.scenes.length} in ${dir}\n`);
    }

    if (opts.poster) {
      const file = path.resolve(opts.poster);
      mkdirSync(path.dirname(file), { recursive: true });
      await renderStill({
        ...common,
        composition,
        puppeteerInstance: browser,
        frame: stillFrame(spans[0]),
        imageFormat: "png",
        output: file,
      });
      process.stdout.write(`poster: ${file}\n`);
    }

    if (opts.out) {
      const file = path.resolve(opts.out);
      mkdirSync(path.dirname(file), { recursive: true });
      let frameRange = null;
      if (opts.scene) {
        const at = manifest.scenes.findIndex((s) => s.id === opts.scene);
        if (at < 0)
          throw new UsageError(
            `--scene: no scene "${opts.scene}" in the manifest`,
          );
        frameRange = [spans[at].from, spans[at].from + spans[at].frames - 1];
      }
      const draft = opts.quality === "draft";
      // write beside the target and rename, so a failed render leaves no partial file
      const partial = `${file}.partial.mp4`;
      await renderMedia({
        ...common,
        composition,
        puppeteerInstance: browser,
        codec: "h264",
        pixelFormat: "yuv420p",
        audioCodec: "aac",
        audioBitrate: "192k",
        sampleRate: 48000,
        crf: draft ? 28 : 16,
        x264Preset: draft ? "veryfast" : "medium",
        imageFormat: "jpeg",
        jpegQuality: draft ? 80 : 95,
        scale: draft ? 0.5 : 1,
        concurrency: 4,
        frameRange,
        outputLocation: partial,
        overwrite: true,
        enforceAudioTrack: true,
        onProgress: ({ progress: p }) => progressVideo(p),
        ffmpegOverride: ({ args }) => withFaststart(args),
      });
      renameSync(partial, file);
      process.stdout.write(`video: ${file}\n`);
    }
  } finally {
    await browser.close({ silent: true }).catch(() => {});
  }
};

const progressVideo = progress("render");

/** Put the moov atom first (movflags +faststart) on the final mux. */
const withFaststart = (args) => {
  if (args.includes("-movflags")) return args;
  const out = args.slice(0, -1);
  return [...out, "-movflags", "+faststart", args[args.length - 1]];
};

main().catch((err) => {
  if (process.env.UO_RENDER_DEBUG) console.error(err);
  fail(err instanceof Error ? err.message : err);
});
