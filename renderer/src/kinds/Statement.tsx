import React from "react";
import { useFrameInfo } from "../context.tsx";
import { fitText, steps } from "../layout.ts";
import type { StatementProps } from "../manifest.ts";
import { color } from "../theme.ts";
import { FitText, Reveal } from "./parts.tsx";

const POINT_SIZES = steps(52, 40, 4);
const POINT_LINE = 1.25;

export const Statement: React.FC<{ props: StatementProps }> = ({ props }) => {
  const { box } = useFrameInfo();
  const points = props.points ?? [];
  const pointWidth = box.w - 160 - 36;
  // the points get their height first, the headline takes what is left
  const pointFits = React.useMemo(
    () =>
      points.map((text) =>
        fitText({
          text,
          sizes: POINT_SIZES,
          maxWidth: pointWidth,
          maxLines: 2,
          fontWeight: 500,
          lineHeight: POINT_LINE,
        }),
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [points.join("\n"), pointWidth],
  );
  const pointsHeight = pointFits.reduce(
    (sum, f) => sum + f.height + 36,
    points.length ? 20 : 0,
  );
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
        gap: 48,
      }}
    >
      <Reveal>
        <FitText
          text={props.headline}
          sizes={steps(108, 52, 4)}
          maxWidth={box.w - 160}
          maxLines={4}
          maxHeight={box.h - pointsHeight - 48}
          weight={700}
          lineHeight={1.1}
        />
      </Reveal>
      {points.length ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 36 }}>
          {points.map((text, i) => (
            <Reveal key={i} delay={14 + i * 10}>
              <div
                style={{
                  borderLeft: `6px solid ${color.line}`,
                  paddingLeft: 30,
                }}
              >
                <FitText
                  text={text}
                  sizes={POINT_SIZES}
                  maxWidth={pointWidth}
                  maxLines={2}
                  weight={500}
                  lineHeight={POINT_LINE}
                />
              </div>
            </Reveal>
          ))}
        </div>
      ) : null}
    </div>
  );
};
