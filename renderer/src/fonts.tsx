// Inter from the @fontsource/inter package (SIL OFL 1.1): the files are
// bundled with the composition, so rendering fetches nothing.
import { loadFont } from "@remotion/fonts";
import React, { useEffect, useState } from "react";
import { useDelayRender } from "remotion";
import latin400 from "@fontsource/inter/files/inter-latin-400-normal.woff2";
import latin500 from "@fontsource/inter/files/inter-latin-500-normal.woff2";
import latin600 from "@fontsource/inter/files/inter-latin-600-normal.woff2";
import latin700 from "@fontsource/inter/files/inter-latin-700-normal.woff2";
import ext400 from "@fontsource/inter/files/inter-latin-ext-400-normal.woff2";
import ext500 from "@fontsource/inter/files/inter-latin-ext-500-normal.woff2";
import ext600 from "@fontsource/inter/files/inter-latin-ext-600-normal.woff2";
import ext700 from "@fontsource/inter/files/inter-latin-ext-700-normal.woff2";
import { FONT_FAMILY } from "./theme.ts";

const LATIN =
  "U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,U+0304,U+0308,U+0329,U+2000-206F,U+20AC,U+2122,U+2191,U+2193,U+2212,U+2215,U+FEFF,U+FFFD";
const LATIN_EXT =
  "U+0100-02BA,U+02BD-02C5,U+02C7-02CC,U+02CE-02D7,U+02DD-02FF,U+0304,U+0308,U+0329,U+1D00-1DBF,U+1E00-1E9F,U+1EF2-1EFF,U+2020,U+20A0-20AB,U+20AD-20C0,U+2113,U+2C60-2C7F,U+A720-A7FF";

const faces: { url: string; weight: string; range: string }[] = [
  { url: latin400, weight: "400", range: LATIN },
  { url: latin500, weight: "500", range: LATIN },
  { url: latin600, weight: "600", range: LATIN },
  { url: latin700, weight: "700", range: LATIN },
  { url: ext400, weight: "400", range: LATIN_EXT },
  { url: ext500, weight: "500", range: LATIN_EXT },
  { url: ext600, weight: "600", range: LATIN_EXT },
  { url: ext700, weight: "700", range: LATIN_EXT },
];

let loading: Promise<unknown> | null = null;

/** Loads every face once; measuring text needs it (the composition's metadata and FontGate). */
export const loadFaces = (): Promise<unknown> => {
  loading ??= Promise.all(
    faces.map((face) =>
      loadFont({
        family: FONT_FAMILY,
        url: face.url,
        weight: face.weight,
        unicodeRange: face.range,
        format: "woff2",
      }),
    ),
  );
  return loading;
};

/** Holds the render until every face is loaded, so text is measured with Inter. */
export const FontGate: React.FC<{ children: React.ReactNode }> = ({
  children,
}) => {
  const { delayRender, continueRender, cancelRender } = useDelayRender();
  const [handle] = useState(() => delayRender("Loading Inter"));
  const [ready, setReady] = useState(false);

  useEffect(() => {
    loadFaces()
      .then(() => {
        setReady(true);
        continueRender(handle);
      })
      .catch((err: unknown) => cancelRender(err));
  }, [handle, continueRender, cancelRender]);

  return ready ? <>{children}</> : null;
};
