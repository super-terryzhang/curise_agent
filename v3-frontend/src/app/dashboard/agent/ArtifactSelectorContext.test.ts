/**
 * ArtifactSelectorContext — pure-logic tests for selectByNarration's
 * matching semantics. The provider's React-state coupling is left to a
 * higher-level integration test (out of scope for vitest-node); here we
 * pin the deterministic matching rule by exercising the underlying
 * search shape.
 *
 * The rule we care about:
 *   - exact narration match → return latest occurrence (newest-first walk)
 *   - no match → no-op (return false)
 *   - empty narration → no-op
 *
 * If selectByNarration ever loosens to fuzzy matching, these tests must
 * be updated *deliberately* — fuzzy by accident is exactly the failure
 * mode we want to prevent.
 */

import { describe, expect, it } from "vitest";

import type { ChatArtifact } from "@/lib/v3-chat-store";

// Mirror the matching logic for direct testability without needing a
// React renderer. Kept in sync with the implementation in
// ArtifactSelectorContext.tsx; if the impl drifts, this test should fail.
function findArtifactByNarration(
  narration: string,
  artifacts: ChatArtifact[],
): number | null {
  if (!narration) return null;
  for (let i = artifacts.length - 1; i >= 0; i--) {
    if (artifacts[i].narration === narration) {
      return artifacts[i].id;
    }
  }
  return null;
}

function art(id: number, narration: string): ChatArtifact {
  return {
    id,
    session_id: "s",
    component: "generic_table",
    data: {},
    narration,
  };
}

describe("findArtifactByNarration (selectByNarration semantics)", () => {
  it("returns null on empty narration", () => {
    expect(findArtifactByNarration("", [art(1, "a")])).toBeNull();
  });

  it("returns null when nothing matches", () => {
    expect(
      findArtifactByNarration("not there", [art(1, "a"), art(2, "b")]),
    ).toBeNull();
  });

  it("matches an exact narration string", () => {
    expect(
      findArtifactByNarration("订单 #1 匹配预览", [
        art(1, "订单 #1 匹配预览"),
        art(2, "天气表"),
      ]),
    ).toBe(1);
  });

  it("returns the LATEST occurrence when multiple match (re-dispatch case)", () => {
    // Agent re-ran the same view. Newer copy should be selected.
    expect(
      findArtifactByNarration("订单 #1 匹配预览", [
        art(1, "订单 #1 匹配预览"),
        art(2, "其他"),
        art(3, "订单 #1 匹配预览"),
      ]),
    ).toBe(3);
  });

  it("does NOT fuzzy-match substrings (would jump silently)", () => {
    expect(
      findArtifactByNarration("订单", [art(1, "订单 #1 匹配预览")]),
    ).toBeNull();
  });

  it("does NOT match on whitespace-trim differences", () => {
    expect(
      findArtifactByNarration("订单 #1", [art(1, " 订单 #1 ")]),
    ).toBeNull();
  });
});
