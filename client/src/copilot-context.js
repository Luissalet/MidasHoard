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
  if (row.lot_id) out.lot_id = row.lot_id;
  return out;
}

/** A lot as it was, to find it again on the server: every identifying field, absent ones as null. */
const lotMatch = (h) => ({ quantity: h.quantity, cost_basis: h.cost_basis ?? null, snapshot_id: h.snapshot_id ?? null, currency: h.currency ?? null });

/**
 * What the user changed in the holdings table, as `{upsert, remove}` for the PATCH endpoint. Rows are paired with their
 * baseline row by identity (`_k` when the editor sets it, else the n-th occurrence of the symbol), so several lots of one
 * symbol diff like any others: edited rows send only their changed fields (a cleared field is null), added rows go whole,
 * removed rows go as a symbol. A row that carries the server's `lot_id` is addressed by it (edited: `{symbol, ...delta, lot_id}`,
 * removed: `{symbol, lot_id}`), so it is exactly that lot or a conflict. Only legacy rows without an id fall back to `match` (the
 * lot's previous values) when their symbol has several lots. New rows carry no id; an extra lot of an existing symbol carries
 * `new_lot: true`. Never null.
 */
export function holdingsPatch(baseRows, draftRows) {
  const keyed = (rows) => {
    const seen = new Map();
    return (rows || []).filter(usable).map((row) => {
      const h = holdingFromRow(row);
      const nth = seen.get(h.symbol) || 0;
      seen.set(h.symbol, nth + 1);
      return { key: row._k != null ? `k:${row._k}` : `s:${h.symbol}#${nth}`, h };
    });
  };
  const base = keyed(baseRows);
  const draft = keyed(draftRows);
  const count = (list, symbol) => list.filter((e) => e.h.symbol === symbol).length;
  const draftByKey = new Map(draft.map((e) => [e.key, e.h]));
  const baseByKey = new Map(base.map((e) => [e.key, e.h]));
  const remove = [];
  const upsert = [];
  const lotRef = (h) => ({ symbol: h.symbol, match: lotMatch(h) });
  for (const { key, h } of base) {
    const now = draftByKey.get(key);
    if (now && now.symbol === h.symbol) continue;
    if (h.lot_id) remove.push({ symbol: h.symbol, lot_id: h.lot_id });
    else if (count(base, h.symbol) === 1 && count(draft, h.symbol) === 0) remove.push(h.symbol);
    else remove.push(lotRef(h));
  }
  for (const { key, h: drafted } of draft) {
    const { lot_id: _ignored, ...h } = drafted; // a new row never carries an id: the server names it
    const prev = baseByKey.get(key);
    if (!prev || prev.symbol !== h.symbol) {
      upsert.push(count(base, h.symbol) > 0 || count(draft, h.symbol) > 1 ? { ...h, new_lot: true } : h);
      continue;
    }
    const delta = {};
    for (const field of HOLDING_FIELDS) {
      if (h[field] !== prev[field]) delta[field] = h[field] === undefined ? null : h[field];
    }
    if (!Object.keys(delta).length) continue;
    if (prev.lot_id) { upsert.push({ symbol: h.symbol, ...delta, lot_id: prev.lot_id }); continue; }
    const several = count(base, h.symbol) > 1 || count(draft, h.symbol) > 1;
    upsert.push(several ? { symbol: h.symbol, ...delta, match: lotMatch(prev) } : { symbol: h.symbol, ...delta });
  }
  return { upsert, remove };
}

const ROW_FIELDS = ["symbol", ...HOLDING_FIELDS];
const rowSymbol = (row) => String(row.symbol || "").trim();
const sameFields = (a, b, fields = ROW_FIELDS) => fields.every((f) => String(a[f] ?? "") === String(b[f] ?? ""));
let rebases = 0;

/** A stored holding as an editor row (strings), keyed `key`. */
export function rowFromHolding(h, key) {
  return { symbol: "", quantity: "", snapshot_id: "", currency: "", cost_basis: "", ...h, _k: key, quantity: String(h.quantity), cost_basis: h.cost_basis ?? "" };
}

/**
 * Re-bases an unsaved holdings form onto the lots stored now (after a save conflict), without losing the draft. Lots are paired
 * strictly by the server's `lot_id`, never by symbol or position:
 *  - a baseline row whose lot still exists is rebased: the new baseline is the current lot and the form keeps the user's changed
 *    fields on top of the current values of the others (the assistant's quantity edit plus the user's cost edit);
 *  - a baseline row whose lot is gone and that the user did not touch (or already removed) simply follows the server;
 *  - a baseline row whose lot is gone but that the user edited is a conflict: the draft row is kept (as a new row, without the
 *    dead id) and its symbol is reported in `ambiguous`; nothing may be written on its behalf to any other lot;
 *  - rows without a `lot_id` (legacy) cannot be paired: when the user touched them they are kept as they were and reported as
 *    ambiguous, otherwise they follow the server.
 * The caller must not retry automatically while `ambiguous` is not empty. Returns `{ base, rows, ambiguous }`.
 */
export function rebaseLots(baseRows, draftRows, serverHoldings) {
  const gen = ++rebases;
  const server = (serverHoldings || []).map((h, i) => rowFromHolding(h, `s${gen}_${i}`));
  const serverById = new Map(server.filter((s) => s.lot_id).map((s) => [s.lot_id, s]));
  const base = (baseRows || []).filter((r) => rowSymbol(r));
  const draft = draftRows || [];
  const draftByKey = new Map(draft.map((r) => [r._k, r]));
  const ambiguous = [];
  const flag = (symbol) => { if (!ambiguous.includes(symbol)) ambiguous.push(symbol); };
  const paired = new Map(); // baseline _k -> current server row
  const dropped = new Set(); // baseline _k that follow the server (their draft row is not emitted)
  const orphans = new Set(); // baseline _k of a gone lot the user edited: the draft survives as a new row
  const legacyKept = new Set(); // baseline _k of legacy rows the user touched
  const legacySymbols = new Set();
  for (const b of base) {
    const d = draftByKey.get(b._k);
    const untouched = d && sameFields(b, d);
    if (b.lot_id) {
      const s = serverById.get(b.lot_id);
      if (s) paired.set(b._k, s);
      else if (!d || untouched) dropped.add(b._k);
      else { orphans.add(b._k); flag(rowSymbol(d)); }
    } else if (untouched) dropped.add(b._k);
    else { legacyKept.add(b._k); legacySymbols.add(rowSymbol(b)); flag(rowSymbol(b)); }
  }
  // the legacy rows of a symbol the user touched stay together, so the form keeps one consistent set for it
  for (const b of base) if (!b.lot_id && dropped.has(b._k) && legacySymbols.has(rowSymbol(b))) { dropped.delete(b._k); legacyKept.add(b._k); }
  const rows = [];
  for (const d of draft) {
    const p = paired.get(d._k);
    if (p) {
      const b = base.find((x) => x._k === d._k);
      const mine = Object.fromEntries(ROW_FIELDS.filter((f) => String(b[f] ?? "") !== String(d[f] ?? "")).map((f) => [f, d[f]]));
      rows.push({ ...p, ...mine, _k: p._k });
    } else if (orphans.has(d._k)) {
      const { lot_id: _dead, ...rest } = d;
      rows.push(rest);
    } else if (!dropped.has(d._k)) rows.push(d);
  }
  const pairedKeys = new Set([...paired.values()].map((s) => s._k));
  const knownIds = new Set(base.filter((b) => b.lot_id).map((b) => b.lot_id));
  const newBase = [];
  for (const s of server) {
    if (legacySymbols.has(rowSymbol(s)) && !pairedKeys.has(s._k)) continue; // would duplicate the legacy rows kept below
    newBase.push(s);
    if (!knownIds.has(s.lot_id)) rows.push(s); // a lot added meanwhile by someone else appears in the form
  }
  for (const b of base) if (legacyKept.has(b._k)) newBase.push(b);
  return { base: newBase, rows, ambiguous };
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
  // Published view state and form snapshots are captured synchronously, before the first await: the user may navigate or keep
  // typing while a fetch is pending, and the context must describe one single view. After a fetch, a snapshot is used only when it
  // was taken for the very object that came back; otherwise there is none rather than a late one.
  const readState = (key) => ({ ...(read(key) || {}) });
  const snap = new Map();
  const grab = (key) => { if (!snap.has(key)) snap.set(key, editor(key)); return snap.get(key); };
  try {
    if (route.page === "theses") {
      const state = readState("theses");
      if (state.creating || !route.param || route.param === "new") {
        view.object_id = "new";
        view.editor = grab("thesis:new");
      } else {
        const wanted = route.param;
        grab(`evidence:${wanted}`);
        const th = await lookup(() => api.thesis(wanted));
        if (th && th.id) {
          objects.push({ kind: "thesis", object_id: th.id, title: th.title, status: th.status, as_of: th.as_of, ref: th.ref });
          view.object_id = th.id;
          view.tab = state.tab;
          view.editor = th.id === wanted ? snap.get(`evidence:${wanted}`) : undefined;
        }
      }
    } else if (route.page === "portfolio") {
      const state = readState("portfolio");
      const selected = state.selected;
      if (selected) grab(`portfolio:${selected}`);
      grab("portfolio:new");
      const found = selected ? await lookup(() => api.portfolio(selected)) : null;
      if (found && found.name) {
        objects.push({ kind: "portfolio", object_id: found.name, title: found.name, currency: found.currency, holdings: (found.holdings || []).length });
        view.object_id = found.name;
        view.editor = found.name === selected ? snap.get(`portfolio:${selected}`) : undefined;
      } else {
        view.object_id = "new";
        view.editor = snap.get("portfolio:new");
      }
    } else if (route.page === "lab") {
      const lab = readState("lab");
      const spec = readState("lab-spec");
      const strategyId = spec.strategyId;
      if (lab.tab === "backtest") {
        if (strategyId) grab(`strategy:${strategyId}`);
        grab("strategy:new");
      }
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
        const strategy = strategyId ? await lookup(() => api.strategy(strategyId)) : null;
        if (strategy && strategy.id) objects.push({ kind: "strategy", object_id: strategy.id, title: strategy.name });
        view.editor = strategy && strategy.id ? (strategy.id === strategyId ? snap.get(`strategy:${strategyId}`) : undefined) : snap.get("strategy:new");
        if (!view.object_id) view.object_id = strategy && strategy.id ? strategy.id : "new";
      }
    } else if (route.page === "market") {
      const state = readState("market");
      const ids = Array.isArray(state.selected) ? [...state.selected] : [];
      grab("snapshot:new");
      if (ids.length) {
        const list = await lookup(() => api.snapshots({ limit: 100 }));
        for (const s of (list && list.snapshots) || []) {
          if (ids.includes(s.id)) objects.push({ kind: "snapshot", object_id: s.id, title: snapshotTitle(s), symbol: s.symbol, provider: s.provider });
        }
        if (objects.length) view.object_id = objects[0].object_id;
      }
      view.tab = state.tab;
      view.editor = snap.get("snapshot:new");
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
