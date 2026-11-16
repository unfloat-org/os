// The media box (C7): sizes come from theme.json, a box-aspect item fills its box
// exactly, other items are fitted. Pure arithmetic: no browser.
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";
import {
  infoCells,
  INFO_ROW,
  mediaLayout,
  mediaWidth,
  placeInBox,
} from "../src/mediabox.ts";
import { fixtureManifest } from "../fixtures/manifest.mjs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const theme = JSON.parse(readFileSync(path.join(root, "theme.json"), "utf8"));
const { frame, media } = theme.layout;

test("theme.json carries the layout block of C7", () => {
  assert.deepEqual(theme.layout, {
    frame: [1920, 1080],
    media: { one: [1728, 600], two: [852, 600], gap: 24 },
  });
});

test("the boxes are the theme's numbers, centred, with the gap between two", () => {
  const one = mediaLayout(1);
  assert.deepEqual(
    one.boxes.map((b) => [b.w, b.h]),
    [media.one],
  );
  assert.equal(one.boxes[0].x, (frame[0] - media.one[0]) / 2);

  const two = mediaLayout(2);
  assert.deepEqual(
    two.boxes.map((b) => [b.w, b.h]),
    [media.two, media.two],
  );
  assert.equal(two.boxes[1].x - (two.boxes[0].x + two.boxes[0].w), media.gap);
  assert.equal(mediaWidth(2), 2 * media.two[0] + media.gap);
  // both rows of boxes span the same width, so the headline and the line align with them
  assert.equal(mediaWidth(1), mediaWidth(2));
  assert.equal(two.boxes[0].x, one.boxes[0].x);
  assert.equal(two.boxes[0].y, one.boxes[0].y);
});

test("the numbers are not repeated in the source", () => {
  const files = [
    ...readdirSync(path.join(root, "src")).map((f) => path.join(root, "src", f)),
    ...readdirSync(path.join(root, "src/kinds")).map((f) => path.join(root, "src/kinds", f)),
  ].filter((f) => /\.tsx?$/.test(f));
  for (const file of files) {
    const code = readFileSync(file, "utf8");
    for (const n of ["1728", "852"]) {
      assert.ok(!new RegExp(`\\b${n}\\b`).test(code.replace(/\/\/.*$/gm, "")), `${path.relative(root, file)} repeats ${n}`);
    }
  }
});

test("headline, boxes and the info line fit above the caption band", () => {
  const { headline, boxes, info } = mediaLayout(1);
  assert.ok(headline.y + headline.h <= boxes[0].y);
  assert.equal(info.h, INFO_ROW);
  assert.ok(info.h <= 40);
  assert.ok(boxes[0].y + boxes[0].h <= info.y);
  // the caption band starts at 1080 - 196, and keeps 12 px clear (layout.ts)
  assert.ok(info.y + info.h <= frame[1] - 196 - 12);
});

test("an item of the box's aspect is drawn at exactly the box size", () => {
  // the export sizes of C7
  const cases = [
    [1, { width: 2400, height: 833 }],
    [1, { width: 1920, height: 666 }],
    [2, { width: 1200, height: 845 }],
    [2, { width: 960, height: 676 }],
    [1, { width: 1728, height: 600 }],
  ];
  for (const [count, item] of cases) {
    const box = mediaLayout(count).boxes[0];
    const p = placeInBox(item, box);
    assert.deepEqual(p.rect, box, `${item.width}x${item.height} in ${count} box`);
    assert.equal(p.filled, true);
    assert.ok(Math.abs(p.scaleX - box.w / item.width) < 1e-12);
    assert.ok(Math.abs(p.scaleY - box.h / item.height) < 1e-12);
  }
});

test("a zoom that keeps the box's aspect fills the box too, in box coordinates", () => {
  const box = mediaLayout(2).boxes[1];
  const p = placeInBox({ width: 1200, height: 845, zoom: [0.2, 0.2, 0.6, 0.6] }, box);
  assert.deepEqual(p.rect, box);
  // the shown 720x507 pixels cover the box: 852 / 720 px per px
  assert.ok(Math.abs(p.scaleX - 852 / 720) < 1e-12);
});

test("any other aspect is fitted inside the box, centred and not cropped", () => {
  const box = mediaLayout(1).boxes[0];
  const wide = placeInBox({ width: 2400, height: 1350 }, box); // 16:9
  assert.equal(wide.filled, false);
  assert.equal(wide.rect.h, box.h);
  assert.ok(wide.rect.w < box.w);
  assert.equal(wide.rect.x - box.x, box.x + box.w - (wide.rect.x + wide.rect.w));
  assert.ok(Math.abs(wide.scaleX - wide.scaleY) < 1e-12);

  const tall = placeInBox({ width: 600, height: 1000 }, box);
  assert.equal(tall.rect.h, box.h);
  assert.ok(tall.rect.x >= box.x && tall.rect.x + tall.rect.w <= box.x + box.w);

  // a 2 % difference of aspect is not the box's aspect
  const near = placeInBox({ width: 2448, height: 833 }, box);
  assert.equal(near.filled, false);
});

test("the info line is one line: labels and caption joined, or each label under its box", () => {
  const one = infoCells([{ label: "Counts" }], "Weeks one to eight");
  assert.equal(one.length, 1);
  assert.match(one[0].text, /^Counts\s+·\s+Weeks one to eight$/);
  assert.equal(one[0].field, "caption");

  const two = infoCells([{ label: "Left" }, { label: "Right" }], undefined);
  const { boxes } = mediaLayout(2);
  assert.deepEqual(
    two.map((c) => [c.text, c.rect.x, c.rect.w, c.field]),
    [
      ["Left", boxes[0].x, boxes[0].w, "items[0].label"],
      ["Right", boxes[1].x, boxes[1].w, "items[1].label"],
    ],
  );

  const both = infoCells([{ label: "Left" }, { label: "Right" }], "Both");
  assert.equal(both.length, 1);
  assert.match(both[0].text, /^Left\s+·\s+Right\s+·\s+Both$/);

  assert.deepEqual(infoCells([{}], undefined), []);
  assert.deepEqual(infoCells([{ label: "A" }, {}], undefined).map((c) => c.field), ["items[0].label"]);
});

test("every item of the fixture's box-aspect scenes has the aspect of its box", () => {
  const m = fixtureManifest();
  const filled = [];
  for (const scene of m.scenes.filter((s) => s.kind === "media")) {
    const { boxes } = mediaLayout(scene.props.items.length);
    scene.props.items.forEach((item, i) =>
      filled.push(placeInBox(item, boxes[i]).filled),
    );
  }
  // all but the one 16:9 item that shows the fitted case
  assert.equal(filled.filter((f) => !f).length, 1);
  assert.ok(filled.filter(Boolean).length >= 5);
});
