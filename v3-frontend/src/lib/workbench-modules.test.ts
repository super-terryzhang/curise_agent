import { describe, expect, it } from "vitest";

import { visibleWorkbenchModules } from "./workbench-modules";


describe("workbench module permissions", () => {
  it("lets finance enter the workbench and AI without exposing product upload", () => {
    const keys = visibleWorkbenchModules("finance").map((module) => module.key);
    expect(keys).toContain("ai");
    expect(keys).toContain("finance");
    expect(keys).not.toContain("product-upload");
    expect(keys).not.toContain("image-upload");
  });

  it("shows operational upload tools to employees", () => {
    const keys = visibleWorkbenchModules("employee").map((module) => module.key);
    expect(keys).toEqual(["product-upload", "image-upload", "ai"]);
  });
});
