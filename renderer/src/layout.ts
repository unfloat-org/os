// Frame geometry and text fitting. The frame size comes from theme.json (layout.frame).
import { measureText } from "@remotion/layout-utils";
import { FRAME_H, FRAME_W } from "./mediabox.ts";
import { FONT_FAMILY } from "./theme.ts";

export const WIDTH = FRAME_W;
export const HEIGHT = FRAME_H;
export const MARGIN_X = 96;
/** Height reserved for the caption band; media never enters it. */
export const CAPTION_BAND = 196;

export type Box = { x: number; y: number; w: number; h: number };

/** The area every kind draws in: below the frame furniture, above the caption band. */
export const contentBox = (captions: boolean): Box => {
  const top = 116;
  const bottom = captions ? HEIGHT - CAPTION_BAND - 12 : HEIGHT - 72;
  return { x: MARGIN_X, y: top, w: WIDTH - 2 * MARGIN_X, h: bottom - top };
};

export type TextStyle = {
  fontWeight: number;
  lineHeight: number;
};

/** Number of lines `text` takes at `fontSize` in `maxWidth`, wrapping greedily at spaces. */
export const countLines = (
  text: string,
  fontSize: number,
  fontWeight: number,
  maxWidth: number,
): number => {
  const width = (s: string): number =>
    measureText({
      text: s,
      fontFamily: FONT_FAMILY,
      fontSize,
      fontWeight,
      validateFontIsLoaded: true,
    }).width;
  let lines = 0;
  for (const paragraph of text.split("\n")) {
    let line = "";
    let count = 1;
    for (const word of paragraph.split(/\s+/).filter(Boolean)) {
      const next = line ? `${line} ${word}` : word;
      if (width(next) <= maxWidth) {
        line = next;
        continue;
      }
      if (line) count += 1;
      const w = width(word);
      // a single word wider than the box breaks over several lines
      count += Math.max(0, Math.ceil(w / maxWidth) - 1);
      line = word;
    }
    lines += count;
  }
  return lines;
};

/** `fits`: the text is within its lines and height at `fontSize`; false when even the smallest size is too big. */
export type Fit = {
  fontSize: number;
  lines: number;
  height: number;
  fits: boolean;
};

/**
 * The largest of `sizes` (descending) at which `text` fits in `maxWidth` within
 * `maxLines` lines and `maxHeight`. When none fits, the smallest size is used
 * (text is never clipped) and `fits` is false: fitcheck.ts refuses such text.
 */
export const fitText = (opts: {
  text: string;
  sizes: readonly number[];
  maxWidth: number;
  maxLines: number;
  maxHeight?: number;
  fontWeight: number;
  lineHeight: number;
  /** Prefer one line: sizes from here up are tried with a single line first. */
  oneLineMin?: number;
}): Fit => {
  if (opts.oneLineMin !== undefined) {
    const one = fitText({
      ...opts,
      oneLineMin: undefined,
      sizes: opts.sizes.filter((s) => s >= (opts.oneLineMin ?? 0)),
      maxLines: 1,
    });
    if (one.lines === 1 && one.fontSize >= opts.oneLineMin) return one;
  }
  let fit: Fit = { fontSize: 0, lines: 0, height: 0, fits: false };
  for (const size of opts.sizes) {
    const lines = countLines(
      opts.text,
      size,
      opts.fontWeight,
      // margin for the difference between measuring and the browser's layout
      opts.maxWidth * 0.97,
    );
    const height = lines * size * opts.lineHeight;
    const fits =
      lines <= opts.maxLines &&
      (opts.maxHeight === undefined || height <= opts.maxHeight);
    fit = { fontSize: size, lines, height, fits };
    if (fits) return fit;
  }
  return fit;
};

/** `src` scaled to fit `box` without leaving it; upscaled by at most `maxScale`. */
export const fitRect = (
  srcW: number,
  srcH: number,
  box: { w: number; h: number },
  maxScale = 2,
): { w: number; h: number; scale: number } => {
  const scale = Math.min(box.w / srcW, box.h / srcH, maxScale);
  return { w: srcW * scale, h: srcH * scale, scale };
};

/** Descending size steps from `from` to `to`, `step` apart. */
export const steps = (from: number, to: number, step: number): number[] => {
  const out: number[] = [];
  for (let s = from; s >= to; s -= step) out.push(s);
  return out;
};
