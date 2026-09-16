"use client";

/**
 * ArtifactSelectorContext — lets components anywhere in the v2 page tree
 * "select" an artifact in the right pane.
 *
 * Why a context (not props): the consumer is `ArtifactToolUI` in the chat
 * stream — it's mounted deep inside assistant-ui's `MessagePrimitive.Parts`
 * tree, with no clean prop path back to the workspace shell. Context is
 * the standard React escape hatch for this kind of cross-tree wiring.
 *
 * Two ways to select:
 *   - `selectById(id)`: when the caller knows the synthetic id assigned
 *     by `useV3Chat` (ArtifactPane uses this from tab clicks / dropdown).
 *   - `selectByNarration(narration)`: when the caller only knows what the
 *     agent named the artifact (inline chip in chat). We do a strict
 *     equality match against the most-recent artifact's narration field;
 *     no fuzzy matching — fuzzy would silently jump to the wrong artifact
 *     after a near-duplicate dispatch.
 *
 * Active-id state lives in the Provider. Consumers read it via
 * `useArtifactSelector().activeId`. ArtifactPane subscribes to changes
 * and updates its visible tab/dropdown highlight.
 */

import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type { ChatArtifact } from "@/lib/v3-chat-store";

interface ArtifactSelectorValue {
  /** Currently-active artifact id, or null if nothing selected yet. */
  activeId: number | null;
  /** Direct select — used by tabs / dropdown inside ArtifactPane. */
  selectById: (id: number | null) => void;
  /**
   * Select the most-recent artifact whose `narration` matches exactly.
   * Returns true if a match was found + activated; false if no-op.
   * The provider holds a ref to the live artifacts list so callers
   * don't have to pass it on every click — keeps the inline-chip call
   * site one-liner: `selector?.selectByNarration(narration)`.
   */
  selectByNarration: (narration: string) => boolean;
  /** Read-only signal: is the active artifact currently selectable
   * via narration? Used by the inline chip to disable itself when
   * the matching artifact hasn't been created yet (mid-stream). */
  hasArtifactWithNarration: (narration: string) => boolean;
}

const ArtifactSelectorCtx = createContext<ArtifactSelectorValue | null>(null);

export function ArtifactSelectorProvider({
  artifacts,
  children,
}: {
  artifacts: ChatArtifact[];
  children: ReactNode;
}) {
  const [activeId, setActiveId] = useState<number | null>(null);

  // Keep a stable ref to the latest artifacts list so the callbacks
  // below don't recreate every render (which would churn child memo
  // dependencies). Updated synchronously below useState, before any
  // child effect that reads from context fires.
  const artifactsRef = useRef<ChatArtifact[]>(artifacts);
  artifactsRef.current = artifacts;

  const selectById = useCallback((id: number | null) => {
    setActiveId(id);
  }, []);

  const findByNarration = useCallback((narration: string): number | null => {
    if (!narration) return null;
    const list = artifactsRef.current;
    // Walk from the end (most recent) so a re-generation lands on the
    // newer copy, not the original.
    for (let i = list.length - 1; i >= 0; i--) {
      if (list[i].narration === narration) return list[i].id;
    }
    return null;
  }, []);

  const selectByNarration = useCallback(
    (narration: string) => {
      const id = findByNarration(narration);
      if (id == null) return false;
      setActiveId(id);
      return true;
    },
    [findByNarration],
  );

  const hasArtifactWithNarration = useCallback(
    (narration: string) => findByNarration(narration) !== null,
    [findByNarration],
  );

  const value = useMemo<ArtifactSelectorValue>(
    () => ({
      activeId,
      selectById,
      selectByNarration,
      hasArtifactWithNarration,
    }),
    [activeId, selectById, selectByNarration, hasArtifactWithNarration],
  );

  return (
    <ArtifactSelectorCtx.Provider value={value}>
      {children}
    </ArtifactSelectorCtx.Provider>
  );
}

/**
 * Hook for consumers. Returns `null` when used outside a provider — keep
 * this defensive so an accidental usage from a shared component
 * re-used outside this page degrades gracefully instead of crashing.
 * Consumers should null-check before calling methods.
 */
export function useArtifactSelector(): ArtifactSelectorValue | null {
  return useContext(ArtifactSelectorCtx);
}
