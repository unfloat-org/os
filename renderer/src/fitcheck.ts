// What only a browser can decide: whether text fits its line. Runs in the
// composition's metadata step (the fonts are loaded first), so a media scene whose
// headline, label or caption cannot be one line at the smallest size is refused
// with the path of the field, before anything is drawn. The message travels back
// to bin/render.mjs in the resolved props (`fitError`): throwing in the page would
// make Remotion print a stack trace of its own before our error line.
import { fitText } from "./layout.ts";
import {
  HEADLINE_LINE_HEIGHT,
  HEADLINE_SIZES,
  HEADLINE_WEIGHT,
  INFO_LINE_HEIGHT,
  INFO_SIZES,
  INFO_WEIGHT,
  infoCells,
  mediaLayout,
  type MediaCount,
} from "./mediabox.ts";
import type { Manifest } from "./manifest.ts";

// fitText keeps a margin for the difference between measuring and the browser's layout.
const oneLine = (
  text: string,
  sizes: readonly number[],
  maxWidth: number,
  fontWeight: number,
  lineHeight: number,
): boolean =>
  fitText({ text, sizes, maxWidth, maxLines: 1, fontWeight, lineHeight }).lines === 1;

/** "scenes[i].props.<field>: ..." for the first media text that is not one line, else null. */
export const checkMediaText = (manifest: Manifest): string | null => {
  for (const [i, scene] of manifest.scenes.entries()) {
    if (scene.kind !== "media") continue;
    const count = scene.props.items.length as MediaCount;
    const at = `scenes[${i}].props`;
    const layout = mediaLayout(count);
    const smallest = HEADLINE_SIZES[HEADLINE_SIZES.length - 1];
    if (
      !oneLine(
        scene.props.headline,
        HEADLINE_SIZES,
        layout.headline.w,
        HEADLINE_WEIGHT,
        HEADLINE_LINE_HEIGHT,
      )
    ) {
      return `${at}.headline: does not fit one line at ${smallest} px: shorten it`;
    }
    for (const cell of infoCells(scene.props.items, scene.props.caption)) {
      const small = INFO_SIZES[INFO_SIZES.length - 1];
      if (!oneLine(cell.text, INFO_SIZES, cell.rect.w, INFO_WEIGHT, INFO_LINE_HEIGHT)) {
        return `${at}.${cell.field}: does not fit the line under the media at ${small} px: shorten it`;
      }
    }
  }
  return null;
};
