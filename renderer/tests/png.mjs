// A minimal PNG reader for the render test: 8-bit grey, RGB or RGBA, not
// interlaced (what Chrome's screenshots are). Returns { width, height, channels, data }.
import { Buffer } from "node:buffer";
import { inflateSync } from "node:zlib";

export const readPng = (buffer) => {
  if (buffer.subarray(1, 4).toString() !== "PNG") throw new Error("not a PNG");
  let pos = 8;
  let header;
  const parts = [];
  while (pos < buffer.length) {
    const length = buffer.readUInt32BE(pos);
    const type = buffer.subarray(pos + 4, pos + 8).toString("latin1");
    const body = buffer.subarray(pos + 8, pos + 8 + length);
    if (type === "IHDR") header = body;
    if (type === "IDAT") parts.push(body);
    pos += 12 + length;
  }
  const width = header.readUInt32BE(0);
  const height = header.readUInt32BE(4);
  const [depth, colour, , , interlace] = [header[8], header[9], header[10], header[11], header[12]];
  const channels = { 0: 1, 2: 3, 6: 4 }[colour];
  if (depth !== 8 || !channels || interlace !== 0) {
    throw new Error(`unsupported PNG: depth ${depth}, colour type ${colour}, interlace ${interlace}`);
  }
  const raw = inflateSync(Buffer.concat(parts));
  const stride = width * channels;
  const data = Buffer.alloc(stride * height);
  for (let y = 0; y < height; y++) {
    const filter = raw[y * (stride + 1)];
    const line = raw.subarray(y * (stride + 1) + 1, (y + 1) * (stride + 1));
    for (let x = 0; x < stride; x++) {
      const a = x >= channels ? data[y * stride + x - channels] : 0;
      const b = y > 0 ? data[(y - 1) * stride + x] : 0;
      const c = x >= channels && y > 0 ? data[(y - 1) * stride + x - channels] : 0;
      let predictor = 0;
      if (filter === 1) predictor = a;
      else if (filter === 2) predictor = b;
      else if (filter === 3) predictor = (a + b) >> 1;
      else if (filter === 4) {
        const p = a + b - c;
        const [pa, pb, pc] = [Math.abs(p - a), Math.abs(p - b), Math.abs(p - c)];
        predictor = pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
      }
      data[y * stride + x] = (line[x] + predictor) & 255;
    }
  }
  return { width, height, channels, data };
};

/** Bounding box { x, y, w, h } of the pixels for which `match(r, g, b)` is true, or null. */
export const boundingBox = (png, match) => {
  let [x0, y0, x1, y1] = [Infinity, Infinity, -1, -1];
  for (let y = 0; y < png.height; y++) {
    for (let x = 0; x < png.width; x++) {
      const i = (y * png.width + x) * png.channels;
      if (match(png.data[i], png.data[i + 1], png.data[i + 2])) {
        x0 = Math.min(x0, x);
        y0 = Math.min(y0, y);
        x1 = Math.max(x1, x);
        y1 = Math.max(y1, y);
      }
    }
  }
  return x1 < 0 ? null : { x: x0, y: y0, w: x1 - x0 + 1, h: y1 - y0 + 1 };
};
