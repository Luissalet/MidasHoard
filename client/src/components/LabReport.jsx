import React from "react";
import { Chip } from "./ui.jsx";
import { num, pct } from "../format.js";

// The lab report contract: decision badge, claim kind tags, the data-health gate, the duplicate risk and the eight fixed sections.
const DECISION_CLASS = { reject: "decision-reject", investigate: "decision-investigate", review: "decision-review", adopt: "decision-adopt" };
const STATUS_CLASS = { pass: "chip chip-ok", warn: "chip chip-amber", fail: "chip chip-danger", not_run: "chip", unknown: "chip" };
const RISK_CLASS = { low: "chip chip-ok", medium: "chip chip-amber", high: "chip chip-danger" };

export function DecisionBadge({ decision, t, big }) {
  if (!decision) return null;
  return <span className={`decision ${DECISION_CLASS[decision] || ""} ${big ? "decision-big" : ""}`} title={t("decision_rule_hint")}>{t(`decision_${decision}`)}</span>;
}

export function KindTag({ kind, t }) {
  return <span className={`kind kind-${kind}`}>{t(`kind_${kind}`)}</span>;
}

export function StatusChip({ status, t }) {
  return <Chip className={STATUS_CLASS[status] || "chip"}>{t(`gate_${status}`)}</Chip>;
}

export function DuplicateChip({ level, similar, t }) {
  if (!level) return null;
  const title = (similar || []).map((s) => `${s.experiment} ${s.name} (${s.family}, ${s.level}): ${(s.why || []).join("; ")}`).join("\n");
  return <Chip className={RISK_CLASS[level] || "chip"} title={title || undefined}>{t("duplicate_risk")}: {t(`risk_${level}`)}</Chip>;
}

export function HealthGate({ health, t, lang }) {
  if (!health || !health.summary) return null;
  const sum = health.summary;
  return (
    <div className="panel space-y-2 overflow-x-auto">
      <h3 className="flex flex-wrap items-center gap-2">{t("data_health")} <StatusChip status={sum.status} t={t} />
        <span className="help">{t("min_coverage")} {pct(sum.min_coverage, 1, lang)}</span></h3>
      {(health.series || []).length > 0 && (
        <table className="num">
          <thead><tr><th>{t("symbol")}</th><th>{t("window")}</th><th className="r">{t("coverage")}</th><th className="r">{t("nulls")}</th><th className="r">{t("zeros")}</th><th className="r">{t("outliers_3s")}</th><th className="r">{t("long_gaps")}</th><th>{t("status")}</th></tr></thead>
          <tbody>{health.series.map((s) => (
            <tr key={s.snapshot || s.symbol}>
              <td>{s.symbol}</td><td className="mono">{s.window?.start} → {s.window?.end}</td>
              <td className="r">{pct(s.metrics?.coverage_ratio, 1, lang)}</td><td className="r">{pct(s.metrics?.null_ratio, 1, lang)}</td><td className="r">{pct(s.metrics?.zero_ratio, 1, lang)}</td>
              <td className="r">{s.metrics?.outlier_count ?? "—"}</td><td className="r">{s.metrics?.long_gaps ?? "—"}{s.metrics?.longest_gap_days ? ` (${s.metrics.longest_gap_days} d)` : ""}</td>
              <td><StatusChip status={s.status} t={t} /></td>
            </tr>
          ))}</tbody>
        </table>
      )}
      {(sum.issues || []).map((i, k) => <div key={k} className="banner banner-warn"><b>{i.series}</b>: {i.issue}</div>)}
      <p className="help m-0">{t("health_hint")}</p>
    </div>
  );
}

function Claims({ claims, t }) {
  return (
    <ul className="claims">
      {claims.map((c, i) => <li key={i}><KindTag kind={c.kind} t={t} />{c.text}{c.why ? <span className="help block">{c.why}{c.tool ? ` (${c.tool})` : ""}</span> : null}</li>)}
    </ul>
  );
}

function MetricTable({ table, t }) {
  const hasBench = table.rows.some((r) => r.benchmark_text !== "");
  return (
    <div className="space-y-1 overflow-x-auto">
      <p className="m-0"><KindTag kind="observed" t={t} /><b>{table.title}</b></p>
      <table className="num">
        <thead><tr><th>{t("metric")}</th><th className="r">{t("strategy")}</th>{hasBench && <th className="r">{t("benchmark")}</th>}<th>{t("note")}</th></tr></thead>
        <tbody>{table.rows.map((r) => (
          <tr key={r.metric}><td>{r.metric}</td>
            <td className="r">{r.strategy === null ? <span className="help">{t("no_data")}</span> : r.strategy_text}</td>
            {hasBench && <td className="r">{r.benchmark_text === "no data" ? <span className="help">{t("no_data")}</span> : r.benchmark_text}</td>}
            <td className="help">{r.note}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}

export function ReportContract({ report, t, lang }) {
  if (!report) return null;
  const quality = report.evidence_quality;
  return (
    <div className="panel space-y-4" data-testid="lab-report">
      <div className="flex flex-wrap items-center gap-2">
        <h2>{t("lab_report")}</h2>
        <DecisionBadge decision={report.decision.decision} t={t} />
        {quality && <Chip title={quality.reason}>{t("evidence_quality")}: {t(`grade_${quality.grade}`)}</Chip>}
        <span className="flex flex-wrap gap-1">{["observed", "inference", "alternative", "experiment"].map((k) => <KindTag key={k} kind={k} t={t} />)}</span>
      </div>
      {report.sections.map((sec) => (
        <section key={sec.id} className="space-y-2" aria-labelledby={`rep-${sec.id}`}>
          <h3 id={`rep-${sec.id}`}>{sec.number}. {lang === "en" ? sec.title : sec.title_es}</h3>
          {sec.id === "gates" && (
            <div className="overflow-x-auto">
              <table className="num">
                <thead><tr><th>{t("gate")}</th><th>{t("status")}</th><th>{t("detail")}</th></tr></thead>
                <tbody>{sec.gates.map((g) => <tr key={g.id}><td>{g.label}</td><td><StatusChip status={g.status} t={t} /></td><td><KindTag kind={g.kind} t={t} />{g.text}</td></tr>)}</tbody>
              </table>
            </div>
          )}
          {(sec.tables || []).map((tb) => <MetricTable key={tb.id} table={tb} t={t} />)}
          {sec.id === "decision" && (
            <div className="flex flex-wrap items-center gap-2">
              <DecisionBadge decision={sec.decision.decision} t={t} big />
              <span className="help">{sec.decision.meaning}</span>
            </div>
          )}
          {sec.id === "next" ? (
            <ol className="claims">{sec.claims.map((c) => <li key={c.priority}><KindTag kind="experiment" t={t} />{c.text}<span className="help block">{c.why} (<span className="mono">{c.tool}</span>)</span></li>)}</ol>
          ) : <Claims claims={sec.claims} t={t} />}
        </section>
      ))}
      <p className="help m-0">{report.disclaimer}</p>
    </div>
  );
}

export function DecisionBanner({ decision, health, duplicate, t, lang }) {
  if (!decision) return null;
  const cov = health?.summary?.min_coverage;
  return (
    <div className="panel space-y-1" data-testid="decision-banner">
      <div className="flex flex-wrap items-center gap-2">
        <span className="help">{t("decision")}</span>
        <DecisionBadge decision={decision.decision} t={t} big />
        {health?.summary && <span className="flex items-center gap-1">{t("data_health")} <StatusChip status={health.summary.status} t={t} /><span className="help">{pct(cov, 1, lang)}</span></span>}
        <DuplicateChip level={duplicate?.level} similar={duplicate?.similar} t={t} />
      </div>
      {(decision.reasons || []).slice(0, 3).map((r, i) => <div key={i} className="help">• {r}</div>)}
      {(decision.needs || []).slice(0, 2).map((r, i) => <div key={i} className="help"><KindTag kind="experiment" t={t} />{r}</div>)}
    </div>
  );
}
