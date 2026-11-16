// The media box (C7): where the items of a media scene are drawn, in frame
// pixels. The sizes come from theme.json ("layout"), which reporting
// repositories read by path to export figures and clips at the box aspect, so
// text in a figure lands at a known size in the frame. Pure arithmetic: no
// React, no browser, so `npm test` checks it.
import theme from "../theme.json" with { type: "json" };

const pair = (value: readonly number[], name: string): [number, number] => {
  if (value.length !== 2 || value.some((n) => !(n > 0))) {
    throw new Error(`theme.json: layout.${name} must be [width, height] in pixels`);
  }
  return [value[0], value[1]];
};

export const layout = {
  frame: pair(theme.layout.frame, "frame"),
  media: {
    one: pair(theme.layout.media.one, "media.one"),
    two: pair(theme.layout.media.two, "media.two"),
    gap: theme.layout.media.gap,
  },
};
export const FRAME_W = layout.frame[0];
export const FRAME_H = layout.frame[1];

export type Rect = { x: number; y: number; w: number; h: number };
export type MediaCount = 1 | 2;

/** Rows of a media scene, top to bottom. The media box sits between the two rows. */
export const HEADLINE_TOP = 116;
export const HEADLINE_ROW = 76;
const HEADLINE_GAP = 16;
export const INFO_ROW = 40;
const INFO_GAP = 8;

/** Headline: one line, its size steps down to fit (a text that does not fit at the last step is refused). */
export const HEADLINE_SIZES = [68, 64, 60, 56, 52, 48, 44, 40] as const;
export const HEADLINE_WEIGHT = 700;
export const HEADLINE_LINE_HEIGHT = 1.1;

/** Info line under the box: item labels and the caption, muted, one line of at most INFO_ROW px. */
export const INFO_SIZES = [32, 30, 28, 26, 24] as const;
export const INFO_WEIGHT = 500;
export const INFO_LINE_HEIGHT = 1.25;
export const INFO_SEPARATOR = "  ·  ";

/** An item whose aspect differs from its box's by at most this share fills the box exactly. */
export const ASPECT_TOLERANCE = 0.01;

const boxSize = (count: MediaCount): [number, number] =>
  count === 1 ? layout.media.one : layout.media.two;

/** Width of the row of boxes: the headline and the info line span it. */
export const mediaWidth = (count: MediaCount): number =>
  boxSize(count)[0] * count + layout.media.gap * (count - 1);

const left = (count: MediaCount): number => (FRAME_W - mediaWidth(count)) / 2;
const mediaTop = HEADLINE_TOP + HEADLINE_ROW + HEADLINE_GAP;

export type MediaLayout = {
  headline: Rect;
  boxes: Rect[];
  info: Rect;
};

/** Positions of the headline row, the media boxes and the info line, in frame pixels. */
export const mediaLayout = (count: MediaCount): MediaLayout => {
  const [w, h] = boxSize(count);
  const x0 = left(count);
  return {
    headline: { x: x0, y: HEADLINE_TOP, w: mediaWidth(count), h: HEADLINE_ROW },
    boxes: Array.from({ length: count }, (_, i) => ({
      x: x0 + i * (w + layout.media.gap),
      y: mediaTop,
      w,
      h,
    })),
    info: { x: x0, y: mediaTop + h + INFO_GAP, w: mediaWidth(count), h: INFO_ROW },
  };
};

export type Placement = {
  /** Where the part of the item that is shown lies, in frame pixels. */
  rect: Rect;
  /** Frame pixels per item pixel, horizontally and vertically. */
  scaleX: number;
  scaleY: number;
  /** True when the item filled the box exactly (its aspect is the box's). */
  filled: boolean;
};

/**
 * Places the shown part of an item (the whole of it, or its `zoom` rectangle) in
 * `box`: an item of the box's aspect fills the box exactly; any other aspect is
 * fitted inside it, centred, never cropped.
 */
export const placeInBox = (
  item: {
    width: number;
    height: number;
    zoom?: readonly [number, number, number, number];
  },
  box: Rect,
): Placement => {
  const [, , zw, zh] = item.zoom ?? [0, 0, 1, 1];
  const srcW = item.width * zw;
  const srcH = item.height * zh;
  const filled =
    Math.abs(srcW / srcH / (box.w / box.h) - 1) <= ASPECT_TOLERANCE;
  const fit = Math.min(box.w / srcW, box.h / srcH);
  const w = filled ? box.w : srcW * fit;
  const h = filled ? box.h : srcH * fit;
  return {
    rect: { x: box.x + (box.w - w) / 2, y: box.y + (box.h - h) / 2, w, h },
    scaleX: w / srcW,
    scaleY: h / srcH,
    filled,
  };
};

export type InfoCell = {
  text: string;
  rect: Rect;
  /** The manifest field to name when the text does not fit its cell on one line. */
  field: string;
};

/**
 * The cells of the one muted line under the boxes. One item: its label and the
 * caption joined. Two items: each label under its own box; with a caption, the
 * line is one text, the labels in order and then the caption, across both boxes
 * (a caption is never dropped, and the line stays one line).
 */
export const infoCells = (
  items: readonly { label?: string }[],
  caption: string | undefined,
): InfoCell[] => {
  const count = items.length as MediaCount;
  const { boxes, info } = mediaLayout(count);
  const labels = items.map((item) => item.label?.trim() ?? "");
  const joined = [...labels, caption ?? ""].filter(Boolean).join(INFO_SEPARATOR);
  if (count === 1 || caption) {
    if (!joined) return [];
    return [{ text: joined, rect: info, field: caption ? "caption" : "items[0].label" }];
  }
  return labels.flatMap((text, i) =>
    text
      ? [{ text, rect: { ...info, x: boxes[i].x, w: boxes[i].w }, field: `items[${i}].label` }]
      : [],
  );
};
