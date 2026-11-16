// What only a browser can decide: whether text fits where it is drawn. The sizes
// come from textfit.ts and mediabox.ts, the same functions the components draw
// with, so this check and the picture cannot disagree. It runs where Inter is
// loaded: in the composition's metadata step of a render (a problem refuses the
// render with the field path) and in the `Fit` composition that `--fit` runs for
// `uo video check` (every problem is reported, before anything is voiced).
// Throwing in the page would make Remotion print a stack trace of its own before
// our error line, so problems travel back in the resolved props.
import { contentBox, fitText } from "./layout.ts";
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
import {
  LIST,
  listLayout,
  NUMBER,
  numberFit,
  ROADMAP,
  roadmapHeadlineFit,
  STATEMENT,
  statementLayout,
} from "./textfit.ts";

/** A scene as far as its text goes: the manifest's scenes, or the input of `--fit`. */
export type TextScene = {
  id: string;
  kind: string;
  props: Record<string, unknown>;
};
/** `field` is the path inside the scene's props, for example `rows[2]` or `items[0].label`. */
export type FitProblem = {
  scene: number;
  id: string;
  field: string;
  message: string;
};

const smallest = (sizes: readonly number[]): number => sizes[sizes.length - 1];

const str = (value: unknown): string | undefined =>
  typeof value === "string" ? value : undefined;

const strings = (value: unknown): string[] =>
  Array.isArray(value)
    ? value.filter((v): v is string => typeof v === "string")
    : [];

const lines = (n: number): string => (n === 1 ? "one line" : `${n} lines`);

/** Every text of `scenes` that does not fit, in scene order. */
export const textProblems = (
  scenes: readonly TextScene[],
  captions: boolean,
): FitProblem[] => {
  const box = contentBox(captions);
  const problems: FitProblem[] = [];
  for (const [i, scene] of scenes.entries()) {
    const p = scene.props;
    const add = (field: string, message: string): void => {
      problems.push({ scene: i, id: scene.id, field, message });
    };
    if (scene.kind === "statement") {
      const layout = statementLayout(
        { headline: str(p.headline) ?? "", points: strings(p.points) },
        box,
      );
      layout.points.forEach((fit, n) => {
        if (!fit.fits) {
          add(
            `points[${n}]`,
            `does not fit ${lines(STATEMENT.pointLines)} at ${smallest(STATEMENT.pointSizes)} px: shorten it`,
          );
        }
      });
      if (!layout.headline.fits) {
        add(
          "headline",
          `does not fit ${lines(STATEMENT.headlineLines)} at ${smallest(STATEMENT.headlineSizes)} px above its points: shorten it`,
        );
      }
    } else if (scene.kind === "list") {
      const rows = Array.isArray(p.rows)
        ? (p.rows as (string | { left: string; right: string })[])
        : [];
      const style = (str(p.style) ?? "bullets") as
        | "bullets"
        | "numbered"
        | "pairs";
      const layout = listLayout(
        {
          headline: str(p.headline) ?? "",
          style,
          rows,
          left_title: str(p.left_title),
          right_title: str(p.right_title),
        },
        box,
      );
      if (!layout.headline.fits) {
        add(
          "headline",
          `does not fit ${lines(LIST.headlineLines)} at ${smallest(LIST.headlineSizes)} px: shorten it`,
        );
      }
      for (const field of layout.longRows) {
        add(
          field,
          `does not fit ${lines(LIST.rowLines)} at ${smallest(LIST.rowSizes)} px: shorten it`,
        );
      }
      if (!layout.rowsFit && layout.longRows.length === 0) {
        add(
          "rows",
          `the rows do not fit the frame together at ${smallest(LIST.rowSizes)} px: shorten them or use fewer`,
        );
      }
    } else if (scene.kind === "number") {
      for (const field of ["headline", "label", "denominator"] as const) {
        const text = str(p[field]);
        if (text && !numberFit(field, text, box).fits) {
          add(
            field,
            `does not fit ${lines(NUMBER[field].lines)} at ${smallest(NUMBER[field].sizes)} px: shorten it`,
          );
        }
      }
    } else if (scene.kind === "roadmap") {
      const text = str(p.headline);
      if (text && !roadmapHeadlineFit(text, box).fits) {
        add(
          "headline",
          `does not fit ${lines(ROADMAP.headlineLines)} at ${smallest(ROADMAP.headlineSizes)} px beside the steps: shorten it`,
        );
      }
    } else if (scene.kind === "media") {
      const items = (Array.isArray(p.items) ? p.items : []) as {
        label?: string;
      }[];
      if (items.length < 1 || items.length > 2) continue;
      const layout = mediaLayout(items.length as MediaCount);
      const headline = fitText({
        text: str(p.headline) ?? "",
        sizes: HEADLINE_SIZES,
        maxWidth: layout.headline.w,
        maxLines: 1,
        fontWeight: HEADLINE_WEIGHT,
        lineHeight: HEADLINE_LINE_HEIGHT,
      });
      if (!headline.fits) {
        add(
          "headline",
          `does not fit one line at ${smallest(HEADLINE_SIZES)} px: shorten it`,
        );
      }
      for (const cell of infoCells(items, str(p.caption))) {
        const fit = fitText({
          text: cell.text,
          sizes: INFO_SIZES,
          maxWidth: cell.rect.w,
          maxLines: 1,
          fontWeight: INFO_WEIGHT,
          lineHeight: INFO_LINE_HEIGHT,
        });
        if (!fit.fits) {
          add(
            cell.field,
            `does not fit the line under the media at ${smallest(INFO_SIZES)} px: shorten it`,
          );
        }
      }
    }
  }
  return problems;
};

/** "scenes[i].props.<field>: ..." for the first text of the manifest that does not fit, else null. */
export const firstTextProblem = (manifest: Manifest): string | null => {
  const [first] = textProblems(
    manifest.scenes as unknown as TextScene[],
    manifest.captions,
  );
  return first
    ? `scenes[${first.scene}].props.${first.field}: ${first.message}`
    : null;
};
