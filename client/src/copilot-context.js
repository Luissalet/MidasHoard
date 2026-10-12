// Domain side of the Faustus assistant: what is open in the view (real ids read through the app's own API),
// the unsaved form fields, the minimal "what the user changed" payloads, and the refresh when a turn ends.
// The panel, relay and launcher are generic and live elsewhere; this module only exposes window.hoardCopilot.
import { api as realApi } from "./api.js";

// ------------------------------------------------------------------ pure helpers

/** Fields of `draft` whose value differs from `initial`, so only manual changes are sent and agent edits survive. */
export function changedFields(initial, draft) {
  const base = initial || {};
  return Object.fromEntries(Object.entries(draft || {}).filter(([key, value]) => JSON.stringify(value) !== JSON.stringify(base[key])));
}

/** Deep merge-patch between two JSON documents: nested objects diff, arrays replace, a removed key becomes null. */
export function configPatch(initial, draft) {
  const a = initial || {};
  const b = draft || {};
  const result = {};
  for (const key of new Set([...Object.keys(a), ...Object.keys(b)])) {
    if (JSON.stringify(a[key]) === JSON.stringify(b[key])) continue;
    if (!(key in b)) result[key] = null;
    else if (b[key] && a[key] && !Array.isArray(b[key]) && !Array.isArray(a[key]) && typeof b[key] === "object" && typeof a[key] === "object") {
      result[key] = configPatch(a[key], b[key]);
    } else result[key] = b[key];
  }
  return result;
}

const HOLDING_FIELDS = ["quantity", "snapshot_id", "currency", "cost_basis"];
const usable = (row) => String(row.symbol || "").trim() && row.quantity !== "" && row.quantity != null;

/** An editor row (strings) as the backend stores a holding. */
export function holdingFromRow(row) {
  const out = { symbol: String(row.symbol || "").trim(), quantity: Number(row.quantity) };
  if (row.snapshot_id) out.snapshot_id = row.snapshot_id;
  if (row.currency) out.currency = row.currency;
  if (row.cost_basis !== "" && row.cost_basis != null) out.cost_basis = Number(row.cost_basis);
  return out;
}

/**
 * What the user changed in the holdings table, as `{upsert, remove}` for the PATCH endpoint: new rows go whole, edited rows
 * only with their changed fields (a cleared field is null), removed rows by symbol. Holdings are matched by symbol, so
 * repeated symbols (several lots) cannot be diffed: null means "replace the whole portfolio".
 */
export function holdingsPatch(baseRows, draftRows) {
  const base = (baseRows || []).filter(usable).map(holdingFromRow);
  const draft = (draftRows || []).filter(usable).map(holdingFromRow);
  const repeated = (list) => new Set(list.map((h) => h.symbol)).size !== list.length;
  if (repeated(base) || repeated(draft)) return null;
  const before = new Map(base.map((h) => [h.symbol, h]));
  const after = new Set(draft.map((h) => h.symbol));
  const remove = base.filter((h) => !after.has(h.symbol)).map((h) => h.symbol);
  const upsert = [];
  for (const h of draft) {
    const prev = before.get(h.symbol);
    if (!prev) { upsert.push(h); continue; }
    const delta = {};
    for (const field of HOLDING_FIELDS) {
      if (h[field] !== prev[field]) delta[field] = h[field] === undefined ? null : h[field];
    }
    if (Object.keys(delta).length) upsert.push({ symbol: h.symbol, ...delta });
  }
  return { upsert, remove };
}

/** The URL hash as { page, param }. */
export function parseRoute(hash) {
  const [path] = String(hash || "").replace(/^#\/?/, "").split("?");
  const parts = path.split("/");
  return { page: parts[0] || "market", param: parts[1] ? decodeURIComponent(parts[1]) : null };
}

// ------------------------------------------------------------------ what the open view publishes

const published = new Map();

/** A page states what it is showing (ids, tab, mode); returns the cleanup. Safe to call with the same key repeatedly. */
export function publishView(key, value) {
  published.set(key, value);
  return () => { if (published.get(key) === value) published.delete(key); };
}

export function publishedView(key) {
  return published.get(key);
}

/** Ask the open form for its unsaved fields. Forms answer `midas:context-request` for their own object id; no answer is fine. */
export function editorContext(objectId) {
  if (typeof window === "undefined") return undefined;
  let snapshot;
  window.dispatchEvent(new CustomEvent("midas:context-request", {
    detail: { objectId, provide: (value) => { if (snapshot === undefined) snapshot = value; } },
  }));
  return snapshot;
}

// ------------------------------------------------------------------ turn end -> refresh

const REFRESH_EVENT = "midas:refresh";
let lastTurnEnd = 0;

/** Called by both the window event and onTurnEnd (the launcher fires both): the open view refreshes once. */
export function turnEnded(now = Date.now()) {
  if (typeof window === "undefined" || now - lastTurnEnd < 300) return false;
  lastTurnEnd = now;
  window.dispatchEvent(new CustomEvent(REFRESH_EVENT));
  return true;
}

/** Subscribe a view to refreshes. Handlers must reload data in place, never remount or reset forms. */
export function onRefresh(handler) {
  window.addEventListener(REFRESH_EVENT, handler);
  return () => window.removeEventListener(REFRESH_EVENT, handler);
}

// ------------------------------------------------------------------ context()

const snapshotTitle = (s) => `${s.symbol} (${s.provider}, ${(s.actual_start || "").slice(0, 10)} → ${(s.actual_end || "").slice(0, 10)})`;

async function lookup(fn) {
  try { return await fn(); } catch { return null; }
}

/**
 * Builds `{ active_objects, view }` for the open view. Ids come from the app's own REST API, so an unknown id or the `new`
 * creation views produce no object, never an exception.
 */
export async function buildContext({ api = realApi, hash, read = publishedView, editor = editorContext } = {}) {
  const route = parseRoute(hash ?? (typeof window !== "undefined" ? window.location.hash : ""));
  const objects = [];
  const view = { page: route.page };
  try {
    if (route.page === "theses") {
      const state = read("theses") || {};
      if (state.creating || !route.param || route.param === "new") {
        view.object_id = "new";
        view.editor = editor("thesis:new");
      } else {
        const th = await lookup(() => api.thesis(route.param));
        if (th && th.id) {
          objects.push({ kind: "thesis", object_id: th.id, title: th.title, status: th.status, as_of: th.as_of, ref: th.ref });
          view.object_id = th.id;
          view.tab = state.tab;
          view.editor = editor(`evidence:${th.id}`);
        }
      }
    } else if (route.page === "portfolio") {
      const state = read("portfolio") || {};
      const found = state.selected ? await lookup(() => api.portfolio(state.selected)) : null;
      if (found && found.name) {
        objects.push({ kind: "portfolio", object_id: found.name, title: found.name, currency: found.currency, holdings: (found.holdings || []).length });
        view.object_id = found.name;
        view.editor = editor(`portfolio:${found.name}`);
      } else {
        view.object_id = "new";
        view.editor = editor("portfolio:new");
      }
    } else if (route.page === "lab") {
      const lab = read("lab") || {};
      const spec = read("lab-spec") || {};
      view.tab = lab.tab;
      const runId = lab.tab === "opened" ? lab.openedId : lab.tab === "backtest" ? spec.runId : null;
      if (runId) {
        const run = await lookup(() => api.experiment(runId));
        if (run && run.run_id) {
          objects.push({ kind: "experiment", object_id: run.run_id, title: run.strategy || run.family || run.run_id, family: run.family });
          view.object_id = run.run_id;
        }
      }
      if (lab.tab === "backtest") {
        const strategy = spec.strategyId ? await lookup(() => api.strategy(spec.strategyId)) : null;
        if (strategy && strategy.id) objects.push({ kind: "strategy", object_id: strategy.id, title: strategy.name });
        view.editor = editor(`strategy:${strategy && strategy.id ? strategy.id : "new"}`);
        if (!view.object_id) view.object_id = strategy && strategy.id ? strategy.id : "new";
      }
    } else if (route.page === "market") {
      const state = read("market") || {};
      const ids = Array.isArray(state.selected) ? state.selected : [];
      if (ids.length) {
        const list = await lookup(() => api.snapshots({ limit: 100 }));
        for (const s of (list && list.snapshots) || []) {
          if (ids.includes(s.id)) objects.push({ kind: "snapshot", object_id: s.id, title: snapshotTitle(s), symbol: s.symbol, provider: s.provider });
        }
        if (objects.length) view.object_id = objects[0].object_id;
      }
      view.tab = state.tab;
      view.editor = editor("snapshot:new");
    }
  } catch {
    // the context is best effort: a failing read must never break the turn
  }
  for (const key of Object.keys(view)) if (view[key] === undefined) delete view[key];
  return { active_objects: objects, view };
}

/** Registers window.hoardCopilot once; the launcher merges context() into its base context and calls onTurnEnd. */
export function installCopilot(target = typeof window !== "undefined" ? window : null) {
  if (!target || target.hoardCopilot?.__midas) return;
  target.hoardCopilot = { __midas: true, context: () => buildContext(), onTurnEnd: () => { turnEnded(); } };
  target.addEventListener("hoard:copilot-turn-end", () => { turnEnded(); });
}
