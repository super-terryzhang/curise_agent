/**
 * Sanity checks for tiles.ts.
 *
 * The data is small and edited by hand, so a few invariants are easy
 * to violate during a rushed refactor (e.g. copy-paste a tile and
 * forget to change the id → React key warning + click handler maps to
 * wrong prompt). Pin them.
 */

import { describe, expect, it } from "vitest";

import { TILES } from "./tiles";

describe("TILES data", () => {
  it("has at least 4 tiles", () => {
    expect(TILES.length).toBeGreaterThanOrEqual(4);
  });

  it("has unique ids", () => {
    const ids = TILES.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("every tile has a non-empty label and description", () => {
    for (const t of TILES) {
      expect(t.label, `${t.id} label`).not.toBe("");
      expect(t.description, `${t.id} description`).not.toBe("");
    }
  });

  it("every tile has an icon component", () => {
    for (const t of TILES) {
      expect(typeof t.icon, `${t.id} icon`).toBe("object");
    }
  });

  it("prompt is a string (empty allowed for the free-dialogue tile)", () => {
    for (const t of TILES) {
      expect(typeof t.prompt, `${t.id} prompt`).toBe("string");
    }
  });
});
