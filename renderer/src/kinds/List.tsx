import React from "react";
import { useFrameInfo } from "../context.tsx";
import { countLines, steps } from "../layout.ts";
import type { ListProps } from "../manifest.ts";
import { color, FONT_STACK } from "../theme.ts";
import { FitText, Reveal, useFit } from "./parts.tsx";

const ROW_SIZES = steps(60, 40, 4);
const ROW_LINE = 1.22;
const ROW_PAD = 22;

const rowText = (row: ListProps["rows"][number]): string[] =>
  typeof row === "string" ? [row] : [row.left, row.right];

export const ListScene: React.FC<{ props: ListProps }> = ({ props }) => {
  const { box } = useFrameInfo();
  const pairs = props.style === "pairs";
  const markerWidth = props.style === "numbered" ? 84 : 52;
  const gap = pairs ? 56 : 0;
  const colWidth = pairs ? (box.w - 160 - gap) / 2 : box.w - 160 - markerWidth;
  const rows = props.rows;

  const headline = useFit({
    text: props.headline,
    sizes: steps(84, 52, 4),
    maxWidth: box.w - 160,
    maxLines: 2,
    weight: 700,
    lineHeight: 1.1,
    oneLineMin: 64,
  });
  const titleRow = pairs && (props.left_title || props.right_title) ? 62 : 0;
  const available = box.h - headline.height - 44 - titleRow;

  // one size for all rows of a list, so the rows read as a set: the largest
  // at which every row takes at most two lines and the rows fit the height
  const rowSize = React.useMemo(() => {
    const all = rows.flatMap(rowText);
    const heightAt = (size: number): number =>
      rows.reduce(
        (sum, row) =>
          sum +
          Math.max(
            ...rowText(row).map((text) =>
              countLines(text, size, 500, colWidth * 0.97),
            ),
          ) *
            size *
            ROW_LINE +
          ROW_PAD * 2,
        0,
      );
    let size = ROW_SIZES[ROW_SIZES.length - 1];
    for (const candidate of ROW_SIZES) {
      const twoLines = all.every(
        (text) => countLines(text, candidate, 500, colWidth * 0.97) <= 2,
      );
      if (twoLines && heightAt(candidate) <= available) {
        size = candidate;
        break;
      }
    }
    return size;
  }, [rows, colWidth, available]);

  const rowStyle: React.CSSProperties = {
    fontFamily: FONT_STACK,
    fontSize: rowSize,
    fontWeight: 500,
    lineHeight: ROW_LINE,
    color: color.text,
    overflowWrap: "anywhere",
    textWrap: "pretty",
  };

  return (
    <div
      style={{
        position: "absolute",
        left: box.x,
        top: box.y,
        width: box.w,
        height: box.h,
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        gap: 44,
      }}
    >
      <Reveal>
        <FitText
          text={props.headline}
          sizes={[headline.fontSize]}
          maxWidth={box.w - 160}
          maxLines={2}
          weight={700}
          lineHeight={1.1}
        />
      </Reveal>
      <div style={{ display: "flex", flexDirection: "column" }}>
        {pairs && (props.left_title || props.right_title) ? (
          <Reveal delay={6}>
            <div
              style={{
                display: "flex",
                gap,
                paddingBottom: 14,
                borderBottom: `3px solid ${color.line}`,
                fontFamily: FONT_STACK,
                fontSize: 34,
                fontWeight: 600,
                letterSpacing: "0.06em",
                textTransform: "uppercase",
                color: color.muted,
              }}
            >
              <div style={{ width: colWidth }}>{props.left_title}</div>
              <div style={{ width: colWidth }}>{props.right_title}</div>
            </div>
          </Reveal>
        ) : null}
        {rows.map((row, i) => (
          <Reveal key={i} delay={10 + i * 8}>
            <div
              style={{
                display: "flex",
                alignItems: "flex-start",
                gap: pairs ? gap : 0,
                padding: `${ROW_PAD}px 0`,
                borderBottom: pairs ? `2px solid ${color.line}` : "none",
              }}
            >
              {pairs && typeof row !== "string" ? (
                <>
                  <div style={{ ...rowStyle, width: colWidth }}>{row.left}</div>
                  <div
                    style={{ ...rowStyle, width: colWidth, color: color.muted }}
                  >
                    {row.right}
                  </div>
                </>
              ) : (
                <>
                  <div
                    style={{
                      width: markerWidth,
                      flexShrink: 0,
                      display: "flex",
                      alignItems: "center",
                      height: rowSize * ROW_LINE,
                      color: color.muted,
                      fontFamily: FONT_STACK,
                      fontWeight: 600,
                      fontSize: rowSize,
                      fontVariantNumeric: "tabular-nums",
                    }}
                  >
                    {props.style === "numbered" ? (
                      `${i + 1}`
                    ) : (
                      <div
                        style={{
                          width: 16,
                          height: 16,
                          borderRadius: 8,
                          background: color.muted,
                        }}
                      />
                    )}
                  </div>
                  <div style={{ ...rowStyle, width: colWidth }}>
                    {typeof row === "string" ? row : row.left}
                  </div>
                </>
              )}
            </div>
          </Reveal>
        ))}
      </div>
    </div>
  );
};
