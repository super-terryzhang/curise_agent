import { describe, expect, it } from "vitest";
import {
  createLatestRequestRunner,
  loadProductPage,
} from "./product-gallery-query";

describe("product gallery query coordination", () => {
  it("ignores an older response that finishes after the newest request", async () => {
    const runner = createLatestRequestRunner();
    let finishFirst: ((value: string) => void) | undefined;
    let finishSecond: ((value: string) => void) | undefined;

    const first = runner.run(
      () => new Promise<string>((resolve) => { finishFirst = resolve; }),
    );
    const second = runner.run(
      () => new Promise<string>((resolve) => { finishSecond = resolve; }),
    );

    finishSecond?.("newest");
    await expect(second).resolves.toBe("newest");
    finishFirst?.("stale");
    await expect(first).resolves.toBeUndefined();
  });

  it("reloads the nearest valid page after the requested page becomes empty", async () => {
    const requestedPages: number[] = [];
    const result = await loadProductPage(1, 24, async (page) => {
      requestedPages.push(page);
      return page === 1
        ? { total: 24, items: [] as string[] }
        : { total: 24, items: ["remaining product"] };
    });

    expect(requestedPages).toEqual([1, 0]);
    expect(result).toEqual({
      page: 0,
      total: 24,
      items: ["remaining product"],
    });
  });
});
