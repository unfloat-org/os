import React from "react";
import { useFrameInfo } from "../context.tsx";
import { steps } from "../layout.ts";
import type { NumberProps } from "../manifest.ts";
import { color } from "../theme.ts";
import { FitText, Reveal } from "./parts.tsx";

export const NumberScene: React.FC<{ props: NumberProps }> = ({ props }) => {
  const { box } = useFrameInfo();
  const width = box.w - 160;
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
        gap: 28,
      }}
    >
      {props.headline ? (
        <Reveal>
          <FitText
            text={props.headline}
            sizes={steps(72, 48, 4)}
            maxWidth={width}
            maxLines={2}
            weight={600}
            lineHeight={1.15}
            color={color.muted}
          />
        </Reveal>
      ) : null}
      <Reveal delay={props.headline ? 6 : 0}>
        <FitText
          text={props.value}
          sizes={steps(360, 160, 20)}
          maxWidth={width}
          maxLines={1}
          weight={700}
          lineHeight={1}
          color={color.accent}
          style={{ letterSpacing: "-0.03em", fontVariantNumeric: "tabular-nums" }}
        />
      </Reveal>
      <Reveal delay={12}>
        <FitText
          text={props.label}
          sizes={steps(68, 44, 4)}
          maxWidth={width}
          maxLines={2}
          weight={600}
          lineHeight={1.18}
        />
      </Reveal>
      {props.denominator ? (
        <Reveal delay={18}>
          <FitText
            text={props.denominator}
            sizes={steps(48, 40, 4)}
            maxWidth={width}
            maxLines={2}
            weight={400}
            lineHeight={1.25}
            color={color.muted}
          />
        </Reveal>
      ) : null}
    </div>
  );
};
