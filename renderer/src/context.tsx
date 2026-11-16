import React, { createContext, useContext } from "react";
import { contentBox, type Box } from "./layout.ts";
import type { Manifest } from "./manifest.ts";

type Frame = { box: Box; captions: boolean; manifest: Manifest };

const FrameContext = createContext<Frame | null>(null);

export const FrameProvider: React.FC<{
  manifest: Manifest;
  children: React.ReactNode;
}> = ({ manifest, children }) => (
  <FrameContext.Provider
    value={{
      box: contentBox(manifest.captions),
      captions: manifest.captions,
      manifest,
    }}
  >
    {children}
  </FrameContext.Provider>
);

export const useFrameInfo = (): Frame => {
  const ctx = useContext(FrameContext);
  if (!ctx) throw new Error("FrameProvider is missing");
  return ctx;
};
