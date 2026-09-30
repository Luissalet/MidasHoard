import React, { useEffect, useState } from "react";
import { api } from "../api.js";
import { useApp } from "../App.jsx";
import { Chip, ErrorBox } from "../components/ui.jsx";

const bytes = (n) => (n < 1024 ? `${n} B` : n < 1048576 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1048576).toFixed(1)} MB`);

export default function Settings() {
  const { t, notify, refresh, lang } = useApp();
  const [status, setStatus] = useState(null);
  const [settings, setSettings] = useState(null);
  const [backend, setBackend] = useState("{}");
  const [error, setError] = useState(null);
  const [jsonError, setJsonError] = useState(null);

  const load = async () => {
    try {
      const [s, cfg] = await Promise.all([api.status(), api.settings()]);
      setStatus(s);
      setSettings(cfg);
      setBackend(JSON.stringify(cfg.backend || {}, null, 2));
      setError(null);
    } catch (e) { setError(e); }
  };
  useEffect(() => { load(); }, []);

  const update = async (patch, message) => {
    try { setSettings(await api.settingsUpdate(patch)); notify(message || t("saved")); refresh(); setError(null); } catch (e) { setError(e); }
  };
  const [keyDraft, setKeyDraft] = useState({});
  const saveKey = async (id, value) => {
    await update({ keys: { [id]: value } }, value ? t("key_saved") : t("key_removed"));
    setKeyDraft((d) => ({ ...d, [id]: "" }));
  };
  const saveBackend = async (e) => {
    e.preventDefault();
    let parsed;
    try { parsed = JSON.parse(backend); setJsonError(null); } catch (err) { setJsonError(err.message); return; }
    await update({ backend: parsed });
    load();
  };

  if (!settings || !status) return <div className="space-y-3"><h1>{t("nav_settings")}</h1><ErrorBox error={error} t={t} /><p className="help">…</p></div>;
  const models = status.models || {};
  return (
    <div className="space-y-4">
      <h1>{t("nav_settings")}</h1>
      <ErrorBox error={error} t={t} />
      <div className="grid gap-4 lg:grid-cols-2">
        <div className="panel space-y-3">
          <h2>{t("data")}</h2>
          <label className="flex items-start gap-2"><input type="checkbox" checked={settings.offline} onChange={(e) => update({ offline: e.target.checked })} /><span>{t("offline_mode")}<span className="help block">{t("offline_hint")}</span></span></label>
          <div className="flex flex-wrap items-center gap-2"><button type="button" className="btn btn-sm" onClick={() => update({ clear_cache: true }, t("cache_cleared"))}>{t("clear_cache")}</button><span className="help">{settings.cache.entries} {t("entries")} · {bytes(settings.cache.bytes)}</span></div>
          <div className="help mono">{settings.data_dir}</div>
          <div className="help">{t("disk")}: {t("snapshots")} {bytes(status.disk.snapshots_bytes)} · {t("runs")} {bytes(status.disk.runs_bytes)}</div>
        </div>
        <div className="panel space-y-3">
          <h2>{t("api_keys")}</h2>
          <p className="help m-0">{t("api_keys_hint")}</p>
          {Object.entries(settings.keys || {}).map(([id, k]) => (
            <form key={id} className="space-y-1" onSubmit={(e) => { e.preventDefault(); if ((keyDraft[id] || "").trim()) saveKey(id, keyDraft[id]); }}>
              <div className="flex items-center justify-between gap-2"><span className="font-semibold">{id}</span><Chip className={k.configured ? "chip chip-ok" : "chip chip-amber"}>{k.configured ? `${t("key_configured")} …${k.last4}${k.source === "env" ? ` (${t("key_env")})` : ""}` : t("needs_key")}</Chip></div>
              <div className="flex gap-2">
                <input className="field" type="password" autoComplete="off" placeholder={t("key_paste")} value={keyDraft[id] || ""} onChange={(e) => setKeyDraft({ ...keyDraft, [id]: e.target.value })} aria-label={`${id} ${t("api_keys")}`} />
                <button className="btn btn-sm btn-primary" disabled={!(keyDraft[id] || "").trim()}>{t("save")}</button>
                {k.source === "settings" && <button type="button" className="btn btn-sm" onClick={() => saveKey(id, "")}>{t("key_remove")}</button>}
              </div>
            </form>
          ))}
        </div>
        <div className="panel space-y-2">
          <h2>{t("status")}</h2>
          <table><tbody>
            <tr><th>{t("version")}</th><td>{status.version} · schema {status.schema_version}</td></tr>
            {Object.entries(status.counts).map(([k, v]) => <tr key={k}><th>{t(`count_${k}`)}</th><td className="num">{v}</td></tr>)}
            <tr><th>{t("theses")}</th><td>{Object.entries(status.theses_by_status).map(([k, v]) => <Chip key={k}>{t(`status_${k}`)} {v}</Chip>)}</td></tr>
          </tbody></table>
        </div>
        <div className="panel space-y-2">
          <h2>{t("models")}</h2>
          {models.error ? <p className="help">{models.error}</p> : <pre className="mono m-0 max-h-[220px] overflow-auto whitespace-pre-wrap">{JSON.stringify(models, null, 2)}</pre>}
          <p className="help m-0">{t("models_hint")}</p>
        </div>
        <form onSubmit={saveBackend} className="panel space-y-2">
          <h2>{t("backend")}</h2>
          <textarea className="field" rows={8} spellCheck={false} value={backend} onChange={(e) => setBackend(e.target.value)} aria-label={t("backend")} />
          {jsonError && <div className="banner banner-danger">{t("invalid_json")}: {jsonError}</div>}
          <p className="help m-0">{t("backend_hint")}</p>
          <button className="btn btn-primary">{t("save")}</button>
        </form>
      </div>
    </div>
  );
}
