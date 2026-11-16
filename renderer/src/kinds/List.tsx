import React from "react";
import { useFrameInfo } from "../context.tsx";
import type { ListProps } from "../manifest.ts";
import { LIST as L, listLayout } from "../textfit.ts";
import { color, FONT_STACK } from "../theme.ts";
import { FitText, Reveal } from "./parts.tsx";

export const ListScene: React.FC<{ props: ListProps }> = ({ props }) => {
  const { box } = useFrameInfo();
  const rows = props.rows;
  // sizes from textfit.ts, which fitcheck.ts also uses to refuse text that cannot fit
  const layout = React.useMemo(() => listLayout(props, box), [props, box]);
  const { pairs, markerWidth, colWidth, rowSize, headline } = layout;
  const gap = layout.columnGap;
  const ROW_LINE = L.rowLine;
  const ROW_PAD = L.rowPad;

  const rowStyle: React.CSSProperties = {
    fontFamily: FONT_STACK,
    fontSize: rowSize,
    fontWeight: L.rowWeight,
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
        gap: L.gap,
      }}
    >
      <Reveal>
        <FitText
          text={props.headline}
          sizes={[headline.fontSize]}
          maxWidth={layout.width}
          maxLines={L.headlineLines}
          weight={L.headlineWeight}
          lineHeight={L.headlineLine}
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
