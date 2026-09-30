import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { api } from "./api.js";
import { initialLang, makeT, saveLang } from "./i18n.js";
import { Icon } from "./components/ui.jsx";
import Market from "./pages/Market.jsx";
import Theses from "./pages/Theses.jsx";
import Lab from "./pages/Lab.jsx";
import Portfolio from "./pages/Portfolio.jsx";
import Sources from "./pages/Sources.jsx";
import Settings from "./pages/Settings.jsx";

const PAGES = [
  { path: "market", key: "nav_market", icon: "M3 17l6-6 4 4 8-8M15 7h6v6", component: Market },
  { path: "theses", key: "nav_theses", icon: "M9 3h6l4 4v14H5V3zM14 3v5h5M8 13h8M8 17h6", component: Theses },
  { path: "lab", key: "nav_lab", icon: "M9 3v6L4 19a2 2 0 002 3h12a2 2 0 002-3l-5-10V3M8 3h8", component: Lab },
  { path: "portfolio", key: "nav_portfolio", icon: "M3 7h18v13H3zM8 7V4h8v3M3 13h18", component: Portfolio },
  { path: "sources", key: "nav_sources", icon: "M4 6c0-1.7 3.6-3 8-3s8 1.3 8 3-3.6 3-8 3-8-1.3-8-3zM4 6v6c0 1.7 3.6 3 8 3s8-1.3 8-3V6M4 12v6c0 1.7 3.6 3 8 3s8-1.3 8-3v-6", component: Sources },
  { path: "settings", key: "nav_settings", icon: "M12 15a3 3 0 100-6 3 3 0 000 6zM19 12l2-1-1-3-2 .3-1.4-1.4.3-2-3-1-1 2h-2l-1-2-3 1 .3 2L6.8 7.3 5 7 4 10l2 1v2l-2 1 1 3 2-.3 1.4 1.4-.3 2 3 1 1-2h2l1 2 3-1-.3-2 1.4-1.4 2 .3 1-3-2-1z", component: Settings },
];

const AppContext = createContext(null);
export const useApp = () => useContext(AppContext);

function useHashRoute() {
  const read = () => {
    const [path] = window.location.hash.replace(/^#\/?/, "").split("?");
    const parts = path.split("/");
    return { page: parts[0] || "market", param: parts[1] || null };
  };
  const [route, setRoute] = useState(read);
  useEffect(() => {
    const onChange = () => setRoute(read());
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}

export function Toast({ message, onClose }) {
  useEffect(() => {
    if (!message) return undefined;
    const timer = setTimeout(onClose, 4500);
    return () => clearTimeout(timer);
  }, [message, onClose]);
  if (!message) return null;
  return <div className="toast" role="status" onClick={onClose}>{message}</div>;
}

export default function App() {
  const route = useHashRoute();
  const [lang, setLang] = useState(initialLang);
  const t = useMemo(() => makeT(lang), [lang]);
  const [health, setHealth] = useState(null);
  const [error, setError] = useState(null);
  const [toast, setToast] = useState(null);

  useEffect(() => {
    document.documentElement.lang = lang;
    document.title = "Midas's Hoard";
  }, [lang]);

  const refresh = useCallback(async () => {
    try {
      setHealth(await api.health());
      setError(null);
    } catch (e) {
      setError(e.message);
    }
  }, []);
  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, 20000);
    return () => clearInterval(timer);
  }, [refresh]);

  const notify = useCallback((message) => setToast(message), []);
  const value = useMemo(() => ({ health, refresh, notify, t, lang, setLang }), [health, refresh, notify, t, lang]);
  const page = PAGES.find((p) => p.path === route.page) || PAGES[0];
  const Component = page.component;

  const switchLang = () => {
    const next = lang === "es" ? "en" : "es";
    saveLang(next);
    setLang(next);
    api.settingsUpdate({ language: next }).catch(() => {});
  };

  return (
    <AppContext.Provider value={value}>
      <div className="min-h-dvh md:grid md:grid-cols-[200px_minmax(0,1fr)]">
        <aside className="sticky top-0 z-10 border-b md:h-dvh md:self-start md:border-b-0 md:border-r" style={{ background: "var(--sidebar)", borderColor: "var(--line)" }}>
          <div className="flex items-center gap-3 px-4 py-3 md:px-4 md:py-4">
            <img src="/icon-192.png" alt="" width="30" height="30" className="rounded-lg" />
            <div className="text-[14px] font-semibold leading-tight">Midas's Hoard</div>
          </div>
          <nav aria-label="Sections" className="flex gap-1 overflow-x-auto px-3 pb-2 md:flex-col">
            {PAGES.map((p) => (
              <a key={p.path} href={`#/${p.path}`} className="nav-link shrink-0 text-[13px]" aria-current={p.path === page.path ? "page" : undefined}>
                <Icon d={p.icon} />
                {t(p.key)}
              </a>
            ))}
          </nav>
          <div className="hidden px-4 pt-4 md:block">
            {health && (
              <div className="help space-y-0.5 text-[11px]">
                <div>{health.counts?.snapshots ?? 0} {t("count_snapshots")}</div>
                <div>{health.counts?.theses ?? 0} {t("count_theses")}</div>
                <div>{health.counts?.experiments ?? 0} {t("count_experiments")}</div>
                {health.offline && <div className="chip chip-amber mt-1">{t("offline_on")}</div>}
              </div>
            )}
            <button type="button" className="btn btn-sm mt-4" onClick={switchLang}>{t("language")}</button>
          </div>
        </aside>
        <main className="min-w-0 px-4 py-4 md:px-7 md:py-6">
          {error && (
            <div className="banner banner-danger mb-4" role="alert">
              {t("unreachable")}: {error}. <button type="button" className="btn-link" onClick={refresh}>{t("retry")}</button>
            </div>
          )}
          <Component param={route.param} />
          <p className="help mt-8 border-t pt-3" style={{ borderColor: "var(--line)" }}>{t("disclaimer")}</p>
          <div className="mt-4 md:hidden">
            <button type="button" className="btn btn-sm" onClick={switchLang}>{t("language")}</button>
          </div>
        </main>
      </div>
      <Toast message={toast} onClose={() => setToast(null)} />
    </AppContext.Provider>
  );
}
