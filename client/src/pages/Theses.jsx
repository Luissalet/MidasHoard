import React, { useCallback, useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Chip, ConfirmButton, CopyButton, Empty, ErrorBox, Field, Issues, Provenance, StatePill, StatusPill, Tabs } from "../components/ui.jsx";
import { clock, num, splitList } from "../format.js";

const STATUSES = ["open", "confirmed", "invalidated", "expired", "archived"];

function RuleLines({ value, onChange }) {
  const { t } = useApp();
  const [checks, setChecks] = useState({});
  const lines = value.split("\n").map((l) => l.trim()).filter(Boolean);
  useEffect(() => {
    let live = true;
    const timer = setTimeout(async () => {
      const out = {};
      for (const line of lines) {
        try { out[line] = await api.ruleValidate(line); } catch { out[line] = { ok: false, issues: [{ message: "?" }] }; }
      }
      if (live) setChecks(out);
    }, 350);
    return () => { live = false; clearTimeout(timer); };
  }, [value]);
  return (
    <div className="space-y-1">
      <textarea className="field" rows={3} value={value} onChange={(e) => onChange(e.target.value)} placeholder={"close(aapl.us) < 150\nyoy(CPIAUCSL) > 4\nsma(aapl.us,50) < sma(aapl.us,200)"} aria-label={t("rules")} />
      {lines.map((line) => {
        const c = checks[line];
        if (!c) return null;
        return c.ok
          ? <div key={line} className="help"><span className="chip chip-ok">OK</span> <span className="mono">{line}</span> · {c.symbols.join(", ")}</div>
          : <div key={line} className="help"><span className="chip chip-danger">!</span> <span className="mono">{line}</span> — {c.issues[0].message}{c.issues[0].hint ? ` (${c.issues[0].hint})` : ""}</div>;
      })}
    </div>
  );
}

function CreateForm({ onCreated }) {
  const { t, notify } = useApp();
  const empty = { title: "", claim: "", rival: "", as_of: "", horizon: "", assets: "", assumptions: "", rules: "", notes: "" };
  const [f, setF] = useState(empty);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    try {
      const created = await api.thesisCreate({
        title: f.title, claim: f.claim, rival: f.rival, as_of: f.as_of || undefined, horizon: f.horizon, assets: splitList(f.assets),
        assumptions: f.assumptions.split("\n").map((s) => s.trim()).filter(Boolean), rules: f.rules.split("\n").map((s) => s.trim()).filter(Boolean), notes: f.notes,
      });
      notify(t("thesis_created"));
      setF(empty);
      setError(null);
      onCreated(created);
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };
  return (
    <form onSubmit={submit} className="panel space-y-2">
      <h2>{t("new_thesis")}</h2>
      <Field label={t("title")}><input className="field" required minLength={3} value={f.title} onChange={set("title")} /></Field>
      <Field label={t("claim")} hint={t("claim_hint")}><textarea className="field" rows={2} required value={f.claim} onChange={set("claim")} /></Field>
      <Field label={t("rival")} hint={t("rival_hint")}><textarea className="field" rows={2} required minLength={12} value={f.rival} onChange={set("rival")} /></Field>
      <div className="grid grid-cols-2 gap-2">
        <Field label={t("as_of")} hint={t("thesis_as_of_hint")}><input className="field" type="date" value={f.as_of} onChange={set("as_of")} /></Field>
        <Field label={t("horizon")} hint="6m · 12m · 2y · 2026-12-31"><input className="field" value={f.horizon} onChange={set("horizon")} /></Field>
      </div>
      <Field label={t("assets")} hint={t("assets_hint")}><input className="field" value={f.assets} onChange={set("assets")} placeholder="aapl.us, ^spx" /></Field>
      <Field label={t("rules")} hint={t("rules_hint")}><RuleLines value={f.rules} onChange={(v) => setF({ ...f, rules: v })} /></Field>
      <Field label={t("assumptions")}><textarea className="field" rows={2} value={f.assumptions} onChange={set("assumptions")} /></Field>
      <ErrorBox error={error} t={t} />
      <button className="btn btn-primary" disabled={busy}>{t("create")}</button>
    </form>
  );
}

function EvidenceForm({ thesis, snapshots, onDone }) {
  const { t, notify } = useApp();
  const [f, setF] = useState({ side: "for", kind: "note", title: "", quote: "", ref: "", snapshot_id: "", expr: "" });
  const [error, setError] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const submit = async (e) => {
    e.preventDefault();
    try {
      const body = { side: f.side, kind: f.kind, title: f.title, quote: f.quote, ref: f.ref, snapshot_id: f.kind === "snapshot_metric" ? f.snapshot_id : "", expr: f.kind === "snapshot_metric" ? f.expr : "" };
      await api.evidenceAdd(thesis.id, body);
      setF({ ...f, title: "", quote: "", ref: "", expr: "" });
      setError(null);
      notify(t("evidence_added"));
      onDone();
    } catch (err) { setError(err); }
  };
  return (
    <form onSubmit={submit} className="panel space-y-2">
      <h3>{t("add_evidence")}</h3>
      <div className="grid grid-cols-2 gap-2">
        <Field label={t("side")}><select className="field" value={f.side} onChange={set("side")}><option value="for">{t("for")}</option><option value="against">{t("against")}</option></select></Field>
        <Field label={t("kind")}><select className="field" value={f.kind} onChange={set("kind")}>{["note", "url", "snapshot_metric", "family_ref"].map((k) => <option key={k} value={k}>{t(`kind_${k}`)}</option>)}</select></Field>
      </div>
      {f.kind === "snapshot_metric" && (
        <div className="grid grid-cols-2 gap-2">
          <Field label={t("snapshot")}><select className="field" required value={f.snapshot_id} onChange={set("snapshot_id")}><option value="">—</option>{snapshots.map((s) => <option key={s.id} value={s.id}>{s.symbol} ({s.actual_end})</option>)}</select></Field>
          <Field label={t("metric_expr")} hint={t("metric_hint")}><input className="field" value={f.expr} onChange={set("expr")} placeholder="yoy(CPIAUCSL)" /></Field>
        </div>
      )}
      {(f.kind === "url" || f.kind === "family_ref") && <Field label={t("reference")}><input className="field" required value={f.ref} onChange={set("ref")} placeholder={f.kind === "url" ? "https://…" : "hoard://app/kind/id"} /></Field>}
      <Field label={t("title")}><input className="field" value={f.title} onChange={set("title")} /></Field>
      {f.kind !== "snapshot_metric" && <Field label={t("quote")}><textarea className="field" rows={2} value={f.quote} onChange={set("quote")} /></Field>}
      <ErrorBox error={error} t={t} />
      <button className="btn btn-sm btn-primary">{t("add")}</button>
    </form>
  );
}

function CommitteeView({ run, t }) {
  if (!run) return null;
  if (run.mode !== "model") {
    return (
      <div className="panel space-y-2">
        <h3>{t("committee")} <Chip className="chip chip-amber">{t("mode_material")}</Chip></h3>
        <p className="help">{run.reason}</p>
        <p>{run.instructions}</p>
        <pre className="mono m-0 max-h-[240px] overflow-auto rounded p-2" style={{ background: "var(--field)" }}>{JSON.stringify(run.deterministic, null, 2)}</pre>
      </div>
    );
  }
  const Section = ({ title, items }) => (
    <div>
      <h3 className="mb-1">{title}</h3>
      <ul className="m-0 space-y-1 pl-4">
        {items.map((it, i) => (
          <li key={i}>
            {it.text} {it.evidence.map((e) => <Chip key={e} className="chip chip-accent">{e}</Chip>)}
            {it.uncited && <Chip className="chip chip-amber">{t("uncited")}</Chip>}
            {it.ledger && !it.ledger.ok && <Chip className="chip chip-danger" title={it.ledger.unmatched.map((u) => u.text).join(", ")}>{t("unverified_numbers")}: {it.ledger.unmatched.map((u) => u.text).join(", ")}</Chip>}
          </li>
        ))}
      </ul>
    </div>
  );
  return (
    <div className="panel space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h3>{t("committee")}</h3>
        <Chip className={run.verdict === "strengthen" ? "chip chip-ok" : run.verdict === "weaken" ? "chip chip-danger" : "chip chip-amber"}>{t(`verdict_${run.verdict}`)}</Chip>
        <Chip>{t("confidence")} {run.confidence}</Chip>
        <Chip>{run.model}</Chip>
      </div>
      <p className="help">{run.method}</p>
      <div className="grid gap-3 md:grid-cols-2"><Section title={t("advocate_for")} items={run.advocate_for} /><Section title={t("advocate_against")} items={run.advocate_against} /></div>
      <Section title={t("risk_review")} items={run.risk_review} />
      <Section title={t("arbiter")} items={run.arbiter.reasons} />
      {run.arbiter.missing_evidence.length > 0 && <div><h3>{t("missing_evidence")}</h3><ul className="m-0 pl-4">{run.arbiter.missing_evidence.map((m, i) => <li key={i}>{m}</li>)}</ul></div>}
      <div className={`banner ${run.ledger.ok ? "" : "banner-warn"}`}>{run.ledger.ok ? t("ledger_ok") : `${t("ledger_unmatched")}: ${run.ledger.unmatched.length}`}<span className="help block">{run.ledger.note}</span></div>
      {run.flags.map((f, i) => <div key={i} className="help">⚑ {f}</div>)}
    </div>
  );
}

function Detail({ id, snapshots, onChanged, onDeleted }) {
  const { t, lang, notify } = useApp();
  const [th, setTh] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState("");
  const [checkDate, setCheckDate] = useState("");
  const [refresh, setRefresh] = useState(true);
  const [useModel, setUseModel] = useState(true);
  const [committee, setCommittee] = useState(null);
  const [report, setReport] = useState(null);
  const [tab, setTab] = useState("rules");

  const load = useCallback(async () => {
    try { const d = await api.thesis(id); setTh(d); setError(null); } catch (e) { setError(e); }
  }, [id]);
  useEffect(() => { setCommittee(null); setReport(null); load(); }, [load]);

  const run = async (name, fn) => {
    setBusy(name);
    try { const r = await fn(); setError(null); await load(); onChanged(); return r; } catch (e) { setError(e); return null; } finally { setBusy(""); }
  };
  const check = () => run("check", async () => {
    const r = await api.thesisCheck(id, { as_of: checkDate || undefined, refresh });
    notify(r.reused ? t("check_reused") : r.tripped ? t("check_tripped") : t("check_done"));
  });
  const askCommittee = () => run("committee", async () => { setCommittee(await api.committee(id, { use_model: useModel })); });
  const makeReport = () => run("report", async () => { setReport(await api.report({ kind: "thesis", id, format: "md", save: true })); });

  if (!th) return <div className="help">{error ? <ErrorBox error={error} t={t} /> : "…"}</div>;
  const last = th.last_check;
  const stateOf = (rid) => last?.results.find((r) => r.rule_id === rid);

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0"><h1 className="break-words">{th.title}</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2"><StatusPill status={th.status} t={t} /><span className="chip">{t("as_of")} {th.as_of}</span>{th.horizon && <span className="chip">{t("horizon")} {th.horizon}{th.horizon_end ? ` → ${th.horizon_end}` : ""}</span>}<span className="mono help">{th.ref}</span></div></div>
        <div className="flex flex-wrap items-center gap-2">
          <select className="field" style={{ width: 130 }} value={th.status} onChange={(e) => run("status", () => api.thesisUpdate(id, { status: e.target.value }))} aria-label={t("status")}>{STATUSES.map((s) => <option key={s} value={s}>{t(`status_${s}`)}</option>)}</select>
          <ConfirmButton t={t} onConfirm={async () => { try { await api.thesisDelete(id); onDeleted(); } catch (e) { setError(e); } }} />
        </div>
      </div>
      <ErrorBox error={error} t={t} />
      <div className="grid gap-3 md:grid-cols-2">
        <div className="panel"><span className="label">{t("claim")}</span><p className="m-0">{th.claim}</p></div>
        <div className="panel"><span className="label">{t("rival")}</span><p className="m-0">{th.rival}</p></div>
      </div>
      {(th.assumptions.length > 0 || th.assets.length > 0) && (
        <div className="flex flex-wrap gap-2 text-[12px]">
          {th.assets.map((a, i) => <Chip key={i} className="chip chip-accent">{a.symbol}</Chip>)}
          {th.assumptions.map((a, i) => <Chip key={i} className="chip chip-wrap">{a}</Chip>)}
        </div>
      )}
      <div className="panel flex flex-wrap items-end gap-3">
        <label className="block w-[150px]"><span className="label">{t("check_date")}</span><input className="field" type="date" value={checkDate} min={th.as_of} onChange={(e) => setCheckDate(e.target.value)} /></label>
        <label className="flex items-center gap-2 pb-1 text-[12px]"><input type="checkbox" checked={refresh} onChange={(e) => setRefresh(e.target.checked)} />{t("refresh_data")}</label>
        <button type="button" className="btn btn-primary" disabled={!!busy} onClick={check}>{busy === "check" ? "…" : t("check_now")}</button>
        <span className="mx-1 h-6 w-px" style={{ background: "var(--line)" }} />
        <label className="flex items-center gap-2 pb-1 text-[12px]"><input type="checkbox" checked={useModel} onChange={(e) => setUseModel(e.target.checked)} />{t("use_model")}</label>
        <button type="button" className="btn" disabled={!!busy} onClick={askCommittee}>{busy === "committee" ? "…" : t("run_committee")}</button>
        <button type="button" className="btn" disabled={!!busy} onClick={makeReport}>{t("report")}</button>
      </div>
      {last?.warnings?.length > 0 && last.warnings.map((w, i) => <div key={i} className="banner banner-warn">{w}</div>)}
      <Tabs tabs={[{ key: "rules", label: `${t("rules")} (${th.rules.length})` }, { key: "evidence", label: `${t("evidence")} (${th.evidence.length})` }, { key: "history", label: t("history") }]} active={tab} onChange={setTab} />
      {tab === "rules" && (
        <div className="panel overflow-x-auto">
          {th.rules.length === 0 ? <p className="help">{t("no_rules")}</p> : (
            <table>
              <thead><tr><th>{t("rule")}</th><th>{t("state")}</th><th>{t("operands")}</th><th>{t("data_until")}</th></tr></thead>
              <tbody>{th.rules.map((r) => {
                const res = stateOf(r.id);
                return (
                  <tr key={r.id}>
                    <td><span className="mono">{r.expr}</span><div className="help">{t("rule_hint_true")}</div></td>
                    <td><StatePill state={res?.state || "unchecked"} t={t} /></td>
                    <td className="num mono">{res?.terms?.map((x, i) => <div key={i}>{num(x.lhs, 2, lang)} {x.op} {num(x.rhs, 2, lang)}</div>)}</td>
                    <td className="mono">{res ? Object.entries(res.data_dates || {}).map(([k, v]) => <div key={k}>{k}: {v}</div>) : "—"}</td>
                  </tr>
                );
              })}</tbody>
            </table>
          )}
        </div>
      )}
      {tab === "evidence" && (
        <div className="grid gap-3 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="space-y-2">
            {th.evidence.length === 0 && <Empty>{t("no_evidence")}</Empty>}
            {th.evidence.map((e) => (
              <div key={e.id} className="panel space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <Chip className="chip chip-accent">{e.label}</Chip>
                  <Chip className={e.side === "for" ? "chip chip-ok" : "chip chip-danger"}>{t(e.side)}</Chip>
                  <Chip>{t(`kind_${e.kind}`)}</Chip>
                  <b>{e.title}</b>
                  <span className="flex-1" />
                  <ConfirmButton t={t} onConfirm={() => run("ev", () => api.evidenceDelete(id, e.id))} />
                </div>
                {e.quote && <p className="m-0">{e.quote}</p>}
                {e.metric?.expr && <p className="m-0 num mono">{e.metric.expr} = {num(e.metric.value, 4, lang)} · {t("as_of")} {e.metric.as_of}</p>}
                {e.ref && <p className="help m-0 mono">{e.ref}</p>}
              </div>
            ))}
          </div>
          <EvidenceForm thesis={th} snapshots={snapshots} onDone={() => { load(); onChanged(); }} />
        </div>
      )}
      {tab === "history" && (
        <div className="panel overflow-x-auto">
          <table>
            <thead><tr><th>{t("date")}</th><th>{t("check_date")}</th><th>{t("result")}</th><th>{t("status")}</th></tr></thead>
            <tbody>
              {(th.checks || []).map((c) => (
                <tr key={c.id}><td>{clock(c.ts, lang)}</td><td className="mono">{c.as_of}</td><td>{c.tripped ? <Chip className="chip chip-danger">{t("state_tripped")}</Chip> : <Chip className="chip chip-ok">{t("state_clear")}</Chip>}</td><td>{t(`status_${c.status_after}`)}</td></tr>
              ))}
              {(!th.checks || th.checks.length === 0) && <tr><td colSpan={4} className="help">{t("no_checks")}</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      <CommitteeView run={committee} t={t} />
      {!committee && th.committee?.length > 0 && (
        <p className="help">{t("last_committee")}: {th.committee[0].mode} · {th.committee[0].verdict || "—"} · {clock(th.committee[0].created_at, lang)}</p>
      )}
      {report && (
        <div className="panel space-y-2">
          <div className="flex items-center gap-2"><h3>{t("report")}</h3><CopyButton value={report.markdown} t={t} />{report.path && <span className="help mono">{report.path}</span>}</div>
          <pre className="mono m-0 max-h-[360px] overflow-auto whitespace-pre-wrap rounded p-2" style={{ background: "var(--field)" }}>{report.markdown}</pre>
        </div>
      )}
    </div>
  );
}

export default function Theses({ param }) {
  const { t } = useApp();
  const [list, setList] = useState([]);
  const [snapshots, setSnapshots] = useState([]);
  const [status, setStatus] = useState("");
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState(null);
  const selected = param;

  const load = useCallback(async () => {
    try {
      const [l, s] = await Promise.all([api.theses({ status: status || undefined, query: query || undefined, limit: 100 }), api.snapshots({ limit: 100 })]);
      setList(l.theses);
      setSnapshots(s.snapshots);
      setError(null);
    } catch (e) { setError(e); }
  }, [status, query]);
  useEffect(() => { load(); }, [load]);

  const open = (id) => { window.location.hash = id ? `#/theses/${id}` : "#/theses"; };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between"><h1>{t("nav_theses")}</h1><button type="button" className="btn btn-primary" onClick={() => { setCreating(true); open(null); }}>{t("new_thesis")}</button></div>
      <ErrorBox error={error} t={t} />
      <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
        <div className="space-y-2">
          <div className="flex gap-2">
            <input className="field" placeholder={t("filter")} value={query} onChange={(e) => setQuery(e.target.value)} aria-label={t("filter")} />
            <select className="field" style={{ width: 120 }} value={status} onChange={(e) => setStatus(e.target.value)} aria-label={t("status")}><option value="">{t("all")}</option>{STATUSES.map((s) => <option key={s} value={s}>{t(`status_${s}`)}</option>)}</select>
          </div>
          {list.length === 0 && <Empty>{t("theses_empty")}</Empty>}
          <ul className="m-0 list-none space-y-1.5 p-0">
            {list.map((th) => (
              <li key={th.id}>
                <button type="button" onClick={() => { setCreating(false); open(th.id); }} className="panel block w-full text-left" style={{ borderColor: th.id === selected && !creating ? "var(--accent)" : undefined }}>
                  <div className="flex items-center justify-between gap-2"><b className="truncate">{th.title}</b><StatusPill status={th.status} t={t} /></div>
                  <div className="help truncate">{th.assets.join(", ") || "—"} · {th.as_of}</div>
                  <div className="mt-1 flex flex-wrap gap-1">{th.rule_states.map((s, i) => <StatePill key={i} state={s} t={t} />)}<Chip>{th.evidence_counts.for}+ / {th.evidence_counts.against}−</Chip></div>
                </button>
              </li>
            ))}
          </ul>
        </div>
        <div className="min-w-0">
          {creating ? <CreateForm onCreated={(c) => { setCreating(false); load(); open(c.id); }} /> : selected ? (
            <Detail key={selected} id={selected} snapshots={snapshots} onChanged={load} onDeleted={() => { load(); open(null); }} />
          ) : <Empty>{t("theses_pick")}</Empty>}
        </div>
      </div>
    </div>
  );
}
