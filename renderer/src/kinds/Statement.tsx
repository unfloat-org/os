import React from "react";
import { useFrameInfo } from "../context.tsx";
import type { StatementProps } from "../manifest.ts";
import { STATEMENT as S, statementLayout } from "../textfit.ts";
import { color } from "../theme.ts";
import { FitText, Reveal } from "./parts.tsx";

export const Statement: React.FC<{ props: StatementProps }> = ({ props }) => {
  const { box } = useFrameInfo();
  const points = props.points ?? [];
  // sizes from textfit.ts, which fitcheck.ts also uses to refuse text that cannot fit
  const layout = React.useMemo(
    () => statementLayout(props, box),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [props.headline, points.join("\n"), box.w, box.h],
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
        gap: S.gap,
      }}
    >
      <Reveal>
        <FitText
          text={props.headline}
          sizes={[layout.headline.fontSize]}
          maxWidth={layout.width}
          maxLines={S.headlineLines}
          weight={S.headlineWeight}
          lineHeight={S.headlineLine}
        />
      </Reveal>
      {points.length ? (
        <div
          style={{ display: "flex", flexDirection: "column", gap: S.pointGap }}
        >
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
                  sizes={[layout.points[i].fontSize]}
                  maxWidth={layout.pointWidth}
                  maxLines={S.pointLines}
                  weight={S.pointWeight}
                  lineHeight={S.pointLine}
                />
              </div>
            </Reveal>
          ))}
        </div>
      ) : null}
    </div>
  );
};
