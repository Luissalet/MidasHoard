// Number and date formatting shared by the pages. Everything is locale-aware (es-ES / en-GB).
const locale = (lang) => (lang === "en" ? "en-GB" : "es-ES");

export function num(value, digits = 2, lang = "es") {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return Number(value).toLocaleString(locale(lang), { minimumFractionDigits: digits, maximumFractionDigits: digits });
}

// A fraction (0.123) as a percentage string; `signed` adds a + sign for positives.
export function pct(value, digits = 1, lang = "es", signed = false) {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  const text = num(value * 100, digits, lang) + " %";
  return signed && value > 0 ? `+${text}` : text;
}

export function money(value, currency, lang = "es") {
  if (value === null || value === undefined) return "—";
  try {
    return new Intl.NumberFormat(locale(lang), { style: "currency", currency: currency || "EUR", maximumFractionDigits: 2 }).format(value);
  } catch {
    return `${num(value, 2, lang)} ${currency || ""}`;
  }
}

export function clock(ts, lang) {
  if (!ts) return "—";
  const date = new Date(ts * 1000);
  return date.toLocaleString(locale(lang), { day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function shortHash(hash) {
  return hash ? `${hash.slice(0, 10)}…` : "—";
}

export const signClass = (value) => (value > 0 ? "pos" : value < 0 ? "neg" : "");

export const STATE_COLORS = { tripped: "var(--danger)", clear: "var(--ok)", no_data: "var(--warn)", error: "var(--danger)", unchecked: "var(--muted)" };
export const STATUS_COLORS = { open: "var(--accent)", confirmed: "var(--ok)", invalidated: "var(--danger)", expired: "var(--warn)", archived: "var(--muted)" };
export const SERIES_COLORS = ["#f2c230", "#5fb3d9", "#e5604b", "#4fb286", "#b48ce0", "#e0915a", "#8fc1a0", "#d9d9d9"];

// "a, b ,c" -> ["a","b","c"]
export const splitList = (text) => (text || "").split(/[\n,;]+/).map((s) => s.trim()).filter(Boolean);
