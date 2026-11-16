import React from "react";
import { Easing, interpolate, useCurrentFrame } from "remotion";
import { fitText, type Fit } from "../layout.ts";
import { color, FONT_STACK } from "../theme.ts";

/** Calm entrance: a short fade with a small rise, `delay` frames after the scene starts. */
export const useEntrance = (delay = 0, length = 12) => {
  const frame = useCurrentFrame();
  const t = interpolate(frame - delay, [0, length], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.bezier(0.22, 1, 0.36, 1),
  });
  return { opacity: t, translate: `0px ${(1 - t) * 14}px` };
};

export const Reveal: React.FC<{
  delay?: number;
  style?: React.CSSProperties;
  children: React.ReactNode;
}> = ({ delay = 0, style, children }) => (
  <div style={{ ...useEntrance(delay), ...style }}>{children}</div>
);

type FitProps = {
  text: string;
  sizes: readonly number[];
  maxWidth: number;
  maxLines: number;
  maxHeight?: number;
  weight?: number;
  lineHeight?: number;
  oneLineMin?: number;
  color?: string;
  align?: React.CSSProperties["textAlign"];
  style?: React.CSSProperties;
};

/** Fit computed once per text; text is measured with the loaded font. */
export const useFit = (
  p: Pick<
    FitProps,
    "text" | "sizes" | "maxWidth" | "maxLines" | "maxHeight" | "weight" | "lineHeight" | "oneLineMin"
  >,
): Fit =>
  React.useMemo(
    () =>
      fitText({
        text: p.text,
        sizes: p.sizes,
        maxWidth: p.maxWidth,
        maxLines: p.maxLines,
        maxHeight: p.maxHeight,
        fontWeight: p.weight ?? 600,
        lineHeight: p.lineHeight ?? 1.15,
        oneLineMin: p.oneLineMin,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [p.text, p.maxWidth, p.maxLines, p.maxHeight, p.weight, p.lineHeight, p.oneLineMin, p.sizes.join()],
  );

/** A block of text whose size steps down until it fits; never clipped. */
export const FitText: React.FC<FitProps> = (p) => {
  const fit = useFit(p);
  return (
    <div
      style={{
        fontFamily: FONT_STACK,
        fontSize: fit.fontSize,
        fontWeight: p.weight ?? 600,
        lineHeight: p.lineHeight ?? 1.15,
        color: p.color ?? color.text,
        textAlign: p.align ?? "left",
        textWrap: "balance",
        overflowWrap: "anywhere",
        maxWidth: p.maxWidth,
        letterSpacing: fit.fontSize >= 80 ? "-0.015em" : "-0.005em",
        ...p.style,
      }}
    >
      {p.text}
    </div>
  );
};
