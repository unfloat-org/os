// The renderer's statement of contract C3: the only input of the renderer.
// Props of every kind are strict (an unknown or misspelt property is an error,
// as in the storyboard); the other levels ignore fields they do not use.
import { z } from "zod";

const text = z.string().trim().min(1, "must not be empty");
const positive = z.number().positive();
const fraction = z.number().min(0).max(1);

const relativePath = z
  .string()
  .min(1)
  .refine((p) => !p.startsWith("/") && !/^[a-zA-Z]:/.test(p), {
    message: "must be a path relative to the build folder",
  })
  .refine((p) => !p.split(/[\\/]/).includes(".."), {
    message: 'must not contain ".."',
  });

// [x, y, w, h] as fractions of the media; the box lies inside it.
const box = z
  .tuple([fraction, fraction, positive, positive])
  .refine(([x, y, w, h]) => x + w <= 1 + 1e-6 && y + h <= 1 + 1e-6, {
    message: "x + w and y + h must not exceed 1",
  });

export const mediaItem = z
  .object({
    src: relativePath,
    type: z.enum(["image", "video"]),
    width: z.number().int().positive(),
    height: z.number().int().positive(),
    seconds: positive.optional(),
    label: text.optional(),
    zoom: box.optional(),
    highlight: z.array(box).max(8).optional(),
    start: z.number().min(0).optional(),
    end: positive.optional(),
    loop: z.boolean().optional(),
  })
  .strict()
  .superRefine((item, ctx) => {
    if (item.type === "video") {
      if (item.seconds === undefined) {
        ctx.addIssue({
          code: "custom",
          path: ["seconds"],
          message: "a video item needs its length in seconds",
        });
        return;
      }
      const start = item.start ?? 0;
      const end = item.end ?? item.seconds;
      if (start >= item.seconds) {
        ctx.addIssue({
          code: "custom",
          path: ["start"],
          message: `must be before the clip's end (${item.seconds} s)`,
        });
      } else if (end <= start) {
        ctx.addIssue({
          code: "custom",
          path: ["end"],
          message: "must be after start",
        });
      } else if (end > item.seconds + 0.05) {
        ctx.addIssue({
          code: "custom",
          path: ["end"],
          message: `is past the clip's end (${item.seconds} s)`,
        });
      }
    }
  });

const titleProps = z
  .object({ title: text, subtitle: text.optional(), meta: text })
  .strict();

const statementProps = z
  .object({
    headline: text.max(120, "at most 120 characters"),
    points: z.array(text).max(3).optional(),
  })
  .strict();

const mediaProps = z
  .object({
    headline: text,
    items: z.array(mediaItem).min(1).max(2),
    caption: text.optional(),
  })
  .strict();

const numberProps = z
  .object({
    value: text,
    label: text,
    denominator: text.optional(),
    headline: text.optional(),
  })
  .strict();

const listProps = z
  .object({
    headline: text,
    style: z.enum(["bullets", "numbered", "pairs"]),
    rows: z
      .array(z.union([text, z.object({ left: text, right: text }).strict()]))
      .min(1)
      .max(4),
    left_title: text.optional(),
    right_title: text.optional(),
    asks: z.boolean().optional(),
  })
  .strict()
  .superRefine((props, ctx) => {
    props.rows.forEach((row, i) => {
      const isPair = typeof row !== "string";
      if (props.style === "pairs" && !isPair) {
        ctx.addIssue({
          code: "custom",
          path: ["rows", i],
          message: 'a "pairs" list needs rows with left and right',
        });
      }
      if (props.style !== "pairs" && isPair) {
        ctx.addIssue({
          code: "custom",
          path: ["rows", i],
          message: `a "${props.style}" list needs plain strings`,
        });
      }
    });
    if (props.style !== "pairs") {
      for (const key of ["left_title", "right_title"] as const) {
        if (props[key] !== undefined) {
          ctx.addIssue({
            code: "custom",
            path: [key],
            message: 'only for the "pairs" style',
          });
        }
      }
    }
  });

const roadmapProps = z
  .object({
    headline: text.optional(),
    current: text,
    done: z.array(text),
  })
  .strict();

export const KINDS = [
  "title",
  "statement",
  "media",
  "number",
  "list",
  "roadmap",
] as const;
export type Kind = (typeof KINDS)[number];

export type TitleProps = z.infer<typeof titleProps>;
export type StatementProps = z.infer<typeof statementProps>;
export type MediaProps = z.infer<typeof mediaProps>;
export type MediaItem = z.infer<typeof mediaItem>;
export type NumberProps = z.infer<typeof numberProps>;
export type ListProps = z.infer<typeof listProps>;
export type RoadmapProps = z.infer<typeof roadmapProps>;

const slug = z
  .string()
  .regex(/^[a-z0-9][a-z0-9-]*$/, "lower-case letters, digits and hyphens only");

const sceneBase = {
  id: slug,
  chapter: slug,
  audio: z
    .object({ src: relativePath, seconds: positive })
    .nullable()
    .optional(),
  captions: z
    .array(
      z.object({
        text,
        start: z.number().min(0),
        end: positive,
      }),
    )
    .default([]),
  seconds: positive,
};

const sceneOf = <K extends Kind, P extends z.ZodType>(kind: K, props: P) =>
  z.object({ ...sceneBase, kind: z.literal(kind), props });

const scene = z.discriminatedUnion("kind", [
  sceneOf("title", titleProps),
  sceneOf("statement", statementProps),
  sceneOf("media", mediaProps),
  sceneOf("number", numberProps),
  sceneOf("list", listProps),
  sceneOf("roadmap", roadmapProps),
]);

export const manifestSchema = z
  .object({
    version: z.literal(1),
    draft: text,
    title: text,
    series: z.object({
      name: text,
      title: text,
      number: z.number().int().positive(),
    }),
    date: text,
    fps: z.literal(30),
    width: z.literal(1920),
    height: z.literal(1080),
    captions: z.boolean(),
    chapters: z.array(z.object({ id: slug, label: text })).min(1),
    roadmap: z.array(z.object({ id: slug, label: text })),
    scenes: z.array(scene).min(1),
  })
  .superRefine((m, ctx) => {
    const chapterIds = m.chapters.map((c) => c.id);
    const dup = (ids: string[], path: (string | number)[], what: string) => {
      ids.forEach((id, i) => {
        if (ids.indexOf(id) !== i) {
          ctx.addIssue({
            code: "custom",
            path: [...path, i, "id"],
            message: `${what} "${id}" appears twice`,
          });
        }
      });
    };
    dup(chapterIds, ["chapters"], "chapter");
    dup(
      m.roadmap.map((s) => s.id),
      ["roadmap"],
      "roadmap step",
    );
    dup(
      m.scenes.map((s) => s.id),
      ["scenes"],
      "scene",
    );

    let last = -1;
    const frame = 1 / m.fps;
    m.scenes.forEach((s, i) => {
      const at = chapterIds.indexOf(s.chapter);
      if (at < 0) {
        ctx.addIssue({
          code: "custom",
          path: ["scenes", i, "chapter"],
          message: `"${s.chapter}" is not in chapters`,
        });
      } else if (at < last) {
        ctx.addIssue({
          code: "custom",
          path: ["scenes", i, "chapter"],
          message: `"${s.chapter}" comes after a later chapter: scenes follow the order of chapters`,
        });
      } else {
        last = at;
      }
      if (s.audio && s.audio.seconds > s.seconds + frame) {
        ctx.addIssue({
          code: "custom",
          path: ["scenes", i, "audio", "seconds"],
          message: `${s.audio.seconds} s of audio do not fit in a scene of ${s.seconds} s`,
        });
      }
      s.captions.forEach((c, j) => {
        if (c.end <= c.start) {
          ctx.addIssue({
            code: "custom",
            path: ["scenes", i, "captions", j, "end"],
            message: "must be after start",
          });
        } else if (c.end > s.seconds + frame) {
          ctx.addIssue({
            code: "custom",
            path: ["scenes", i, "captions", j, "end"],
            message: `is past the end of the scene (${s.seconds} s)`,
          });
        }
      });
      if (s.kind === "roadmap") {
        const steps = m.roadmap.map((r) => r.id);
        if (steps.length === 0) {
          ctx.addIssue({
            code: "custom",
            path: ["scenes", i, "kind"],
            message: "a roadmap scene needs a roadmap in the manifest",
          });
        }
        if (!steps.includes(s.props.current)) {
          ctx.addIssue({
            code: "custom",
            path: ["scenes", i, "props", "current"],
            message: `"${s.props.current}" is not a roadmap step`,
          });
        }
        s.props.done.forEach((id, j) => {
          if (!steps.includes(id)) {
            ctx.addIssue({
              code: "custom",
              path: ["scenes", i, "props", "done", j],
              message: `"${id}" is not a roadmap step`,
            });
          } else if (id === s.props.current) {
            ctx.addIssue({
              code: "custom",
              path: ["scenes", i, "props", "done", j],
              message: "the current step is not done yet",
            });
          }
        });
      }
    });
  });

export type Manifest = z.infer<typeof manifestSchema>;
export type Scene = Manifest["scenes"][number];

/**
 * The input of `bin/render.mjs --fit <file>` (for `uo video check`): the text of
 * each scene, before anything is voiced or copied. `props` are the scene's
 * storyboard properties as the manifest will carry them; media items need only
 * their `label`. The engine has checked their shape; fitcheck.ts reads them.
 */
export const fitInputSchema = z
  .object({
    version: z.literal(1),
    captions: z.boolean(),
    scenes: z.array(
      z.object({
        id: slug,
        kind: z.enum(KINDS),
        props: z.record(z.string(), z.unknown()),
      }),
    ),
  })
  .strict();

export type FitInput = z.infer<typeof fitInputSchema>;
