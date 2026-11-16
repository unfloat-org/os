import React from "react";
import { useFrameInfo } from "../context.tsx";
import type { RoadmapProps } from "../manifest.ts";
import {
  ROADMAP as R,
  roadmapHeadlineWidth,
  roadmapListWidth,
} from "../textfit.ts";
import { color, FONT_STACK } from "../theme.ts";
import { FitText, Reveal } from "./parts.tsx";

export const RoadmapScene: React.FC<{ props: RoadmapProps }> = ({ props }) => {
  const { box, manifest } = useFrameInfo();
  const list = manifest.roadmap;
  const done = new Set(props.done);
  const hasHeadline = Boolean(props.headline);
  const listWidth = roadmapListWidth(hasHeadline);
  const rowHeight = Math.min(128, Math.floor((box.h - 32) / list.length));
  const fontSize = Math.min(64, Math.floor(rowHeight * 0.5));
  const node = Math.min(60, Math.floor(rowHeight * 0.46));

  return (
    <div
      style={{
        position: "absolute",
        left: box.x,
        top: box.y,
        width: box.w,
        height: box.h,
        display: "flex",
        alignItems: "center",
        justifyContent: hasHeadline ? "space-between" : "center",
        gap: R.gap,
      }}
    >
      {hasHeadline ? (
        <Reveal style={{ width: roadmapHeadlineWidth(box) }}>
          <FitText
            text={props.headline ?? ""}
            sizes={R.headlineSizes}
            maxWidth={roadmapHeadlineWidth(box)}
            maxLines={R.headlineLines}
            maxHeight={box.h}
            weight={R.headlineWeight}
            lineHeight={R.headlineLine}
          />
        </Reveal>
      ) : null}
      <div style={{ width: listWidth, position: "relative" }}>
        {/* the line through the nodes */}
        <div
          style={{
            position: "absolute",
            left: node / 2 - 2,
            top: rowHeight / 2,
            bottom: rowHeight / 2,
            width: 4,
            background: color.line,
          }}
        />
        {list.map((step, i) => {
          const isCurrent = step.id === props.current;
          const isDone = done.has(step.id);
          return (
            <Reveal key={step.id} delay={i * 4}>
              <div
                style={{
                  position: "relative",
                  height: rowHeight,
                  display: "flex",
                  alignItems: "center",
                  gap: 36,
                }}
              >
                <div
                  style={{
                    width: node,
                    height: node,
                    flexShrink: 0,
                    borderRadius: node / 2,
                    boxSizing: "border-box",
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "center",
                    background: isCurrent
                      ? color.accent
                      : isDone
                        ? color.muted
                        : color.bg,
                    border: `4px solid ${
                      isCurrent
                        ? color.accent
                        : isDone
                          ? color.muted
                          : color.line
                    }`,
                    color: color.bg,
                    fontFamily: FONT_STACK,
                    fontWeight: 700,
                    fontSize: node * 0.62,
                  }}
                >
                  {isDone ? (
                    <svg
                      width={node * 0.6}
                      height={node * 0.6}
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke={color.bg}
                      strokeWidth={4}
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    >
                      <path d="M4 12.5l5 5L20 6.5" />
                    </svg>
                  ) : null}
                </div>
                <div
                  style={{
                    fontFamily: FONT_STACK,
                    fontSize,
                    lineHeight: 1.15,
                    fontWeight: isCurrent ? 700 : 500,
                    color: isCurrent
                      ? color.text
                      : isDone
                        ? color.muted
                        : color.neutral,
                    overflowWrap: "anywhere",
                  }}
                >
                  {step.label}
                </div>
              </div>
            </Reveal>
          );
        })}
      </div>
    </div>
  );
};
