import { Audio } from "@remotion/media";
import React from "react";
import {
  interpolate,
  Sequence,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { CaptionBand } from "./Furniture.tsx";
import { ListScene } from "./kinds/List.tsx";
import { MediaScene } from "./kinds/Media.tsx";
import { NumberScene } from "./kinds/Number.tsx";
import { RoadmapScene } from "./kinds/Roadmap.tsx";
import { Statement } from "./kinds/Statement.tsx";
import { Title } from "./kinds/Title.tsx";
import type { Scene as SceneData } from "./manifest.ts";

/** Draws one scene by its kind. Add a kind here, in manifest.ts and in kinds/. */
const Layout: React.FC<{ scene: SceneData }> = ({ scene }) => {
  switch (scene.kind) {
    case "title":
      return <Title props={scene.props} />;
    case "statement":
      return <Statement props={scene.props} />;
    case "media":
      return <MediaScene props={scene.props} />;
    case "number":
      return <NumberScene props={scene.props} />;
    case "list":
      return <ListScene props={scene.props} />;
    case "roadmap":
      return <RoadmapScene props={scene.props} />;
  }
};

/** A short fade in and out inside the scene's own length (no overlap, no change of length). */
const Fade: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const edge = Math.max(1, Math.min(9, Math.floor(durationInFrames / 5)));
  const opacity = interpolate(
    frame,
    [0, edge, durationInFrames - edge, durationInFrames],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  return <div style={{ position: "absolute", inset: 0, opacity }}>{children}</div>;
};

export const SceneView: React.FC<{
  scene: SceneData;
  from: number;
  frames: number;
}> = ({ scene, from, frames }) => (
  <Sequence name={scene.id} from={from} durationInFrames={frames} layout="absolute-fill">
    <Fade>
      <Layout scene={scene} />
    </Fade>
    <CaptionBand captions={scene.captions} />
    {scene.audio ? <Audio src={staticFile(scene.audio.src)} /> : null}
  </Sequence>
);
