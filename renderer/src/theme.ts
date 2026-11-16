// The one source of the look (C7): theme.json at the root of the renderer.
import theme from "../theme.json";

export const color = theme.color;
export const FONT_FAMILY = theme.font.family;
export const FONT_STACK = `${theme.font.family}, ${theme.font.fallback}`;
