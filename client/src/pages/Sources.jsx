import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Chip, ErrorBox, Provenance } from "../components/ui.jsx";
import { shortHash } from "../format.js";

export default function Sources() {
  const { t } = useApp();
  const [providers, setProviders] = useState(null);
  const [snaps, setSnaps] = useState(null);
  const [query, setQuery] = useState("");
  const [error, setError] = useState(null);
  useEffect(() => { api.providers().then(setProviders).catch(setError); }, []);
  useEffect(() => { api.snapshots({ query: query || undefined, limit: 100 }).then(setSnaps).catch(setError); }, [query]);
  return (
    <div className="space-y-4">
      <h1>{t("nav_sources")}</h1>
      <ErrorBox error={error} t={t} />
      {providers && (
        <>
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
            {providers.providers.map((p) => (
              <div key={p.id} className="panel space-y-1">
                <div className="flex items-center justify-between gap-2"><h2>{p.name}</h2><span className="flex gap-1">{p.unofficial && <Chip className="chip chip-amber">{t("unofficial")}</Chip>}{p.status === "blocked" ? <Chip className="chip chip-danger">{t("blocked")}</Chip> : p.status === "needs_key" ? <Chip className="chip chip-amber">{t("needs_key")}</Chip> : <Chip className={p.available ? "chip chip-ok" : "chip chip-danger"}>{p.available ? t("available") : t("unavailable")}</Chip>}</span></div>
                <div className="help mono">{p.id}{p.needs_network ? "" : ` · ${t("local")}`}{p.intervals ? ` · ${p.intervals.join("/")}` : ""}</div>
                <p className="m-0">{p.terms}</p>
                <p className="help m-0">{t("delay")}: {p.delay}</p>
                <p className="help m-0">{t("license")}: {p.license}</p>
                {p.reason && <p className="help m-0">{p.reason}</p>}
              </div>
            ))}
          </div>
          <div className="panel space-y-1">
            <h3>{t("credits")}</h3>
            <p className="m-0">{providers.credits}</p>
            <p className="help m-0">{providers.disclaimer}</p>
            <h3 className="mt-2">{t("symbol_syntax")}</h3>
            <table><tbody>{Object.entries(providers.symbol_syntax).map(([k, v]) => <tr key={k}><th>{k}</th><td className="mono">{v}</td></tr>)}</tbody></table>
            <p className="help m-0">{t("cache")}: {providers.cache.entries} {t("entries")} · {providers.cache.hits} {t("hits")} / {providers.cache.misses} {t("misses")} · TTL {Math.round(providers.cache.ttl_s / 3600)} h</p>
          </div>
        </>
      )}
      <div className="panel overflow-x-auto">
        <div className="mb-2 flex items-center justify-between gap-2"><h2>{t("snapshots")} <span className="help">({snaps?.total ?? 0})</span></h2><input className="field" style={{ maxWidth: 240 }} placeholder={t("filter")} value={query} onChange={(e) => setQuery(e.target.value)} aria-label={t("filter")} /></div>
        <table>
          <thead><tr><th>id</th><th>{t("symbol")}</th><th>{t("provider")}</th><th>{t("prov_range")}</th><th>{t("prov_fetched")}</th><th>{t("prov_currency")}</th><th className="r">{t("rows")}</th><th>sha256</th></tr></thead>
          <tbody>{(snaps?.snapshots || []).map((s) => (
            <tr key={s.id}><td className="mono">{s.id}</td><td>{s.symbol}</td><td>{s.provider}</td><td className="mono">{s.actual_start} → {s.actual_end}</td><td className="mono">{s.fetched_at}</td><td>{s.currency || s.unit || "—"}{s.adjusted ? ` · ${t("adjusted")}` : ""}</td><td className="r">{s.rows}</td><td className="mono" title={s.sha256}>{shortHash(s.sha256)}</td></tr>
          ))}</tbody>
        </table>
        {snaps && snaps.snapshots.length === 0 && <p className="help">{t("snapshots_empty")}</p>}
      </div>
    </div>
  );
}
