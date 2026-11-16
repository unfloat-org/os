import React from "react";
import { AbsoluteFill, useVideoConfig } from "remotion";
import { FrameProvider } from "./context.tsx";
import { FontGate } from "./fonts.tsx";
import { Indicator } from "./Furniture.tsx";
import type { Manifest } from "./manifest.ts";
import { SceneView } from "./Scene.tsx";
import { color } from "./theme.ts";
import { sceneSpans } from "./timing.ts";

/** `fitError` is set by the composition's metadata step (fitcheck.ts) and read by bin/render.mjs. */
export type UpdateProps = { manifest: Manifest; fitError?: string | null };

/** The whole video: its scenes in order, with the frame furniture on top. */
export const Update: React.FC<UpdateProps> = ({ manifest }) => {
  const { fps } = useVideoConfig();
  const spans = React.useMemo(
    () => sceneSpans(manifest.scenes, fps),
    [manifest, fps],
  );
  return (
    <AbsoluteFill style={{ backgroundColor: color.bg }}>
      <FontGate>
        <FrameProvider manifest={manifest}>
          {manifest.scenes.map((scene, i) => (
            <SceneView
              key={scene.id}
              scene={scene}
              from={spans[i].from}
              frames={spans[i].frames}
            />
          ))}
          <Indicator />
        </FrameProvider>
      </FontGate>
    </AbsoluteFill>
  );
};
