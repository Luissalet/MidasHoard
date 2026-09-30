// Thin fetch wrapper: JSON in/out. `{ error, code, hint }` bodies become exceptions that keep code and hint.
async function request(method, path, { params, body } = {}) {
  const url = new URL(path, window.location.origin);
  for (const [key, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(key, value);
  }
  const response = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { error: text };
  }
  if (!response.ok) {
    const error = new Error((data && data.error) || `Error ${response.status}`);
    error.code = data && data.code;
    error.hint = data && data.hint;
    error.issues = data && data.issues;
    throw error;
  }
  return data;
}

const enc = encodeURIComponent;

export const api = {
  health: () => request("GET", "/api/health"),
  status: () => request("GET", "/api/status"),

  providers: () => request("GET", "/api/market/providers"),
  search: (params) => request("GET", "/api/market/search", { params }),
  fetchSeries: (body) => request("POST", "/api/market/fetch", { body }),
  snapshots: (params) => request("GET", "/api/snapshots", { params }),
  series: (body) => request("POST", "/api/market/series", { body }),
  compare: (body) => request("POST", "/api/market/compare", { body }),

  theses: (params) => request("GET", "/api/theses", { params }),
  thesisCreate: (body) => request("POST", "/api/theses", { body }),
  thesis: (id) => request("GET", `/api/theses/${enc(id)}`),
  thesisUpdate: (id, body) => request("PATCH", `/api/theses/${enc(id)}`, { body }),
  thesisDelete: (id) => request("DELETE", `/api/theses/${enc(id)}`),
  evidenceAdd: (id, body) => request("POST", `/api/theses/${enc(id)}/evidence`, { body }),
  evidenceDelete: (id, ev) => request("DELETE", `/api/theses/${enc(id)}/evidence/${enc(ev)}`),
  thesisCheck: (id, body) => request("POST", `/api/theses/${enc(id)}/check`, { body: body || {} }),
  committee: (id, body) => request("POST", `/api/theses/${enc(id)}/committee`, { body: body || {} }),
  ruleValidate: (rule) => request("POST", "/api/rules/validate", { body: { rule } }),

  labExample: () => request("GET", "/api/lab/example"),
  labValidate: (spec) => request("POST", "/api/lab/validate", { body: { spec } }),
  strategies: () => request("GET", "/api/lab/strategies"),
  strategy: (id) => request("GET", `/api/lab/strategies/${enc(id)}`),
  strategySave: (spec, note) => request("POST", "/api/lab/strategies", { body: { spec, note } }),
  strategyDelete: (id) => request("DELETE", `/api/lab/strategies/${enc(id)}`),
  backtest: (body) => request("POST", "/api/lab/backtests", { body }),
  backtestValidate: (id, body) => request("POST", `/api/lab/backtests/${enc(id)}/validate`, { body }),
  experiments: (params) => request("GET", "/api/lab/experiments", { params }),
  experiment: (id) => request("GET", `/api/lab/experiments/${enc(id)}`),

  portfolios: () => request("GET", "/api/portfolios"),
  portfolio: (name) => request("GET", `/api/portfolios/${enc(name)}`),
  portfolioSet: (name, body) => request("PUT", `/api/portfolios/${enc(name)}`, { body }),
  portfolioDelete: (name) => request("DELETE", `/api/portfolios/${enc(name)}`),
  portfolioAnalyze: (name, body) => request("POST", `/api/portfolios/${enc(name)}/analyze`, { body: body || {} }),

  report: (body) => request("POST", "/api/reports", { body }),

  settings: () => request("GET", "/api/settings"),
  settingsUpdate: (body) => request("PUT", "/api/settings", { body }),
};
