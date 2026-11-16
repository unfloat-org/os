import React from "react";
import { Composition, type CalculateMetadataFunction } from "remotion";
import { checkMediaText } from "./fitcheck.ts";
import { loadFaces } from "./fonts.tsx";
import type { Manifest } from "./manifest.ts";
import { totalFrames } from "./timing.ts";
import { Update, type UpdateProps } from "./Update.tsx";

// Shown only when the Studio is opened without props: one empty title scene.
const placeholder: Manifest = {
  version: 1,
  draft: "none",
  title: "No manifest",
  series: { name: "none", title: "No manifest", number: 1 },
  date: "1970-01-01",
  fps: 30,
  width: 1920,
  height: 1080,
  captions: true,
  chapters: [{ id: "title", label: "Title" }],
  roadmap: [],
  scenes: [
    {
      id: "title",
      chapter: "title",
      kind: "title",
      props: { title: "Pass a manifest as props" },
      audio: null,
      captions: [],
      seconds: 3,
    },
  ],
};

const calculateMetadata: CalculateMetadataFunction<UpdateProps> = async ({
  props,
}) => {
  // text is measured with Inter: refuse what cannot be one line before drawing anything
  await loadFaces();
  return {
    props: { ...props, fitError: checkMediaText(props.manifest) },
    durationInFrames: totalFrames(props.manifest.scenes, props.manifest.fps),
    fps: props.manifest.fps,
    width: props.manifest.width,
    height: props.manifest.height,
  };
};

export const RemotionRoot: React.FC = () => (
  <Composition
    id="Update"
    component={Update}
    durationInFrames={90}
    fps={30}
    width={1920}
    height={1080}
    defaultProps={{ manifest: placeholder }}
    calculateMetadata={calculateMetadata}
  />
);
