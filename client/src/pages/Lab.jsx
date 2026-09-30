import React, { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Chip, ConfirmButton, Empty, ErrorBox, Field, Issues, Kpi, Legend, LineChart, NullBand, Tabs } from "../components/ui.jsx";
import { SERIES_COLORS, clock, num, pct, signClass } from "../format.js";

const WARN_CLASS = { none: "chip", low: "chip chip-amber", medium: "chip chip-amber", high: "chip chip-danger" };

function VariantsBanner({ variants, mt, t }) {
  if (!variants) return null;
  const level = variants.warning_level || "none";
  return (
    <div className={`banner ${level === "none" ? "" : level === "high" ? "banner-danger" : "banner-warn"}`}>
      <b>{t("variants_tried")}: {variants.tried}</b> ({variants.family}) — {t("variants_note")}
      {mt && mt.variants_tried > 1 && (
        <span className="block num">
          {t("sharpe")} {num(mt.sharpe_annualised, 2)} → {t("haircut_sharpe")} {num(mt.haircut_sharpe_annualised, 2)} · p {num(mt.p_value, 4)} → {t("bonferroni")} {num(mt.p_value_bonferroni, 4)}
          {mt.deflated_sharpe ? ` · ${t("deflated")} ${num(mt.deflated_sharpe.probability, 3)}` : ""}
        </span>
      )}
    </div>
  );
}

function ValidationView({ v, t, lang }) {
  const r = v.results;
  return (
    <div className="space-y-3">
      <div className="help">{v.validation_id} · {t("as_of")} {v.as_of} · seed {v.seed}</div>
      {r.permutation && (
        <div className="panel space-y-1">
          <h3>{t("permutation")}</h3>
          <p className="m-0">p = <b className="num">{num(r.permutation.p_value, 4, lang)}</b> <span className="help">(n = {r.permutation.n}, {t("block")} {r.permutation.block})</span></p>
          <NullBand low={r.permutation.null_p05} high={r.permutation.null_p95} mean={r.permutation.null_mean} observed={r.permutation.observed} />
          <p className="help m-0">{r.permutation.reading}</p>
          <p className="help m-0">{r.permutation.note}</p>
        </div>
      )}
      {r.bootstrap && (
        <div className="panel space-y-1">
          <h3>{t("bootstrap")}</h3>
          <p className="m-0 num">{t("sharpe")} {num(r.bootstrap.sharpe, 2, lang)} · {Math.round(r.bootstrap.level * 100)} % CI [{num(r.bootstrap.ci_low, 2, lang)}, {num(r.bootstrap.ci_high, 2, lang)}] {r.bootstrap.includes_zero && <Chip className="chip chip-amber">{t("includes_zero")}</Chip>}</p>
          <p className="help m-0">{r.bootstrap.note}</p>
        </div>
      )}
      {r.walk_forward && (
        <div className="panel space-y-2 overflow-x-auto">
          <h3>{t("walk_forward")} <span className="help">{r.walk_forward.positive_windows}/{r.walk_forward.of_windows} {t("positive_windows")} · {t("median_oos_sharpe")} {num(r.walk_forward.median_oos_sharpe, 2, lang)}</span></h3>
          <table className="num">
            <thead><tr><th>#</th><th>{t("train_end")}</th><th>{t("test")}</th><th className="r">{t("return")}</th><th className="r">{t("sharpe")}</th><th className="r">{t("max_drawdown")}</th><th className="r">{t("trades")}</th><th className="r">{t("benchmark")}</th><th>{t("params")}</th></tr></thead>
            <tbody>{r.walk_forward.windows.map((w) => (
              <tr key={w.window}><td>{w.window}</td><td className="mono">{w.train_end}</td><td className="mono">{w.test_start} → {w.test_end}</td>
                <td className={`r ${signClass(w.oos_total_return)}`}>{pct(w.oos_total_return, 1, lang, true)}</td><td className="r">{num(w.oos_sharpe, 2, lang)}</td><td className="r">{pct(w.oos_max_drawdown, 1, lang)}</td><td className="r">{w.oos_trades}</td><td className="r">{pct(w.oos_benchmark_return, 1, lang, true)}</td>
                <td className="mono">{w.chosen_params && Object.keys(w.chosen_params).length ? JSON.stringify(w.chosen_params) : "—"}</td></tr>
            ))}</tbody>
          </table>
          {r.walk_forward.grid && <p className="help m-0">{t("grid")}: {r.walk_forward.grid.paths.join(", ")} · {r.walk_forward.grid.candidates} {t("candidates")}</p>}
          <p className="help m-0">{r.walk_forward.note}</p>
        </div>
      )}
      {r.multiple_testing && (
        <div className="panel space-y-1">
          <h3>{t("multiple_testing")} <Chip className={WARN_CLASS[r.multiple_testing.warning_level] || "chip"}>{r.multiple_testing.warning_level}</Chip></h3>
          <p className="m-0 num">{r.multiple_testing.variants_tried} {t("variants")} · t = {num(r.multiple_testing.t_stat, 2, lang)} · p = {num(r.multiple_testing.p_value, 4, lang)} → Bonferroni {num(r.multiple_testing.p_value_bonferroni, 4, lang)}</p>
          <p className="m-0 num">{t("sharpe")} {num(r.multiple_testing.sharpe_annualised, 2, lang)} → {t("haircut_sharpe")} {num(r.multiple_testing.haircut_sharpe_annualised, 2, lang)}</p>
          {r.multiple_testing.deflated_sharpe ? <p className="m-0 num">{t("deflated")}: {num(r.multiple_testing.deflated_sharpe.probability, 3, lang)} ({r.multiple_testing.deflated_sharpe.trials} {t("trials")}, {t("expected_max")} {num(r.multiple_testing.deflated_sharpe.expected_max_sharpe_annualised, 2, lang)})</p> : <p className="help m-0">{t("deflated_needs")}</p>}
        </div>
      )}
      {r.holdout && (
        <div className={`banner ${r.holdout.status === "opened" ? "banner-warn" : ""}`}>
          <b>{t("holdout")}: {t(`holdout_${r.holdout.status}`)}</b> {r.holdout.peek_number ? `· ${t("peek")} #${r.holdout.peek_number}` : ""}
          <span className="block">{r.holdout.note}</span>
          {r.holdout.metrics && <span className="block num">{t("return")} {pct(r.holdout.metrics.total_return, 1, lang, true)} · {t("sharpe")} {num(r.holdout.metrics.sharpe, 2, lang)} · {t("max_drawdown")} {pct(r.holdout.metrics.max_drawdown, 1, lang)} · {r.holdout.metrics.trades} {t("trades").toLowerCase()} · {t("benchmark")} {pct(r.holdout.benchmark?.total_return, 1, lang, true)}</span>}
        </div>
      )}
    </div>
  );
}

function RunView({ run, onValidated }) {
  const { t, lang, notify } = useApp();
  const [methods, setMethods] = useState({ walk_forward: true, permutation: true, bootstrap: true, multiple_testing: true });
  const [windows, setWindows] = useState(5);
  const [n, setN] = useState(1000);
  const [seed, setSeed] = useState(0);
  const [grid, setGrid] = useState("");
  const [validations, setValidations] = useState([]);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => { setValidations([]); setError(null); }, [run.run_id]);

  const validate = async (reveal) => {
    setBusy(true);
    try {
      let parsedGrid;
      if (grid.trim()) parsedGrid = JSON.parse(grid);
      const res = await api.backtestValidate(run.run_id, { methods: Object.keys(methods).filter((k) => methods[k]), windows: Number(windows), n: Number(n), seed: Number(seed), grid: parsedGrid, reveal_holdout: reveal });
      setValidations((v) => [res, ...v]);
      setError(null);
      onValidated();
    } catch (e) { setError(e); } finally { setBusy(false); }
  };

  const m = run.metrics;
  const b = run.benchmark;
  const eq = run.equity || [];
  const series = [
    { key: "eq", name: t("strategy"), color: SERIES_COLORS[0], points: eq.map((p) => [p.date, p.equity]) },
    { key: "bm", name: `${t("benchmark")} (${b?.symbol || ""})`, color: SERIES_COLORS[1], dashed: true, points: eq.map((p) => [p.date, p.benchmark ?? p.benchmark_equity]) },
  ];
  const markers = run.holdout?.holdout_start ? [{ date: run.holdout.holdout_start, label: t("holdout"), color: "var(--warn)" }] : [];
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <h2>{run.strategy || run.family}</h2><span className="mono help">{run.run_id}</span>
        <Chip className="chip chip-accent">{run.window.start} → {run.window.end}</Chip><Chip>{run.window.bars} {t("bars").toLowerCase()}</Chip>
        {run.holdout && <Chip className={run.holdout.status === "opened" ? "chip chip-amber" : "chip chip-ok"}>{t(`holdout_${run.holdout.status}`)}</Chip>}
      </div>
      <VariantsBanner variants={run.variants} mt={run.multiple_testing} t={t} />
      {run.warnings?.map((w, i) => <div key={i} className="banner banner-warn">{w}</div>)}
      <div className="kpis">
        <Kpi label={t("total_return")} value={pct(m.total_return, 1, lang, true)} tone={signClass(m.total_return)} />
        <Kpi label="CAGR" value={pct(m.cagr, 1, lang, true)} tone={signClass(m.cagr)} />
        <Kpi label={t("sharpe")} value={num(m.sharpe, 2, lang)} />
        <Kpi label="Sortino" value={num(m.sortino, 2, lang)} />
        <Kpi label={t("max_drawdown")} value={pct(m.max_drawdown, 1, lang)} tone="neg" />
        <Kpi label={t("trades")} value={m.trades} />
        <Kpi label={t("hit_rate")} value={pct(m.hit_rate, 0, lang)} />
        <Kpi label={t("exposure")} value={pct(m.exposure, 0, lang)} />
        <Kpi label={t("benchmark")} value={pct(b?.total_return, 1, lang, true)} tone={signClass(b?.total_return)} />
        <Kpi label={t("excess")} value={pct(b?.excess_total_return, 1, lang, true)} tone={signClass(b?.excess_total_return)} />
        <Kpi label={t("turnover")} value={num(m.turnover_per_year, 1, lang)} />
        <Kpi label={t("costs_paid")} value={num(m.costs_paid, 0, lang)} />
        <Kpi label={t("calmar")} value={num(m.calmar, 2, lang)} />
        <Kpi label={t("volatility")} value={pct(m.ann_vol, 1, lang)} />
        <Kpi label={t("dd_duration")} value={`${m.max_drawdown_duration_days ?? "—"} d`} />
        <Kpi label={t("beta")} value={num(b?.beta, 2, lang)} />
      </div>
      <div className="panel space-y-1">
        <Legend items={series.map((s) => ({ name: s.name, color: s.color }))} />
        <LineChart series={series} markers={markers} logScale yFormat={(v) => num(v, 0, lang)} label={t("equity")} />
        <LineChart series={[{ key: "dd", name: "drawdown", color: SERIES_COLORS[2], points: eq.map((p) => [p.date, (p.drawdown ?? 0) * 100]) }]} fill height={110} yFormat={(v) => `${num(v, 1, lang)} %`} label={t("drawdown")} />
      </div>
      <div className="panel overflow-x-auto">
        <h3 className="mb-1">{t("trades")} <span className="help">({run.trades_total ?? run.trades?.length ?? 0})</span></h3>
        <table className="num">
          <thead><tr><th>{t("asset")}</th><th>{t("side")}</th><th>{t("entry")}</th><th>{t("exit")}</th><th className="r">{t("bars")}</th><th className="r">{t("return")}</th></tr></thead>
          <tbody>{(run.trades || []).slice(0, 40).map((x, i) => (
            <tr key={i}><td>{x.asset}</td><td>{x.side}</td><td className="mono">{x.entry_date} @ {num(x.entry_price, 2, lang)}</td><td className="mono">{x.open ? t("open_trade") : `${x.exit_date} @ ${num(x.exit_price, 2, lang)}`}</td><td className="r">{x.bars}</td><td className={`r ${signClass(x.return)}`}>{pct(x.return, 1, lang, true)}</td></tr>
          ))}</tbody>
        </table>
      </div>
      <div className="panel space-y-2">
        <h2>{t("validate_run")}</h2>
        <div className="flex flex-wrap gap-3">
          {Object.keys(methods).map((k) => <label key={k} className="flex items-center gap-1.5 text-[12.5px]"><input type="checkbox" checked={methods[k]} onChange={(e) => setMethods({ ...methods, [k]: e.target.checked })} />{t(k)}</label>)}
        </div>
        <div className="grid grid-cols-3 gap-2 sm:max-w-[420px]">
          <Field label={t("windows")}><input className="field" type="number" min={2} max={12} value={windows} onChange={(e) => setWindows(e.target.value)} /></Field>
          <Field label="N"><input className="field" type="number" min={100} max={20000} step={100} value={n} onChange={(e) => setN(e.target.value)} /></Field>
          <Field label="seed"><input className="field" type="number" min={0} value={seed} onChange={(e) => setSeed(e.target.value)} /></Field>
        </div>
        <Field label={t("grid")} hint={t("grid_hint")}><input className="field mono" value={grid} onChange={(e) => setGrid(e.target.value)} placeholder='{"indicators.fast.window": [10, 20, 50]}' /></Field>
        <ErrorBox error={error} t={t} />
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => validate(false)}>{busy ? "…" : t("validate")}</button>
          {run.holdout && run.holdout.status !== undefined && (
            <ConfirmButton t={t} label={t("reveal_holdout")} confirmLabel={t("reveal_confirm")} className="btn btn-danger" onConfirm={() => validate(true)} />
          )}
        </div>
        {run.holdout && <p className="help m-0">{t("reveal_hint")}</p>}
      </div>
      {validations.map((v) => <ValidationView key={v.validation_id} v={v} t={t} lang={lang} />)}
    </div>
  );
}

function BacktestTab({ snapshots, refreshLog }) {
  const { t, notify } = useApp();
  const [text, setText] = useState("");
  const [issues, setIssues] = useState(null);
  const [error, setError] = useState(null);
  const [holdout, setHoldout] = useState("0.2");
  const [split, setSplit] = useState("");
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState(null);
  const [strategies, setStrategies] = useState([]);

  const loadStrategies = useCallback(() => api.strategies().then((s) => setStrategies(s.strategies)).catch(() => {}), []);
  useEffect(() => { loadStrategies(); }, [loadStrategies]);

  const loadExample = async () => {
    const { spec } = await api.labExample();
    const first = snapshots[0];
    if (first) spec.universe = [first.id];
    setText(JSON.stringify(spec, null, 2));
    setIssues(null);
  };
  const parse = () => { try { return JSON.parse(text); } catch (e) { setError(new Error(`${t("invalid_json")}: ${e.message}`)); return null; } };
  const addToUniverse = (id) => {
    const spec = parse();
    if (!spec) return;
    spec.universe = Array.from(new Set([...(spec.universe || []).filter((x) => !String(x).startsWith("snp_xxx")), id]));
    setText(JSON.stringify(spec, null, 2));
  };
  const validate = async () => {
    const spec = parse(); if (!spec) return;
    setError(null);
    const r = await api.labValidate(spec);
    setIssues(r.ok ? [] : r.issues);
    if (r.ok) notify(t("spec_ok"));
  };
  const save = async () => {
    const spec = parse(); if (!spec) return;
    try { const r = await api.strategySave(spec, note); notify(`${t("saved")}: ${r.name}`); setError(null); loadStrategies(); } catch (e) { setError(e); if (e.issues) setIssues(e.issues); }
  };
  const runIt = async () => {
    const spec = parse(); if (!spec) return;
    setBusy(true);
    try {
      const body = { spec };
      if (split) body.split_date = split; else if (holdout) body.holdout_fraction = Number(holdout);
      setRun(await api.backtest(body));
      setError(null); setIssues(null);
      refreshLog();
    } catch (e) { setError(e); if (e.issues) setIssues(e.issues); } finally { setBusy(false); }
  };
  const loadStrategy = async (id) => { const s = await api.strategy(id); setText(JSON.stringify(s.spec, null, 2)); setIssues(null); };

  return (
    <div className="grid gap-4 xl:grid-cols-[400px_minmax(0,1fr)]">
      <div className="space-y-2">
        <div className="panel space-y-2">
          <div className="flex items-center justify-between"><h2>{t("strategy_spec")}</h2><button type="button" className="btn btn-sm" onClick={loadExample}>{t("load_example")}</button></div>
          <textarea className="field" rows={20} spellCheck={false} value={text} onChange={(e) => setText(e.target.value)} aria-label={t("strategy_spec")} placeholder={t("spec_placeholder")} />
          <div className="flex flex-wrap gap-1">{snapshots.slice(0, 12).map((s) => <button key={s.id} type="button" className="chip" onClick={() => addToUniverse(s.id)} title={s.id}>+ {s.symbol}</button>)}</div>
          <Issues issues={issues} t={t} />
          <ErrorBox error={error} t={t} />
          <div className="grid grid-cols-2 gap-2">
            <Field label={t("holdout_fraction")}><select className="field" value={holdout} onChange={(e) => setHoldout(e.target.value)}><option value="">{t("none")}</option><option value="0.2">20 %</option><option value="0.3">30 %</option><option value="0.4">40 %</option></select></Field>
            <Field label={t("split_date")}><input className="field" type="date" value={split} onChange={(e) => setSplit(e.target.value)} /></Field>
          </div>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn" onClick={validate} disabled={!text}>{t("validate_spec")}</button>
            <button type="button" className="btn btn-primary" onClick={runIt} disabled={!text || busy}>{busy ? "…" : t("run_backtest")}</button>
          </div>
          <div className="flex gap-2"><input className="field" placeholder={t("strategy_note")} value={note} onChange={(e) => setNote(e.target.value)} /><button type="button" className="btn" onClick={save} disabled={!text}>{t("save_strategy")}</button></div>
        </div>
        <div className="panel space-y-1">
          <h3>{t("strategies")} <span className="help">({strategies.length})</span></h3>
          {strategies.length === 0 && <p className="help m-0">{t("strategies_empty")}</p>}
          {strategies.map((s) => (
            <div key={s.id} className="flex items-center justify-between gap-2">
              <button type="button" className="btn-link truncate text-left" onClick={() => loadStrategy(s.id)}>{s.name}</button>
              <span className="flex items-center gap-2"><Chip>{s.variants_tried} {t("variants")}</Chip><ConfirmButton t={t} onConfirm={async () => { await api.strategyDelete(s.id); loadStrategies(); }} /></span>
            </div>
          ))}
        </div>
      </div>
      <div className="min-w-0">{run ? <RunView run={run} onValidated={refreshLog} /> : <Empty>{t("lab_empty")}</Empty>}</div>
    </div>
  );
}

function ExperimentsTab({ version, onOpen }) {
  const { t, lang } = useApp();
  const [data, setData] = useState(null);
  const [family, setFamily] = useState("");
  const [kind, setKind] = useState("");
  const [error, setError] = useState(null);
  useEffect(() => { api.experiments({ family: family || undefined, kind: kind || undefined, limit: 100 }).then((d) => { setData(d); setError(null); }).catch(setError); }, [family, kind, version]);
  if (!data) return <ErrorBox error={error} t={t} />;
  return (
    <div className="space-y-3">
      <p className="help">{data.note}</p>
      <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
        {data.families.map((f) => (
          <button type="button" key={f.family} className="panel text-left" onClick={() => setFamily(family === f.family ? "" : f.family)} style={{ borderColor: family === f.family ? "var(--accent)" : undefined }}>
            <div className="flex items-center justify-between"><b>{f.family}</b><Chip className={WARN_CLASS[f.warning_level] || "chip"}>{f.warning_level}</Chip></div>
            <div className="help">{f.variants} {t("variants")} · {f.runs} {t("runs")} · {f.failed_or_rejected} {t("failed")}</div>
          </button>
        ))}
      </div>
      <div className="flex gap-2">
        <select className="field" style={{ width: 160 }} value={kind} onChange={(e) => setKind(e.target.value)} aria-label={t("kind")}><option value="">{t("all")}</option>{["backtest", "validation", "holdout", "wf_candidate"].map((k) => <option key={k} value={k}>{t(`exp_${k}`)}</option>)}</select>
        {family && <button type="button" className="btn btn-sm" onClick={() => setFamily("")}>{family} ×</button>}
      </div>
      <div className="panel overflow-x-auto">
        <table className="num">
          <thead><tr><th>{t("date")}</th><th>{t("kind")}</th><th>{t("family")}</th><th>{t("status")}</th><th className="r">{t("sharpe")}</th><th className="r">{t("total_return")}</th><th className="r">{t("max_drawdown")}</th><th className="r">{t("variants")}</th><th>id</th></tr></thead>
          <tbody>{data.experiments.map((e) => (
            <tr key={e.id}>
              <td>{clock(e.created_at, lang)}</td><td>{t(`exp_${e.kind}`)}</td><td>{e.family}</td>
              <td><Chip className={e.status === "ok" ? "chip chip-ok" : "chip chip-danger"}>{e.status}</Chip>{e.error ? <span className="help block">{e.error}</span> : null}</td>
              <td className="r">{num(e.metrics?.sharpe, 2, lang)}</td><td className={`r ${signClass(e.metrics?.total_return)}`}>{pct(e.metrics?.total_return, 1, lang, true)}</td><td className="r">{pct(e.metrics?.max_drawdown, 1, lang)}</td>
              <td className="r">{e.variants_in_family ?? "—"}</td>
              <td className="mono">{e.kind === "backtest" ? <button type="button" className="btn-link" onClick={() => onOpen(e.id)}>{e.id}</button> : e.id}</td>
            </tr>
          ))}</tbody>
        </table>
        {data.experiments.length === 0 && <p className="help">{t("experiments_empty")}</p>}
      </div>
    </div>
  );
}

export default function Lab() {
  const { t } = useApp();
  const [tab, setTab] = useState("backtest");
  const [snapshots, setSnapshots] = useState([]);
  const [version, setVersion] = useState(0);
  const [opened, setOpened] = useState(null);
  const [error, setError] = useState(null);
  useEffect(() => { api.snapshots({ limit: 100 }).then((s) => setSnapshots(s.snapshots)).catch(setError); }, []);
  const bump = useCallback(() => setVersion((v) => v + 1), []);
  const open = async (id) => {
    try { setOpened(await api.experiment(id)); setTab("opened"); setError(null); } catch (e) { setError(e); }
  };
  return (
    <div className="space-y-4">
      <h1>{t("nav_lab")}</h1>
      <Tabs tabs={[{ key: "backtest", label: t("backtest") }, { key: "experiments", label: t("experiments") }, ...(opened ? [{ key: "opened", label: opened.run_id }] : [])]} active={tab} onChange={setTab} />
      <ErrorBox error={error} t={t} />
      <div style={{ display: tab === "backtest" ? "block" : "none" }}><BacktestTab snapshots={snapshots} refreshLog={bump} /></div>
      {tab === "experiments" && <ExperimentsTab version={version} onOpen={open} />}
      {tab === "opened" && opened && <RunView run={opened} onValidated={bump} />}
    </div>
  );
}
