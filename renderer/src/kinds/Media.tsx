import { Video } from "@remotion/media";
import React from "react";
import {
  Img,
  interpolate,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { useFrameInfo } from "../context.tsx";
import { fitRect, steps } from "../layout.ts";
import type { MediaItem, MediaProps } from "../manifest.ts";
import { color } from "../theme.ts";
import { FitText, Reveal, useFit } from "./parts.tsx";

const GAP = 56;
const LABEL_SIZE = 44;
const LABEL_ROW = 68;

/** The clip, trimmed by start and end; looped, or held on its last frame, for the scene. */
const Clip: React.FC<{ item: MediaItem }> = ({ item }) => {
  const { fps, durationInFrames } = useVideoConfig();
  const total = item.seconds ?? 0;
  const startFrame = Math.round((item.start ?? 0) * fps);
  const endFrame = Math.max(
    startFrame + 1,
    Math.round(Math.min(item.end ?? total, total) * fps),
  );
  const length = endFrame - startFrame;
  const style: React.CSSProperties = { width: "100%", height: "100%" };

  if (item.loop ?? true) {
    return (
      <Video
        src={staticFile(item.src)}
        trimBefore={startFrame}
        durationInFrames={length}
        loop
        muted
        objectFit="fill"
        style={style}
      />
    );
  }
  return (
    <>
      <Video
        src={staticFile(item.src)}
        trimBefore={startFrame}
        durationInFrames={length}
        muted
        objectFit="fill"
        style={style}
      />
      {length < durationInFrames ? (
        <Sequence from={length} layout="none">
          <Video
            src={staticFile(item.src)}
            trimBefore={endFrame - 2}
            durationInFrames={1}
            loop
            muted
            objectFit="fill"
            style={style}
          />
        </Sequence>
      ) : null}
    </>
  );
};

const Highlight: React.FC<{ rect: [number, number, number, number] }> = ({
  rect: [x, y, w, h],
}) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [14, 26], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <div
      style={{
        position: "absolute",
        left: `${x * 100}%`,
        top: `${y * 100}%`,
        width: `${w * 100}%`,
        height: `${h * 100}%`,
        boxSizing: "border-box",
        border: `6px solid ${color.accent}`,
        borderRadius: 12,
        opacity,
      }}
    />
  );
};

/** One image or clip, scaled to fit `w` x `h`; `zoom` shows only that part of it. */
const MediaView: React.FC<{ item: MediaItem; w: number; h: number }> = ({
  item,
  w,
  h,
}) => {
  const [zx, zy, zw, zh] = item.zoom ?? [0, 0, 1, 1];
  const shown = fitRect(item.width * zw, item.height * zh, { w, h });
  const scale = shown.scale;
  return (
    <div
      style={{
        position: "relative",
        width: shown.w,
        height: shown.h,
        overflow: "hidden",
        borderRadius: 6,
      }}
    >
      <div
        style={{
          position: "absolute",
          left: -zx * item.width * scale,
          top: -zy * item.height * scale,
          width: item.width * scale,
          height: item.height * scale,
        }}
      >
        {item.type === "image" ? (
          <Img
            src={staticFile(item.src)}
            style={{ width: "100%", height: "100%", display: "block" }}
          />
        ) : (
          <Clip item={item} />
        )}
        {(item.highlight ?? []).map((rect, i) => (
          <Highlight key={i} rect={rect} />
        ))}
      </div>
    </div>
  );
};

export const MediaScene: React.FC<{ props: MediaProps }> = ({ props }) => {
  const { box } = useFrameInfo();
  const items = props.items;
  const width = box.w - 160;
  const headline = useFit({
    text: props.headline,
    sizes: steps(68, 44, 4),
    maxWidth: width,
    maxLines: 2,
    weight: 700,
    lineHeight: 1.1,
    oneLineMin: 52,
  });
  const headlineHeight = headline.height;
  const hasLabels = items.some((i) => i.label);
  const captionFit = useFit({
    text: props.caption ?? "",
    sizes: [44],
    maxWidth: width,
    maxLines: 2,
    weight: 400,
    lineHeight: 1.3,
  });
  const captionHeight = props.caption ? captionFit.height + 28 : 0;
  const labelHeight = hasLabels ? LABEL_ROW : 0;
  const areaH = box.h - headlineHeight - 32 - labelHeight - captionHeight;
  const cellW = (box.w - GAP * (items.length - 1)) / items.length;

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
        gap: 32,
      }}
    >
      <Reveal>
        <FitText
          text={props.headline}
          sizes={[headline.fontSize]}
          maxWidth={width}
          maxLines={2}
          weight={700}
          lineHeight={1.1}
        />
      </Reveal>
      <div
        style={{
          flex: 1,
          display: "flex",
          justifyContent: "center",
          alignItems: "flex-start",
          gap: GAP,
        }}
      >
        {items.map((item, i) => (
          <Reveal key={i} delay={4 + i * 4}>
            <div
              style={{
                width: cellW,
                height: areaH + labelHeight,
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
              }}
            >
              <div
                style={{
                  width: cellW,
                  height: areaH,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                }}
              >
                <MediaView item={item} w={cellW} h={areaH} />
              </div>
              {hasLabels ? (
                <div style={{ height: LABEL_ROW, paddingTop: 14 }}>
                  <FitText
                    text={item.label ?? " "}
                    sizes={steps(LABEL_SIZE, 28, 4)}
                    maxWidth={cellW}
                    maxLines={1}
                    weight={600}
                    lineHeight={1.2}
                    color={color.muted}
                    align="center"
                  />
                </div>
              ) : null}
            </div>
          </Reveal>
        ))}
      </div>
      {props.caption ? (
        <FitText
          text={props.caption}
          sizes={[44]}
          maxWidth={width}
          maxLines={2}
          weight={400}
          lineHeight={1.3}
          color={color.muted}
          align="center"
          style={{ alignSelf: "center" }}
        />
      ) : null}
    </div>
  );
};
