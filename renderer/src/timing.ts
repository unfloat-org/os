// Frame arithmetic shared by the composition and bin/render.mjs. No imports: it
// runs in the browser bundle and in plain Node.

export type Timed = { readonly seconds: number };

/** C3: each scene's seconds * fps, rounded to the nearest frame (at least one). */
export const sceneFrames = (seconds: number, fps: number): number =>
  Math.max(1, Math.round(seconds * fps));

export type SceneSpan = { readonly from: number; readonly frames: number };

/** Start frame and length of every scene, in order. */
export const sceneSpans = (
  scenes: readonly Timed[],
  fps: number,
): SceneSpan[] => {
  let from = 0;
  return scenes.map((scene) => {
    const frames = sceneFrames(scene.seconds, fps);
    const span = { from, frames };
    from += frames;
    return span;
  });
};

export const totalFrames = (scenes: readonly Timed[], fps: number): number =>
  scenes.reduce((sum, scene) => sum + sceneFrames(scene.seconds, fps), 0);

/** C3: stills and poster show the frame at two thirds of a scene's length. */
export const stillFrame = (span: SceneSpan): number =>
  span.from + Math.min(span.frames - 1, Math.floor((span.frames * 2) / 3));
