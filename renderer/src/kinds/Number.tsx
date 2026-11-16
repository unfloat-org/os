import React from "react";
import { useFrameInfo } from "../context.tsx";
import type { NumberProps } from "../manifest.ts";
import { NUMBER as N } from "../textfit.ts";
import { color } from "../theme.ts";
import { FitText, Reveal } from "./parts.tsx";

export const NumberScene: React.FC<{ props: NumberProps }> = ({ props }) => {
  const { box } = useFrameInfo();
  const width = box.w - 160; // as numberFit in textfit.ts
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
            sizes={N.headline.sizes}
            maxWidth={width}
            maxLines={N.headline.lines}
            weight={N.headline.weight}
            lineHeight={N.headline.line}
            color={color.muted}
          />
        </Reveal>
      ) : null}
      <Reveal delay={props.headline ? 6 : 0}>
        <FitText
          text={props.value}
          sizes={N.value.sizes}
          maxWidth={width}
          maxLines={N.value.lines}
          weight={N.value.weight}
          lineHeight={N.value.line}
          color={color.accent}
          style={{
            letterSpacing: "-0.03em",
            fontVariantNumeric: "tabular-nums",
          }}
        />
      </Reveal>
      <Reveal delay={12}>
        <FitText
          text={props.label}
          sizes={N.label.sizes}
          maxWidth={width}
          maxLines={N.label.lines}
          weight={N.label.weight}
          lineHeight={N.label.line}
        />
      </Reveal>
      {props.denominator ? (
        <Reveal delay={18}>
          <FitText
            text={props.denominator}
            sizes={N.denominator.sizes}
            maxWidth={width}
            maxLines={N.denominator.lines}
            weight={N.denominator.weight}
            lineHeight={N.denominator.line}
            color={color.muted}
          />
        </Reveal>
      ) : null}
    </div>
  );
};
