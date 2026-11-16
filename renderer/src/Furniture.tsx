// What stays on screen between scenes: the series line, the chapter indicator
// with the progress through the video, and the caption band.
import React from "react";
import { useCurrentFrame, useVideoConfig } from "remotion";
import { useFrameInfo } from "./context.tsx";
import { CAPTION_BAND, HEIGHT, MARGIN_X, WIDTH, fitText } from "./layout.ts";
import { sceneSpans } from "./timing.ts";
import { color, FONT_STACK } from "./theme.ts";

const TRACK_HEIGHT = 6;

/** Series title and update number, the current chapter, and how far through the video. */
export const Indicator: React.FC = () => {
  const frame = useCurrentFrame();
  const { durationInFrames, fps } = useVideoConfig();
  const { manifest } = useFrameInfo();
  const spans = React.useMemo(
    () => sceneSpans(manifest.scenes, fps),
    [manifest, fps],
  );

  const index = Math.max(
    0,
    spans.findIndex((span) => frame >= span.from && frame < span.from + span.frames),
  );
  const chapterId = manifest.scenes[index].chapter;
  // the title scene says it already
  const showSeries = manifest.scenes[index].kind !== "title";
  const chapterIndex = manifest.chapters.findIndex((c) => c.id === chapterId);
  const chapter = manifest.chapters[chapterIndex];

  // the first frame of every chapter, drawn as a gap in the track
  const starts = manifest.chapters
    .map((c) => spans[manifest.scenes.findIndex((s) => s.chapter === c.id)])
    .filter(Boolean)
    .map((span) => span.from);

  const progress = Math.min(1, (frame + 1) / durationInFrames);
  const text: React.CSSProperties = {
    fontFamily: FONT_STACK,
    fontSize: 32,
    fontWeight: 500,
    color: color.muted,
    whiteSpace: "nowrap",
  };

  return (
    <>
      <div
        style={{
          position: "absolute",
          left: 0,
          top: 0,
          width: WIDTH,
          height: TRACK_HEIGHT,
          background: color.line,
        }}
      >
        <div
          style={{
            width: `${progress * 100}%`,
            height: TRACK_HEIGHT,
            background: color.muted,
          }}
        />
        {starts.slice(1).map((from) => (
          <div
            key={from}
            style={{
              position: "absolute",
              top: 0,
              left: (from / durationInFrames) * WIDTH - 2,
              width: 4,
              height: TRACK_HEIGHT,
              background: color.bg,
            }}
          />
        ))}
      </div>
      <div
        style={{
          position: "absolute",
          left: MARGIN_X,
          right: MARGIN_X,
          top: 34,
          display: "flex",
          justifyContent: "space-between",
          gap: 48,
        }}
      >
        <div style={{ ...text, overflow: "hidden", textOverflow: "ellipsis" }}>
          {showSeries
            ? `${manifest.series.title} · Update ${manifest.series.number}`
            : ""}
        </div>
        <div style={text}>
          {chapter.label}
          <span style={{ color: color.neutral }}>
            {"  "}
            {chapterIndex + 1} / {manifest.chapters.length}
          </span>
        </div>
      </div>
    </>
  );
};

const CAPTION_SIZES = [60, 56, 52, 48, 44];

/** The caption shown at `seconds` into the scene, in the fixed band at the bottom. */
export const CaptionBand: React.FC<{
  captions: readonly { text: string; start: number; end: number }[];
}> = ({ captions }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const seconds = frame / fps;
  const { captions: enabled } = useFrameInfo();
  if (!enabled) return null;
  const active = captions.find((c) => seconds >= c.start && seconds < c.end);
  if (!active) return null;

  const maxWidth = WIDTH - 2 * MARGIN_X - 80;
  const fit = fitText({
    text: active.text,
    sizes: CAPTION_SIZES,
    maxWidth,
    maxLines: 2,
    maxHeight: CAPTION_BAND - 40,
    fontWeight: 500,
    lineHeight: 1.25,
  });
  return (
    <div
      style={{
        position: "absolute",
        left: 0,
        top: HEIGHT - CAPTION_BAND,
        width: WIDTH,
        height: CAPTION_BAND,
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
      }}
    >
      <div
        style={{
          maxWidth,
          fontFamily: FONT_STACK,
          fontSize: fit.fontSize,
          fontWeight: 500,
          lineHeight: 1.25,
          color: color.text,
          textAlign: "center",
          textWrap: "balance",
          overflowWrap: "anywhere",
        }}
      >
        {active.text}
      </div>
    </div>
  );
};
