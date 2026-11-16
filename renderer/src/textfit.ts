// How the text of each kind is sized: one source for the components that draw it
// and for fitcheck.ts, which refuses text that cannot fit. Each function takes the
// kind's props and the content box and returns the fits the component draws with.
// Text is measured with Inter, so these run only where the fonts are loaded.
import { countLines, fitText, steps, type Box, type Fit } from "./layout.ts";

// statement ------------------------------------------------------------------

export const STATEMENT = {
  headlineSizes: steps(108, 52, 4),
  headlineLines: 4,
  headlineWeight: 700,
  headlineLine: 1.1,
  pointSizes: steps(52, 40, 4),
  pointLines: 2,
  pointWeight: 500,
  pointLine: 1.25,
  gap: 48,
  pointGap: 36,
} as const;

export type StatementLayout = {
  width: number;
  pointWidth: number;
  points: Fit[];
  headline: Fit;
  headlineMaxHeight: number;
};

/** The points get their height first; the headline takes what is left. */
export const statementLayout = (
  props: { headline: string; points?: readonly string[] },
  box: Box,
): StatementLayout => {
  const S = STATEMENT;
  const width = box.w - 160;
  const pointWidth = width - 36;
  const points = (props.points ?? []).map((text) =>
    fitText({
      text,
      sizes: S.pointSizes,
      maxWidth: pointWidth,
      maxLines: S.pointLines,
      fontWeight: S.pointWeight,
      lineHeight: S.pointLine,
    }),
  );
  const pointsHeight = points.reduce(
    (sum, f) => sum + f.height + S.pointGap,
    points.length ? 20 : 0,
  );
  const headlineMaxHeight = box.h - pointsHeight - S.gap;
  const headline = fitText({
    text: props.headline,
    sizes: S.headlineSizes,
    maxWidth: width,
    maxLines: S.headlineLines,
    maxHeight: headlineMaxHeight,
    fontWeight: S.headlineWeight,
    lineHeight: S.headlineLine,
  });
  return { width, pointWidth, points, headline, headlineMaxHeight };
};

// list -----------------------------------------------------------------------

export const LIST = {
  headlineSizes: steps(84, 52, 4),
  headlineLines: 2,
  headlineOneLineMin: 64,
  headlineWeight: 700,
  headlineLine: 1.1,
  rowSizes: steps(60, 40, 4),
  rowLines: 2,
  rowWeight: 500,
  rowLine: 1.22,
  rowPad: 22,
  gap: 44,
  titleRow: 62,
} as const;

type Row = string | { left: string; right: string };

export const rowTexts = (row: Row): string[] =>
  typeof row === "string" ? [row] : [row.left, row.right];

export type ListLayout = {
  width: number;
  pairs: boolean;
  markerWidth: number;
  columnGap: number;
  colWidth: number;
  headline: Fit;
  /** One size for every row, so the rows read as a set. */
  rowSize: number;
  /** Every row within two lines and all rows within the height, at `rowSize`. */
  rowsFit: boolean;
  /** Field paths (`rows[i]`, `rows[i].left`) of texts that need more than two lines at the smallest size. */
  longRows: string[];
};

export const listLayout = (
  props: {
    headline: string;
    style: "bullets" | "numbered" | "pairs";
    rows: readonly Row[];
    left_title?: string;
    right_title?: string;
  },
  box: Box,
): ListLayout => {
  const L = LIST;
  const width = box.w - 160;
  const pairs = props.style === "pairs";
  const markerWidth = props.style === "numbered" ? 84 : 52;
  const columnGap = pairs ? 56 : 0;
  const colWidth = pairs ? (width - columnGap) / 2 : width - markerWidth;
  const headline = fitText({
    text: props.headline,
    sizes: L.headlineSizes,
    maxWidth: width,
    maxLines: L.headlineLines,
    fontWeight: L.headlineWeight,
    lineHeight: L.headlineLine,
    oneLineMin: L.headlineOneLineMin,
  });
  const titleRow =
    pairs && (props.left_title || props.right_title) ? L.titleRow : 0;
  const available = box.h - headline.height - L.gap - titleRow;
  const lines = (text: string, size: number): number =>
    countLines(text, size, L.rowWeight, colWidth * 0.97);
  const heightAt = (size: number): number =>
    props.rows.reduce(
      (sum, row) =>
        sum +
        Math.max(...rowTexts(row).map((t) => lines(t, size))) *
          size *
          L.rowLine +
        L.rowPad * 2,
      0,
    );
  const all = props.rows.flatMap(rowTexts);
  const smallest = L.rowSizes[L.rowSizes.length - 1];
  let rowSize: number = smallest;
  let rowsFit = false;
  for (const size of L.rowSizes) {
    if (
      all.every((t) => lines(t, size) <= L.rowLines) &&
      heightAt(size) <= available
    ) {
      rowSize = size;
      rowsFit = true;
      break;
    }
  }
  const longRows = props.rows.flatMap((row, i) =>
    typeof row === "string"
      ? lines(row, smallest) > L.rowLines
        ? [`rows[${i}]`]
        : []
      : (["left", "right"] as const)
          .filter((side) => lines(row[side], smallest) > L.rowLines)
          .map((side) => `rows[${i}].${side}`),
  );
  return {
    width,
    pairs,
    markerWidth,
    columnGap,
    colWidth,
    headline,
    rowSize,
    rowsFit,
    longRows,
  };
};

// number ---------------------------------------------------------------------

export const NUMBER = {
  headline: { sizes: steps(72, 48, 4), lines: 2, weight: 600, line: 1.15 },
  value: { sizes: steps(360, 160, 20), lines: 1, weight: 700, line: 1 },
  label: { sizes: steps(68, 44, 4), lines: 2, weight: 600, line: 1.18 },
  denominator: { sizes: steps(48, 40, 4), lines: 2, weight: 400, line: 1.25 },
} as const;

export type NumberField = keyof typeof NUMBER;

export const numberFit = (field: NumberField, text: string, box: Box): Fit => {
  const spec = NUMBER[field];
  return fitText({
    text,
    sizes: spec.sizes,
    maxWidth: box.w - 160,
    maxLines: spec.lines,
    fontWeight: spec.weight,
    lineHeight: spec.line,
  });
};

// roadmap --------------------------------------------------------------------

export const ROADMAP = {
  headlineSizes: steps(92, 52, 4),
  headlineLines: 5,
  headlineWeight: 700,
  headlineLine: 1.1,
  gap: 64,
} as const;

export const roadmapListWidth = (hasHeadline: boolean): number =>
  hasHeadline ? 880 : 1100;

export const roadmapHeadlineWidth = (box: Box): number =>
  box.w - roadmapListWidth(true) - ROADMAP.gap - 160;

export const roadmapHeadlineFit = (text: string, box: Box): Fit =>
  fitText({
    text,
    sizes: ROADMAP.headlineSizes,
    maxWidth: roadmapHeadlineWidth(box),
    maxLines: ROADMAP.headlineLines,
    maxHeight: box.h,
    fontWeight: ROADMAP.headlineWeight,
    lineHeight: ROADMAP.headlineLine,
  });
