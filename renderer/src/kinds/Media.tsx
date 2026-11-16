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
import type { MediaItem, MediaProps } from "../manifest.ts";
import {
  HEADLINE_LINE_HEIGHT,
  HEADLINE_SIZES,
  HEADLINE_WEIGHT,
  INFO_LINE_HEIGHT,
  INFO_SIZES,
  INFO_WEIGHT,
  infoCells,
  mediaLayout,
  placeInBox,
  type MediaCount,
  type Rect,
} from "../mediabox.ts";
import { color } from "../theme.ts";
import { FitText, Reveal } from "./parts.tsx";

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

/**
 * One image or clip in its media box. The box-aspect item fills the box exactly;
 * any other is fitted inside it, centred. `zoom` shows only that part of it, and
 * highlights stay in item coordinates.
 */
const MediaView: React.FC<{ item: MediaItem; box: Rect }> = ({ item, box }) => {
  const [zx, zy] = item.zoom ?? [0, 0];
  const { rect, scaleX, scaleY } = placeInBox(item, box);
  return (
    <div
      style={{
        position: "absolute",
        left: rect.x,
        top: rect.y,
        width: rect.w,
        height: rect.h,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          position: "absolute",
          left: -zx * item.width * scaleX,
          top: -zy * item.height * scaleY,
          width: item.width * scaleX,
          height: item.height * scaleY,
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
        {(item.highlight ?? []).map((r, i) => (
          <Highlight key={i} rect={r} />
        ))}
      </div>
    </div>
  );
};

/** A headline of one line, the media in its box(es), and one muted line under them (C7). */
export const MediaScene: React.FC<{ props: MediaProps }> = ({ props }) => {
  const items = props.items;
  const layout = mediaLayout(items.length as MediaCount);
  const cells = infoCells(items, props.caption);
  const absolute = (r: Rect): React.CSSProperties => ({
    position: "absolute",
    left: r.x,
    top: r.y,
    width: r.w,
    height: r.h,
  });

  return (
    <>
      <Reveal style={absolute(layout.headline)}>
        <FitText
          text={props.headline}
          sizes={HEADLINE_SIZES}
          maxWidth={layout.headline.w}
          maxLines={1}
          weight={HEADLINE_WEIGHT}
          lineHeight={HEADLINE_LINE_HEIGHT}
        />
      </Reveal>
      {items.map((item, i) => (
        <Reveal key={i} delay={4 + i * 4} style={{ position: "absolute", inset: 0 }}>
          <MediaView item={item} box={layout.boxes[i]} />
        </Reveal>
      ))}
      <Reveal delay={8} style={{ position: "absolute", inset: 0 }}>
        {cells.map((cell) => (
          <div key={cell.field} style={{ ...absolute(cell.rect), whiteSpace: "nowrap" }}>
            <FitText
              text={cell.text}
              sizes={INFO_SIZES}
              maxWidth={cell.rect.w}
              maxLines={1}
              weight={INFO_WEIGHT}
              lineHeight={INFO_LINE_HEIGHT}
              color={color.muted}
              align="center"
            />
          </div>
        ))}
      </Reveal>
    </>
  );
};
