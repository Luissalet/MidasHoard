import React, { useEffect, useMemo, useRef, useState } from "react";
import { STATE_COLORS, STATUS_COLORS, shortHash } from "../format.js";

export function Icon({ d, size = 17 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

export function Empty({ children }) {
  return <div className="panel help text-center">{children}</div>;
}

export function Field({ label, hint, children }) {
  return (
    <label className="block">
      <span className="label">{label}</span>
      {children}
      {hint && <span className="help block mt-1">{hint}</span>}
    </label>
  );
}

export function Kpi({ label, value, tone }) {
  return (
    <div className="kpi">
      <b className={`num ${tone || ""}`}>{value}</b>
      <span>{label}</span>
    </div>
  );
}

export function Chip({ children, className = "chip", title }) {
  return <span className={className} title={title}>{children}</span>;
}

export function StatePill({ state, t }) {
  const cls = state === "tripped" || state === "error" ? "chip chip-danger" : state === "clear" ? "chip chip-ok" : state === "no_data" ? "chip chip-amber" : "chip";
  return (
    <span className={cls}>
      <span className="dot" style={{ background: STATE_COLORS[state] || STATE_COLORS.unchecked }} />
      {t(`state_${state}`)}
    </span>
  );
}

export function StatusPill({ status, t }) {
  return (
    <span className="chip">
      <span className="dot" style={{ background: STATUS_COLORS[status] || "var(--muted)" }} />
      {t(`status_${status}`)}
    </span>
  );
}

// Provenance chip: provider, symbol, fetch time and a hash prefix; the full record is in the title.
export function Provenance({ snap, t }) {
  if (!snap) return null;
  const title = [`${t("prov_id")}: ${snap.id}`, `${t("prov_fetched")}: ${snap.fetched_at}`, `${t("prov_range")}: ${snap.actual_start} → ${snap.actual_end}`,
    `${t("prov_currency")}: ${snap.currency || snap.unit || "—"}`, `${t("prov_adjusted")}: ${snap.adjusted ? t("yes") : t("no")}`, `sha256: ${snap.sha256}`].join("\n");
  return (
    <span className="chip chip-accent" title={title}>
      {snap.provider}:{snap.symbol} · {snap.actual_end} · {shortHash(snap.sha256)}
    </span>
  );
}

export function CopyButton({ value, t }) {
  const [copied, setCopied] = useState(false);
  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(value);
    } catch {
      // clipboard may be unavailable
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return <button type="button" className="btn btn-sm" onClick={onCopy}>{copied ? t("copied") : t("copy")}</button>;
}

export function ConfirmButton({ label, confirmLabel, onConfirm, t, className = "btn btn-sm btn-danger" }) {
  const [pending, setPending] = useState(false);
  if (pending) {
    return (
      <span className="inline-flex gap-1">
        <button type="button" className={className} onClick={() => { setPending(false); onConfirm(); }}>{confirmLabel || t("delete")}</button>
        <button type="button" className="btn btn-sm" onClick={() => setPending(false)}>{t("cancel")}</button>
      </span>
    );
  }
  return <button type="button" className={className} onClick={() => setPending(true)}>{label || t("delete")}</button>;
}

export function Tabs({ tabs, active, onChange }) {
  return (
    <div role="tablist" className="flex flex-wrap gap-1 border-b" style={{ borderColor: "var(--line)" }}>
      {tabs.map((tab) => (
        <button key={tab.key} type="button" role="tab" className="tab" aria-selected={tab.key === active} onClick={() => onChange(tab.key)}>{tab.label}</button>
      ))}
    </div>
  );
}

export function Issues({ issues, t }) {
  if (!issues || !issues.length) return null;
  return (
    <ul className="banner banner-danger m-0 list-none space-y-1 p-3" role="alert">
      {issues.map((i, n) => (
        <li key={n}>
          {i.path ? <span className="mono">{i.path}: </span> : null}
          {i.message}
          {i.hint ? <span className="help block">{t("hint")}: {i.hint}</span> : null}
        </li>
      ))}
    </ul>
  );
}

export function ErrorBox({ error, t }) {
  if (!error) return null;
  return (
    <div className="banner banner-danger" role="alert">
      {error.message || String(error)}
      {error.hint ? <span className="help block">{t("hint")}: {error.hint}</span> : null}
    </div>
  );
}

// ------------------------------------------------------------------ charts (hand-drawn SVG)
function useWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(640);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const update = () => setWidth(Math.max(240, Math.floor(el.clientWidth)));
    update();
    if (typeof ResizeObserver === "undefined") return undefined;
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

function niceTicks(min, max, count = 5) {
  if (!Number.isFinite(min) || !Number.isFinite(max)) return [0, 1];
  if (min === max) return [min - 1, min, min + 1];
  const span = max - min;
  const raw = span / count;
  const pow = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * pow).find((s) => s >= raw) || raw;
  const out = [];
  for (let v = Math.ceil(min / step) * step; v <= max + step * 1e-9; v += step) out.push(Number(v.toFixed(10)));
  return out;
}

const fmtTick = (v) => {
  const a = Math.abs(v);
  if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`;
  if (a >= 1e4) return `${(v / 1e3).toFixed(0)}k`;
  if (a >= 100) return v.toFixed(0);
  if (a >= 1) return v.toFixed(1);
  return v.toFixed(2);
};

/**
 * series: [{ key, name, color, dashed, points: [[ "YYYY-MM-DD", y ], ...] }]
 * fill: shade between the line and `base` (drawdown charts). yFormat formats tooltip values.
 * markers: [{ date, label, color }] vertical rules (as_of, holdout start...).
 */
export function LineChart({ series, height = 220, fill = false, base = 0, yFormat, markers = [], logScale = false, label }) {
  const [ref, width] = useWidth();
  const [hover, setHover] = useState(null);
  const pad = { l: 46, r: 10, t: 10, b: 22 };
  const data = useMemo(() => {
    const all = [];
    const lines = series
      .filter((s) => s.points && s.points.length)
      .map((s) => {
        const pts = s.points.filter((p) => p[1] !== null && p[1] !== undefined && Number.isFinite(p[1]) && (!logScale || p[1] > 0)).map((p) => [Date.parse(p[0]), p[1], p[0]]);
        pts.forEach((p) => all.push(p));
        return { ...s, pts };
      });
    if (!all.length) return null;
    let x0 = Infinity, x1 = -Infinity, y0 = Infinity, y1 = -Infinity;
    for (const p of all) {
      x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]);
      const y = logScale ? Math.log(p[1]) : p[1];
      y0 = Math.min(y0, y); y1 = Math.max(y1, y);
    }
    if (fill) { y0 = Math.min(y0, base); y1 = Math.max(y1, base); }
    const padY = (y1 - y0) * 0.06 || 1;
    return { lines, x0, x1, y0: y0 - padY, y1: y1 + padY };
  }, [series, fill, base, logScale]);
  if (!data) return <div className="help">—</div>;
  const w = width - pad.l - pad.r;
  const h = height - pad.t - pad.b;
  const sx = (x) => pad.l + ((x - data.x0) / (data.x1 - data.x0 || 1)) * w;
  const val = (y) => (logScale ? Math.log(y) : y);
  const sy = (y) => pad.t + (1 - (val(y) - data.y0) / (data.y1 - data.y0 || 1)) * h;
  const yTicks = logScale ? niceTicks(Math.exp(data.y0), Math.exp(data.y1), 4) : niceTicks(data.y0, data.y1, 4);
  const years = [];
  for (let y = new Date(data.x0).getUTCFullYear(); y <= new Date(data.x1).getUTCFullYear() + 1; y++) years.push(y);
  const span = years.length;
  const stepY = span > 14 ? 4 : span > 8 ? 2 : 1;
  const xTicks = years.filter((y, i) => i % stepY === 0).map((y) => [Date.UTC(y, 0, 1), String(y)]).filter(([x]) => x >= data.x0 && x <= data.x1);
  const path = (pts) => pts.map((p, i) => `${i ? "L" : "M"}${sx(p[0]).toFixed(1)},${sy(p[1]).toFixed(1)}`).join("");

  const onMove = (event) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const x = data.x0 + ((event.clientX - rect.left - pad.l) / w) * (data.x1 - data.x0);
    const values = data.lines.map((line) => {
      let lo = 0, hi = line.pts.length - 1;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (line.pts[mid][0] < x) lo = mid + 1; else hi = mid; }
      const c = line.pts[Math.max(0, lo)];
      const prev = line.pts[Math.max(0, lo - 1)];
      return { line, p: Math.abs(prev[0] - x) < Math.abs(c[0] - x) ? prev : c };
    });
    setHover({ x: values[0]?.p[0], values });
  };
  const fmt = yFormat || ((v) => fmtTick(v));

  return (
    <div ref={ref} className="relative w-full">
      <svg className="chart" width={width} height={height} role="img" aria-label={label || series.map((s) => s.name).join(", ")} onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
        {yTicks.map((v) => (
          <g key={v}>
            <line x1={pad.l} x2={width - pad.r} y1={sy(v)} y2={sy(v)} stroke="#ffffff12" />
            <text x={pad.l - 6} y={sy(v) + 3} textAnchor="end">{fmtTick(v)}</text>
          </g>
        ))}
        {xTicks.map(([x, name]) => (
          <text key={name} x={sx(x)} y={height - 6} textAnchor="middle">{name}</text>
        ))}
        {fill && <line x1={pad.l} x2={width - pad.r} y1={sy(base)} y2={sy(base)} stroke="#ffffff30" />}
        {markers.map((m, i) => {
          const x = Date.parse(m.date);
          if (!(x >= data.x0 && x <= data.x1)) return null;
          return (
            <g key={i}>
              <line x1={sx(x)} x2={sx(x)} y1={pad.t} y2={pad.t + h} stroke={m.color || "var(--warn)"} strokeDasharray="4 3" />
              <text x={sx(x) + 4} y={pad.t + 10}>{m.label}</text>
            </g>
          );
        })}
        {data.lines.map((line) => (
          <g key={line.key || line.name}>
            {fill && line.pts.length > 1 && (
              <path d={`${path(line.pts)}L${sx(line.pts[line.pts.length - 1][0]).toFixed(1)},${sy(base).toFixed(1)}L${sx(line.pts[0][0]).toFixed(1)},${sy(base).toFixed(1)}Z`} fill={line.color} opacity="0.18" />
            )}
            <path d={path(line.pts)} fill="none" stroke={line.color} strokeWidth="1.6" strokeDasharray={line.dashed ? "5 4" : undefined} strokeLinejoin="round" />
          </g>
        ))}
        {hover && (
          <g>
            <line x1={sx(hover.x)} x2={sx(hover.x)} y1={pad.t} y2={pad.t + h} stroke="#ffffff55" />
            {hover.values.map((v) => <circle key={v.line.key || v.line.name} cx={sx(v.p[0])} cy={sy(v.p[1])} r="3" fill={v.line.color} />)}
          </g>
        )}
      </svg>
      {hover && (
        <div className="panel absolute pointer-events-none text-[11.5px]" style={{ top: 6, left: Math.min(Math.max(sx(hover.x) + 10, 8), Math.max(8, width - 190)), padding: "4px 8px", minWidth: 120 }}>
          <div className="mono">{hover.values[0]?.p[2]}</div>
          {hover.values.map((v) => (
            <div key={v.line.key || v.line.name} className="flex items-center gap-2">
              <span className="dot" style={{ background: v.line.color }} />
              <span className="num">{fmt(v.p[1])}</span>
              {series.length > 1 && <span className="help">{v.line.name}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

export function Legend({ items }) {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 text-[11.5px]">
      {items.map((i) => (
        <span key={i.name} className="inline-flex items-center gap-1.5">
          <span className="inline-block h-[3px] w-4 rounded" style={{ background: i.color }} />
          {i.name}
        </span>
      ))}
    </div>
  );
}

// Horizontal share bars (allocation, currency exposure).
export function BarList({ items, format }) {
  const max = Math.max(...items.map((i) => Math.abs(i.value)), 1e-9);
  return (
    <div className="space-y-1.5">
      {items.map((i, n) => (
        <div key={i.label} className="grid grid-cols-[minmax(0,110px)_minmax(0,1fr)_64px] items-center gap-2 text-[12px]">
          <span className="truncate" title={i.label}>{i.label}</span>
          <span className="block h-2 rounded" style={{ background: "#ffffff0d" }}>
            <span className="block h-2 rounded" style={{ width: `${(Math.abs(i.value) / max) * 100}%`, background: i.color || "var(--accent)" }} />
          </span>
          <span className="num text-right">{format ? format(i.value) : i.value}</span>
        </div>
      ))}
    </div>
  );
}

// Where the observed statistic sits against the 5-95 % band of the permutation null.
export function NullBand({ low, high, mean, observed, width = 260, format = (v) => v.toFixed(2) }) {
  const pad = 8;
  const lo = Math.min(low, observed) - Math.abs(high - low) * 0.15;
  const hi = Math.max(high, observed) + Math.abs(high - low) * 0.15;
  const sx = (v) => pad + ((v - lo) / (hi - lo || 1)) * (width - 2 * pad);
  return (
    <svg className="chart" width={width} height={44} role="img" aria-label="null distribution band">
      <rect x={sx(low)} y={14} width={Math.max(1, sx(high) - sx(low))} height={12} rx="3" fill="#ffffff22" />
      <line x1={sx(mean)} x2={sx(mean)} y1={12} y2={28} stroke="#ffffff66" />
      <line x1={sx(observed)} x2={sx(observed)} y1={6} y2={32} stroke="var(--accent)" strokeWidth="2.5" />
      <text x={sx(observed)} y={43} textAnchor="middle" style={{ fill: "var(--accent)" }}>{format(observed)}</text>
      <text x={sx(low)} y={10} textAnchor="middle">{format(low)}</text>
      <text x={sx(high)} y={10} textAnchor="middle">{format(high)}</text>
    </svg>
  );
}

