/**
 * Contract tests for `resolveActiveArtifact` in ArtifactPane.
 *
 * The selection rule decides which artifact is rendered given the
 * context's `activeId` and the live list. We pin each case so a refactor
 * surfaces a deterministic regression instead of a "tabs jump around"
 * UX bug.
 */

import { describe, expect, it } from "vitest";

import { resolveActiveArtifact } from "./ArtifactPane";
import type { ChatArtifact } from "@/lib/v3-chat-store";

function artifact(id: number, narration = "n"): ChatArtifact {
  return {
    id,
    session_id: "s",
    component: "generic_table",
    data: {},
    narration,
  };
}

describe("resolveActiveArtifact", () => {
  it("returns null when the artifact list is empty (activeId null)", () => {
    expect(resolveActiveArtifact([], null)).toBeNull();
  });

  it("returns null when the artifact list is empty (activeId set — gone now)", () => {
    expect(resolveActiveArtifact([], 5)).toBeNull();
  });

  it("returns the only artifact when the list has one", () => {
    expect(resolveActiveArtifact([artifact(7)], null)?.id).toBe(7);
  });

  it("returns the most recent (last in list) when activeId is null", () => {
    expect(
      resolveActiveArtifact(
        [artifact(1), artifact(2), artifact(3)],
        null,
      )?.id,
    ).toBe(3);
  });

  it("preserves the active id when it is still in the list", () => {
    // User manually picked tab #1; a new artifact #4 arrives. We must
    // NOT yank focus away from their manual selection.
    expect(
      resolveActiveArtifact(
        [artifact(1), artifact(2), artifact(3), artifact(4)],
        1,
      )?.id,
    ).toBe(1);
  });

  it("falls back to most recent when activeId is no longer in the list", () => {
    // The user had #5 selected, then the session reset wiped it out and
    // the list is now [1,2,3]. Fall through to the newest.
    expect(
      resolveActiveArtifact(
        [artifact(1), artifact(2), artifact(3)],
        5,
      )?.id,
    ).toBe(3);
  });
});
