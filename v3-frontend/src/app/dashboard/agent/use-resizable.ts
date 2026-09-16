"use client";

/**
 * `useResizable` — hand-rolled drag-to-resize state, replacing
 * `react-resizable-panels` which silently ignored its own minSize/
 * defaultSize props in our v2 layout (see phase2 work log).
 *
 * Design:
 *   - Caller renders a thin handle and forwards `startDrag` to its onMouseDown.
 *   - Width is clamped between `min` and `max` on every move event.
 *   - Width persists to localStorage under `storageKey` so the user's
 *     preference survives reloads. SSR-safe (skipped on server).
 *   - The `edge` prop is the side of the screen the pane sits on. A pane
 *     on the LEFT grows when the cursor moves right (positive ΔX). A pane
 *     on the RIGHT grows when the cursor moves left (negative ΔX).
 *
 * Not handled (deliberately simple):
 *   - Touch events. Desktop-first. Add `onTouchStart` later if mobile
 *     usage materializes.
 *   - rAF throttling of mousemove. At ~60Hz the React re-renders are fine;
 *     if profiling shows jank, wrap setWidth in a requestAnimationFrame.
 *   - Debounced localStorage writes. Drag ops are short; the cost of an
 *     extra setItem per pixel is negligible.
 */

import { useCallback, useEffect, useRef, useState } from "react";

interface UseResizableOptions {
  storageKey: string;
  defaultWidth: number;
  min: number;
  max: number;
  /** Which screen edge the pane sits on. Determines drag direction sign. */
  edge: "left" | "right";
}

export function useResizable(opts: UseResizableOptions) {
  const { storageKey, defaultWidth, min, max, edge } = opts;

  const [width, setWidth] = useState<number>(defaultWidth);

  // Restore on first client render — keeping SSR initial state as
  // `defaultWidth` so server + client output match and we avoid hydration
  // mismatch warnings.
  const restoredRef = useRef(false);
  useEffect(() => {
    if (restoredRef.current) return;
    restoredRef.current = true;
    try {
      const stored = window.localStorage.getItem(storageKey);
      const parsed = stored ? Number.parseInt(stored, 10) : NaN;
      if (Number.isFinite(parsed) && parsed >= min && parsed <= max) {
        setWidth(parsed);
      }
    } catch {
      // localStorage may be blocked (private mode, etc.) — fall through.
    }
  }, [storageKey, min, max]);

  // Persist after each settled width. (Writing every pixel during a drag
  // is fine; localStorage is cheap and synchronous-feeling.)
  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, String(width));
    } catch {
      // ignore quota / blocked storage
    }
  }, [storageKey, width]);

  const startDrag = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault();
      e.stopPropagation();

      const startX = e.clientX;
      // Capture starting width via state at the time of mousedown, NOT via
      // closure over `width` (which would lag a render behind).
      const startWidth = width;
      const direction = edge === "left" ? 1 : -1;

      const onMove = (ev: MouseEvent) => {
        const delta = (ev.clientX - startX) * direction;
        const next = Math.max(min, Math.min(max, startWidth + delta));
        setWidth(next);
      };

      const cleanup = () => {
        window.removeEventListener("mousemove", onMove);
        window.removeEventListener("mouseup", cleanup);
        document.body.style.cursor = "";
        document.body.style.userSelect = "";
      };

      window.addEventListener("mousemove", onMove);
      window.addEventListener("mouseup", cleanup);
      // Visual feedback during drag — col-resize cursor everywhere, and
      // disable text selection so dragging over the chat doesn't highlight
      // messages.
      document.body.style.cursor = "col-resize";
      document.body.style.userSelect = "none";
    },
    [width, edge, min, max],
  );

  return { width, startDrag };
}
