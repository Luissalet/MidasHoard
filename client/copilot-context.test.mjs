import { test } from "node:test";
import assert from "node:assert/strict";

globalThis.window = new EventTarget();
const ctx = await import("./src/copilot-context.js");
const { changedFields, configPatch, holdingsPatch, holdingFromRow, parseRoute, buildContext, publishView, editorContext, turnEnded, onRefresh } = ctx;

const row = (symbol, quantity, extra = {}) => ({ symbol, quantity: String(quantity), snapshot_id: "", currency: "", cost_basis: "", ...extra });

test("changedFields keeps only the manual changes", () => {
  assert.deepEqual(changedFields({ name: "Old", notes: "Before" }, { name: "Old", notes: "Manual" }), { notes: "Manual" });
  assert.deepEqual(changedFields(null, { a: 1 }), { a: 1 });
});

test("configPatch diffs nested objects, replaces arrays and nulls removed keys", () => {
  const a = { indicators: { fast: { window: 20, type: "sma" }, slow: { window: 50 } }, universe: ["x"], costs_bps: 5 };
  const b = { indicators: { fast: { window: 10, type: "sma" }, slow: { window: 50 } }, universe: ["x", "y"] };
  assert.deepEqual(configPatch(a, b), { indicators: { fast: { window: 10 } }, universe: ["x", "y"], costs_bps: null });
  assert.deepEqual(configPatch(a, a), {});
});

test("holdingsPatch sends added rows whole, edited rows by changed field, removed rows by symbol", () => {
  const base = [row("AAA", 10, { cost_basis: "100", currency: "USD" }), row("BBB", 5), row("CCC", 1)];
  const draft = [row("AAA", 10, { cost_basis: "", currency: "USD" }), row("BBB", 7), row("DDD", 2, { currency: "EUR" })];
  assert.deepEqual(holdingsPatch(base, draft), {
    upsert: [{ symbol: "AAA", cost_basis: null }, { symbol: "BBB", quantity: 7 }, { symbol: "DDD", quantity: 2, currency: "EUR" }],
    remove: ["CCC"],
  });
});

test("holdingsPatch is empty without edits and ignores blank rows", () => {
  const base = [row("AAA", 10)];
  assert.deepEqual(holdingsPatch(base, [row("AAA", "10"), row("", "")]), { upsert: [], remove: [] });
  assert.deepEqual(holdingFromRow(row(" AAA ", "1,5".replace(",", "."), { cost_basis: "0" })), { symbol: "AAA", quantity: 1.5, cost_basis: 0 });
});

const lot = (key, symbol, quantity, cost) => row(symbol, quantity, { _k: key, cost_basis: cost == null ? "" : String(cost) });
const lots = () => [lot("l0", "AAA", 10, 100), lot("l1", "AAA", 20, 200), lot("l2", "BBB", 5)];

test("holdingsPatch with several lots sends only the edited lot, addressed by its previous values", () => {
  const draft = lots();
  draft[0] = lot("l0", "AAA", 11, 100);
  assert.deepEqual(holdingsPatch(lots(), draft), {
    upsert: [{ symbol: "AAA", quantity: 11, match: { quantity: 10, cost_basis: 100, snapshot_id: null, currency: null } }],
    remove: [],
  });
  assert.deepEqual(holdingsPatch(lots(), lots()), { upsert: [], remove: [] });
});

test("holdingsPatch with several lots keeps the other symbols plain and follows rows when one is removed", () => {
  const draft = [lot("l1", "AAA", 20, 200), lot("l2", "BBB", 6)];
  assert.deepEqual(holdingsPatch(lots(), draft), {
    upsert: [{ symbol: "BBB", quantity: 6 }],
    remove: [{ symbol: "AAA", match: { quantity: 10, cost_basis: 100, snapshot_id: null, currency: null } }],
  });
});

test("holdingsPatch appends a lot of an existing symbol with new_lot and never falls back to a full replacement", () => {
  const draft = [...lots(), lot("n1", "AAA", 3), lot("n2", "NEW", 1)];
  assert.deepEqual(holdingsPatch(lots(), draft), {
    upsert: [{ symbol: "AAA", quantity: 3, new_lot: true }, { symbol: "NEW", quantity: 1 }],
    remove: [],
  });
  const keyless = holdingsPatch([row("AAA", 1), row("AAA", 2)], [row("AAA", 1), row("AAA", 5)]);
  assert.ok(keyless);
  assert.deepEqual(keyless.upsert, [{ symbol: "AAA", quantity: 5, match: { quantity: 2, cost_basis: null, snapshot_id: null, currency: null } }]);
});

test("parseRoute reads page and param", () => {
  assert.deepEqual(parseRoute("#/theses/th_1"), { page: "theses", param: "th_1" });
  assert.deepEqual(parseRoute(""), { page: "market", param: null });
  assert.deepEqual(parseRoute("#/portfolio?x=1"), { page: "portfolio", param: null });
});

const fakeApi = {
  thesis: async (id) => { if (id !== "th_1") throw new Error("not found"); return { id, title: "Rates fall", status: "open", as_of: "2026-01-01", ref: "hoard://midas/thesis/th_1" }; },
  portfolio: async (name) => { if (name !== "main") throw new Error("nope"); return { name, currency: "EUR", holdings: [{ symbol: "A" }] }; },
  strategy: async (id) => { if (id !== "st_1") throw new Error("nope"); return { id, name: "sma-trend" }; },
  experiment: async (id) => { if (id !== "bt_1") throw new Error("nope"); return { run_id: id, strategy: "sma-trend", family: "sma-trend" }; },
  snapshots: async () => ({ snapshots: [{ id: "snp_a", symbol: "AAPL", provider: "yahoo", actual_start: "2020-01-01", actual_end: "2026-01-01" }, { id: "snp_b", symbol: "MSFT", provider: "yahoo" }] }),
};
const reader = (state) => (key) => state[key];

test("thesis view: real id, title and the evidence form answer", async () => {
  const out = await buildContext({ api: fakeApi, hash: "#/theses/th_1", read: reader({ theses: { creating: false } }), editor: (id) => ({ asked: id }) });
  assert.deepEqual(out.active_objects.map((o) => [o.kind, o.object_id, o.title]), [["thesis", "th_1", "Rates fall"]]);
  assert.deepEqual(out.view, { page: "theses", object_id: "th_1", editor: { asked: "evidence:th_1" } });
});

test("creation and unknown ids give no object and never throw", async () => {
  const creating = await buildContext({ api: fakeApi, hash: "#/theses", read: reader({ theses: { creating: true } }), editor: (id) => ({ asked: id }) });
  assert.deepEqual(creating, { active_objects: [], view: { page: "theses", object_id: "new", editor: { asked: "thesis:new" } } });
  const asNew = await buildContext({ api: fakeApi, hash: "#/theses/new", read: reader({}), editor: () => undefined });
  assert.deepEqual(asNew.active_objects, []);
  const missing = await buildContext({ api: fakeApi, hash: "#/theses/th_gone", read: reader({}), editor: () => undefined });
  assert.deepEqual(missing, { active_objects: [], view: { page: "theses" } });
  const broken = { thesis: () => { throw new Error("boom"); }, snapshots: () => { throw new Error("boom"); } };
  assert.deepEqual((await buildContext({ api: broken, hash: "#/theses/th_1", read: reader({}), editor: () => undefined })).active_objects, []);
  assert.deepEqual((await buildContext({ api: broken, hash: "#/market", read: reader({ market: { selected: ["snp_a"] } }), editor: () => undefined })).active_objects, []);
});

test("portfolio view: the selected portfolio, or the new-portfolio form", async () => {
  const open = await buildContext({ api: fakeApi, hash: "#/portfolio", read: reader({ portfolio: { selected: "main" } }), editor: (id) => ({ asked: id }) });
  assert.deepEqual(open.active_objects, [{ kind: "portfolio", object_id: "main", title: "main", currency: "EUR", holdings: 1 }]);
  assert.equal(open.view.editor.asked, "portfolio:main");
  const none = await buildContext({ api: fakeApi, hash: "#/portfolio", read: reader({ portfolio: { selected: "ghost" } }), editor: (id) => ({ asked: id }) });
  assert.deepEqual(none.active_objects, []);
  assert.deepEqual(none.view, { page: "portfolio", object_id: "new", editor: { asked: "portfolio:new" } });
});

test("lab view: open run and loaded strategy; experiments tab has none", async () => {
  const lab = await buildContext({ api: fakeApi, hash: "#/lab", read: reader({ lab: { tab: "backtest" }, "lab-spec": { strategyId: "st_1", runId: "bt_1" } }), editor: (id) => ({ asked: id }) });
  assert.deepEqual(lab.active_objects.map((o) => [o.kind, o.object_id]), [["experiment", "bt_1"], ["strategy", "st_1"]]);
  assert.equal(lab.view.editor.asked, "strategy:st_1");
  const opened = await buildContext({ api: fakeApi, hash: "#/lab", read: reader({ lab: { tab: "opened", openedId: "bt_1" } }), editor: () => undefined });
  assert.deepEqual(opened.active_objects.map((o) => o.object_id), ["bt_1"]);
  const list = await buildContext({ api: fakeApi, hash: "#/lab", read: reader({ lab: { tab: "experiments" } }), editor: () => undefined });
  assert.deepEqual(list, { active_objects: [], view: { page: "lab", tab: "experiments" } });
});

test("market view: only selected snapshots that exist", async () => {
  const out = await buildContext({ api: fakeApi, hash: "#/market", read: reader({ market: { selected: ["snp_a", "snp_zz"], tab: "manual" } }), editor: () => ({ kind: "snapshot-fetch-form" }) });
  assert.deepEqual(out.active_objects.map((o) => [o.kind, o.object_id, o.symbol]), [["snapshot", "snp_a", "AAPL"]]);
  assert.equal(out.view.tab, "manual");
});

test('market watchlist context keeps its draft before asynchronous native lookup', async () => {
  let draft = { kind: 'watchlist-form', dirty: true, fields: { name: 'Manual', filters: { lookback: 10 } } };
  const api = { ...fakeApi, watchlistView: async () => {
    draft.fields.name = 'Changed during lookup';
    return { watchlist: { id: 'wls_test', name: 'Saved', entries_count: 4 } };
  }};
  const out = await buildContext({ api, hash: '#/market', read: reader({ market: { selected: [] }, watchlist: { id: 'wls_test' } }),
    editor: (id) => id === 'watchlist:editor' ? draft : undefined });
  assert.equal(out.active_objects[0].kind, 'watchlist');
  assert.equal(out.active_objects[0].object_id, 'wls_test');
  assert.equal(out.view.watchlist_editor.fields.name, 'Manual');
});

test("editorContext collects the answer of the open form and tolerates none", () => {
  assert.equal(editorContext("x"), undefined);
  const off = (() => { const h = (e) => { if (e.detail.objectId === "x") e.detail.provide({ dirty: true }); }; window.addEventListener("midas:context-request", h); return () => window.removeEventListener("midas:context-request", h); })();
  assert.deepEqual(editorContext("x"), { dirty: true });
  assert.equal(editorContext("y"), undefined);
  off();
});

test("publishView cleanup only removes its own value", () => {
  const a = { n: 1 };
  const cleanA = publishView("k", a);
  const b = { n: 2 };
  publishView("k", b);
  cleanA();
  assert.equal(ctx.publishedView("k"), b);
});

test("turn end refreshes once even when the launcher fires both the event and the callback", () => {
  let n = 0;
  const off = onRefresh(() => { n += 1; });
  assert.equal(turnEnded(1_000_000), true);
  assert.equal(turnEnded(1_000_100), false);
  assert.equal(turnEnded(1_001_000), true);
  assert.equal(n, 2);
  off();
});

test("form snapshots and view state are captured before the fetches, not after them", async () => {
  let gate = null;
  const held = (fn) => (...args) => new Promise((resolve) => { gate = () => resolve(fn(...args)); });
  const slowApi = { ...fakeApi, thesis: held(fakeApi.thesis), portfolio: held(fakeApi.portfolio), strategy: held(fakeApi.strategy), snapshots: held(fakeApi.snapshots) };
  let draft = "before";
  const editor = (id) => ({ asked: id, text: draft });
  const run = async (hash, state, mutate) => {
    const pending = buildContext({ api: slowApi, hash, read: (key) => state[key], editor });
    draft = "typed while the fetch was pending";
    mutate();
    gate();
    const out = await pending;
    draft = "before";
    return out;
  };
  const theses = await run("#/theses/th_1", { theses: { tab: "evidence" } }, () => {});
  assert.deepEqual(theses.view.editor, { asked: "evidence:th_1", text: "before" });
  const pstate = { portfolio: { selected: "main" } };
  const portfolio = await run("#/portfolio", pstate, () => { pstate.portfolio.selected = "ghost"; });
  assert.equal(portfolio.view.object_id, "main");
  assert.deepEqual(portfolio.view.editor, { asked: "portfolio:main", text: "before" });
  const lstate = { lab: { tab: "backtest" }, "lab-spec": { strategyId: "st_1" } };
  const lab = await run("#/lab", lstate, () => { lstate["lab-spec"].strategyId = null; lstate.lab.tab = "experiments"; });
  assert.equal(lab.view.tab, "backtest");
  assert.deepEqual(lab.view.editor, { asked: "strategy:st_1", text: "before" });
  const market = await run("#/market", { market: { selected: ["snp_a"], tab: "manual" } }, () => {});
  assert.deepEqual(market.view.editor, { asked: "snapshot:new", text: "before" });
});

// ------------------------------------------------------------------ save conflict: rebase the draft, never discard it

const lotsOf = (...rows) => rows.map(([symbol, quantity, cost, id]) => ({ ...(id ? { lot_id: id } : {}), symbol, quantity, cost_basis: cost }));
const asRows = (ctxRef, holdings) => holdings.map((h, i) => ctxRef.rowFromHolding(h, `l${i}`));
const brief = (rows) => rows.map((r) => [r.symbol, r.quantity, String(r.cost_basis)]);

test("holdingsPatch addresses rows that carry a lot_id by it, and new rows never carry one", () => {
  const base = asRows(ctx, lotsOf(["AAA", 10, 100, "lot_a"], ["AAA", 20, 200, "lot_b"], ["BBB", 5, undefined, "lot_c"]));
  const draft = base.map((r) => ({ ...r }));
  draft[0].cost_basis = "120";
  draft.splice(2, 1); // BBB removed
  draft.push({ ...row("CCC", 1), _k: "n1", lot_id: "stale" }, { ...row("AAA", 3), _k: "n2" });
  assert.deepEqual(holdingsPatch(base, draft), {
    upsert: [{ symbol: "AAA", cost_basis: 120, lot_id: "lot_a" }, { symbol: "CCC", quantity: 1 }, { symbol: "AAA", quantity: 3, new_lot: true }],
    remove: [{ symbol: "BBB", lot_id: "lot_c" }],
  });
});

test("rebaseLots: the agent's quantity edit and the user's cost edit of the same lot compose, retried by lot_id", () => {
  const { rebaseLots } = ctx;
  const base = asRows(ctx, lotsOf(["AAA", 11, 100, "lot_a"], ["AAA", 20, 200, "lot_b"], ["BBB", 7, undefined, "lot_c"]));
  const draft = base.map((r, i) => (i === 0 ? { ...r, cost_basis: "110" } : { ...r }));
  const out = rebaseLots(base, draft, lotsOf(["AAA", 12, 100, "lot_a"], ["AAA", 20, 200, "lot_b"], ["BBB", 7, undefined, "lot_c"]));
  assert.deepEqual(out.ambiguous, []);
  assert.deepEqual(brief(out.rows), [["AAA", "12", "110"], ["AAA", "20", "200"], ["BBB", "7", ""]]);
  assert.deepEqual(brief(out.base), [["AAA", "12", "100"], ["AAA", "20", "200"], ["BBB", "7", ""]]);
  assert.deepEqual(holdingsPatch(out.base, out.rows), { upsert: [{ symbol: "AAA", cost_basis: 110, lot_id: "lot_a" }], remove: [] });
});

test("rebaseLots: a replaced lot keeps the draft, is reported, and writes nothing to the other AAA lot", () => {
  const { rebaseLots } = ctx;
  // saved AAA 12/110 (lot_a), AAA 20/200 (lot_b), BBB 7; the user edits the first AAA cost to 120; the agent removes lot_a and adds AAA 30/300
  const base = asRows(ctx, lotsOf(["AAA", 12, 110, "lot_a"], ["AAA", 20, 200, "lot_b"], ["BBB", 7, undefined, "lot_c"]));
  const draft = base.map((r, i) => (i === 0 ? { ...r, cost_basis: "120" } : { ...r }));
  const out = rebaseLots(base, draft, lotsOf(["AAA", 20, 200, "lot_b"], ["BBB", 7, undefined, "lot_c"], ["AAA", 30, 300, "lot_d"]));
  assert.deepEqual(out.ambiguous, ["AAA"]);
  // the draft survives as its own row; AAA 20/200 keeps its own values
  assert.deepEqual(brief(out.rows).sort(), [["AAA", "12", "120"], ["AAA", "20", "200"], ["AAA", "30", "300"], ["BBB", "7", ""]].sort());
  assert.ok(!out.rows.some((r) => r.lot_id === "lot_a"), "the dead id is gone");
  // whatever a later save sends, no edit to lot_b: the draft only re-adds its own row as a new lot
  const patch = holdingsPatch(out.base, out.rows);
  assert.deepEqual(patch.remove, []);
  assert.deepEqual(patch.upsert, [{ symbol: "AAA", quantity: 12, cost_basis: 120, new_lot: true }]);
  assert.ok(!JSON.stringify(patch).includes("lot_b"));
});

test("rebaseLots: untouched lots that vanished follow the server, new lots appear, removed rows stay removed", () => {
  const { rebaseLots } = ctx;
  const base = asRows(ctx, lotsOf(["AAA", 11, 100, "lot_a"], ["BBB", 7, undefined, "lot_b"], ["CCC", 1, undefined, "lot_c"]));
  const draft = [{ ...base[0] }, { ...base[1], quantity: "8" }]; // CCC removed by the user, AAA untouched
  const out = rebaseLots(base, draft, lotsOf(["BBB", 7, undefined, "lot_b"], ["CCC", 1, undefined, "lot_c"], ["DDD", 4, undefined, "lot_d"]));
  assert.deepEqual(out.ambiguous, []); // AAA was untouched and removed by the agent: nothing to keep
  assert.deepEqual(out.rows.map((r) => [r.symbol, r.quantity]), [["BBB", "8"], ["DDD", "4"]]);
  const patch = holdingsPatch(out.base, out.rows);
  assert.deepEqual(patch.remove, [{ symbol: "CCC", lot_id: "lot_c" }]);
  assert.deepEqual(patch.upsert, [{ symbol: "BBB", quantity: 8, lot_id: "lot_b" }]);
});

test("rebaseLots: legacy rows without a lot_id are ambiguous when touched and never auto-written", () => {
  const { rebaseLots } = ctx;
  const base = asRows(ctx, lotsOf(["AAA", 11, 100], ["AAA", 20, 200], ["BBB", 7]));
  const draft = [{ ...base[0], cost_basis: "110" }, { ...base[1] }, { ...base[2] }];
  const out = rebaseLots(base, draft, lotsOf(["AAA", 11, 100, "lot_x"], ["AAA", 20, 200, "lot_y"], ["BBB", 7, undefined, "lot_z"]));
  assert.deepEqual(out.ambiguous, ["AAA"]);
  assert.equal(out.rows.find((r) => r._k === "l0").cost_basis, "110");
  assert.deepEqual(out.base.filter((r) => r.symbol === "AAA").map((r) => r._k), ["l0", "l1"]); // old baseline kept, server AAA not adopted twice
  assert.equal(out.rows.filter((r) => r.symbol === "AAA").length, 2);
});

test("the save conflict path keeps the draft: it rebases and retries instead of forcing a reset", async () => {
  const { readFile } = await import("node:fs/promises");
  const src = await readFile(new URL("./src/pages/Portfolio.jsx", import.meta.url), "utf8");
  const save = src.slice(src.indexOf("const save = async"), src.indexOf("return (\n    <form onSubmit={save}"));
  assert.ok(save.includes("rebaseLots("), "the conflict is rebased");
  const failure = save.slice(save.indexOf("if (err?.code !== \"conflict\") throw err;"), save.indexOf("} else await api.portfolioSet"));
  assert.ok(!failure.includes("forceReset") && !failure.includes("onSaved("), "the conflict path neither discards nor reloads the form");
  assert.ok(save.includes("your edits are kept"));
});
