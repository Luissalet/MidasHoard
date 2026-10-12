// React glue for the assistant context: publish what a page shows, answer context requests from forms, refresh on turn end.
import { useEffect, useRef } from "react";
import { onRefresh, publishView } from "./copilot-context.js";

/** Runs `refresh` whenever an assistant turn ends. It must reload data in place (no remounts, no form resets). */
export function useRefreshOnTurnEnd(refresh) {
  const latest = useRef(refresh);
  latest.current = refresh;
  useEffect(() => onRefresh(() => { try { const r = latest.current(); if (r && r.catch) r.catch(() => {}); } catch { /* best effort */ } }), []);
}

/** tells the assistant what the page shows (ids, tab, mode) under `key`. */
export function usePublishView(key, value) {
  const serial = JSON.stringify(value);
  useEffect(() => publishView(key, value), [key, serial]);
}

/** Answers `midas:context-request` for `objectId` with the form's unsaved fields; `build()` is read at request time. */
export function useContextProvider(objectId, build) {
  const latest = useRef(build);
  latest.current = build;
  useEffect(() => {
    if (!objectId) return undefined;
    const answer = (event) => {
      if (event.detail.objectId !== objectId) return;
      try { event.detail.provide(latest.current()); } catch { /* no answer */ }
    };
    window.addEventListener("midas:context-request", answer);
    return () => window.removeEventListener("midas:context-request", answer);
  }, [objectId]);
}
