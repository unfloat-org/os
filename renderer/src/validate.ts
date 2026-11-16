// Validation of manifest.json: the schema (manifest.ts) and the files it names.
import { existsSync, statSync } from "node:fs";
import path from "node:path";
import {
  fitInputSchema,
  manifestSchema,
  type FitInput,
  type Manifest,
} from "./manifest.ts";

export class ManifestError extends Error {}

const formatPath = (segments: readonly PropertyKey[]): string =>
  segments.reduce<string>(
    (out, s) =>
      typeof s === "number"
        ? `${out}[${s}]`
        : out
          ? `${out}.${String(s)}`
          : String(s),
    "",
  ) || "(manifest)";

/** Parse and check a manifest; throws ManifestError naming the first bad field. */
export const parseManifest = (data: unknown): Manifest => {
  const result = manifestSchema.safeParse(data);
  if (result.success) return result.data;
  const [first, ...rest] = result.error.issues;
  const message = first.message;
  const more = rest.length ? ` (and ${rest.length} more)` : "";
  throw new ManifestError(`${formatPath(first.path)}: ${message}${more}`);
};

/** Parse the input of `--fit`; throws ManifestError naming the first bad field. */
export const parseFitInput = (data: unknown): FitInput => {
  const result = fitInputSchema.safeParse(data);
  if (result.success) return result.data;
  const [first] = result.error.issues;
  throw new ManifestError(`${formatPath(first.path)}: ${first.message}`);
};

/** Every audio and media file the manifest names must exist in the build folder. */
export const checkFiles = (manifest: Manifest, build: string): void => {
  const root = path.resolve(build);
  const need = (rel: string, field: string): void => {
    const file = path.resolve(root, rel);
    if (!file.startsWith(root + path.sep)) {
      throw new ManifestError(`${field}: ${rel} is outside the build folder`);
    }
    if (!existsSync(file) || !statSync(file).isFile()) {
      throw new ManifestError(
        `${field}: file not found in the build folder: ${rel}`,
      );
    }
  };
  manifest.scenes.forEach((scene, i) => {
    if (scene.audio) need(scene.audio.src, `scenes[${i}].audio.src`);
    if (scene.kind === "media") {
      scene.props.items.forEach((item, j) =>
        need(item.src, `scenes[${i}].props.items[${j}].src`),
      );
    }
  });
};
