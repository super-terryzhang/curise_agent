import { describe, expect, it } from "vitest";

import {
  buildDefaultImageOrder,
  makePrimary,
  moveImage,
  type PlannedImage,
} from "./image-upload-order";

const images: PlannedImage[] = [
  { token: "existing:1", source: "existing" },
  { token: "staged:10", source: "staged" },
  { token: "staged:11", source: "staged" },
];

describe("image upload final order", () => {
  it("preserves existing order and appends staged files by upload order", () => {
    expect(
      buildDefaultImageOrder(
        [{ id: 2, display_order: 1 }, { id: 1, display_order: 0 }],
        [{ id: 11, upload_order: 2 }, { id: 10, upload_order: 1 }],
      ).map((item) => item.token),
    ).toEqual(["existing:1", "existing:2", "staged:10", "staged:11"]);
  });

  it("setting the main image moves it to the first position", () => {
    expect(makePrimary(images, "staged:11").map((item) => item.token)).toEqual([
      "staged:11",
      "existing:1",
      "staged:10",
    ]);
  });

  it("moving an image never mutates the previous order", () => {
    const moved = moveImage(images, 2, 0);
    expect(moved.map((item) => item.token)).toEqual([
      "staged:11",
      "existing:1",
      "staged:10",
    ]);
    expect(images.map((item) => item.token)).toEqual([
      "existing:1",
      "staged:10",
      "staged:11",
    ]);
  });
});
