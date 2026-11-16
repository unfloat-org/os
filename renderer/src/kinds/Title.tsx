import React from "react";
import { useFrameInfo } from "../context.tsx";
import { steps } from "../layout.ts";
import type { TitleProps } from "../manifest.ts";
import { color } from "../theme.ts";
import { FitText, Reveal } from "./parts.tsx";

export const Title: React.FC<{ props: TitleProps; meta: string }> = ({
  props,
  meta,
}) => {
  const { box } = useFrameInfo();
  const maxWidth = box.w - 160;
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
        gap: 40,
      }}
    >
      <Reveal>
        <div
          style={{
            width: 132,
            height: 10,
            borderRadius: 5,
            background: color.accent,
          }}
        />
      </Reveal>
      <Reveal delay={4}>
        <FitText
          text={props.title}
          sizes={steps(136, 72, 8)}
          maxWidth={maxWidth}
          maxLines={3}
          maxHeight={box.h * 0.55}
          weight={700}
          lineHeight={1.06}
        />
      </Reveal>
      {props.subtitle ? (
        <Reveal delay={10}>
          <FitText
            text={props.subtitle}
            sizes={steps(56, 40, 4)}
            maxWidth={maxWidth}
            maxLines={3}
            weight={400}
            lineHeight={1.3}
            color={color.muted}
          />
        </Reveal>
      ) : null}
      <Reveal delay={16}>
        <FitText
          text={meta}
          sizes={[36]}
          maxWidth={maxWidth}
          maxLines={2}
          weight={500}
          lineHeight={1.3}
          color={color.muted}
          style={{ marginTop: 24 }}
        />
      </Reveal>
    </div>
  );
};
