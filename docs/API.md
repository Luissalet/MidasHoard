# API reference

Base URL `http://127.0.0.1:5192`. Every body is JSON. Errors are `{"error", "code", "hint"}` with the HTTP status of the code (`not_found` 404, `symbol_not_found` 404, `rate_limited` 429, `provider_unavailable` 502, everything else 400). Requests must carry a local `Host`; the guard refuses foreign hosts and cross-site requests.

## Health

- `GET /api/health` - cheap: `{service: "midas-hoard", version, counts, offline, hoard_link}`.
- `GET /api/status` - full status: providers, cache, disk, model resolution, counts by thesis status.

## Agent bridge

- `GET /api/agent/tools` - `{instructions, tools: [{name, description, annotations, inputSchema}]}`.
- `POST /api/agent/call` - header `Authorization: Bearer <data/mcp-token>`, body `{name, arguments, caller}`. Results are dicts capped at about 20 KB (the largest list is trimmed and a `truncated` block says what was cut; page with `limit` and `cursor`). Every call is recorded. `401` without the token.

## Web routes (same handlers, not capped)

| Route | Tool |
|---|---|
| `GET /api/market/providers` | `market_providers` |
| `GET /api/market/search?q=&provider=&remote=` | `market_search` |
| `POST /api/market/fetch` | `market_fetch` |
| `GET /api/snapshots` | `snapshots_list` |
| `POST /api/market/series` | `market_series` |
| `POST /api/market/compare` | `market_compare` |
| `GET/POST /api/theses`, `GET/PATCH/DELETE /api/theses/{id}` | `thesis_list`, `thesis_create`, `thesis_get`, `thesis_update` |
| `POST /api/theses/{id}/evidence`, `DELETE /api/theses/{id}/evidence/{evidence_id}` | `thesis_evidence_add` |
| `POST /api/theses/{id}/check` | `thesis_check` |
| `POST /api/theses/{id}/committee` | `committee_run` |
| `POST /api/rules/validate` | rule syntax check |
| `GET /api/lab/example`, `POST /api/lab/validate` | `strategy_validate` |
| `GET/POST /api/lab/strategies`, `GET/DELETE /api/lab/strategies/{id}` | `strategies_list`, `strategy_save` |
| `POST /api/lab/backtests`, `POST /api/lab/backtests/{run_id}/validate` | `backtest_run`, `backtest_validate` |
| `GET /api/lab/experiments`, `GET /api/lab/experiments/{run_id}` | `experiments_list` |
| `GET /api/portfolios`, `GET/PUT/DELETE /api/portfolios/{name}`, `POST /api/portfolios/{name}/analyze` | `portfolio_analyze`, `portfolio_set` |
| `POST /api/reports` | `report_export` |
| `GET/PUT /api/settings` | language, offline, clear_cache, backend, keys (write-only: `{provider: key}`, empty removes; responses carry `keys.<id> = {configured, last4, source}`, never the value) |

## Tools

Deletes are `action: "delete"` with `confirm: true` on the owning tool (`thesis_update`, `thesis_evidence_add`, `strategy_save`, `portfolio_set`). Tool descriptions carry English and Spanish keywords.

### `midas_status`

Health: providers, data counts, models, disk. The model resolution is cached for 60 s and probed off-thread; while the first probe runs it reports `{"state": "probing"}`. _read-only._

### `market_providers`

List data providers with terms, delay, licence and status (`ok`, `needs_key`, `blocked`). _read-only._

### `market_search`

Search symbols (indices, FX, stocks, macro series, crypto). _read-only._

| Argument | Type | Notes |
|---|---|---|
| `query` * | string |  |
| `provider` | string |  |
| `remote` | boolean | Also ask providers that offer a live search (CoinGecko) — needs network. |
| `limit` | integer |  |

### `market_fetch`

Fetch a series and freeze it as a provenance-tagged snapshot. _writes._

| Argument | Type | Notes |
|---|---|---|
| `provider` * | string | yahoo / fred / ecb / coingecko / alphavantage / tiingo / csv / fake (stooq is blocked by a browser check) |
| `symbol` * | string | yahoo AAPL / ^GSPC / ^IBEX / SAN.MC / EURUSD=X / BTC-EUR; alphavantage IBM; tiingo aapl; fred CPIAUCSL; ecb EXR/D.USD.EUR.SP00.A; coingecko bitcoin:eur; csv any name. |
| `start` | string | YYYY-MM-DD |
| `end` | string | YYYY-MM-DD |
| `interval` | string |  |
| `currency` | string | Override / declare the currency (required for csv unless unit is given). |
| `unit` | string |  |
| `label` | string |  |
| `path` | string | csv provider: a local .csv file. |
| `csv_text` | string | csv provider: the CSV content itself. |
| `mapping` | object | csv provider: {"date": "Fecha", "close": "Cierre", "open": ..., "high": ..., "low": ..., "volume": ...} |
| `dayfirst` | boolean |  |
| `delimiter` | string |  |
| `decimal` | string |  |
| `frequency` | string | csv provider: declare instead of inferring. |
| `adjusted` | boolean | csv provider: are the prices adjusted for splits/dividends? |

### `market_series`

View a snapshot: points, returns, volatility, drawdown, resample, rebase. _read-only._

| Argument | Type | Notes |
|---|---|---|
| `snapshot_id` * | string |  |
| `field` | string |  |
| `start` | string |  |
| `end` | string |  |
| `as_of` | string | Point-in-time cutoff: nothing after it is used. |
| `returns` | string |  |
| `vol_window` | integer | Rolling annualised volatility window (bars). |
| `drawdown` | boolean |  |
| `resample` | string | Downsample only; recorded in the result. |
| `rebase` | boolean | Rebase to 100 at the first point. |
| `convert_to` | string | Convert prices to this currency with fx. |
| `fx` | string | Snapshot id of the exchange rate to use for convert_to. |
| `limit` | integer |  |
| `cursor` | integer |  |

### `market_compare`

Compare 2-12 snapshots: correlation, relative performance, aligned dates. _read-only._

| Argument | Type | Notes |
|---|---|---|
| `snapshot_ids` * | array |  |
| `field` | string |  |
| `start` | string |  |
| `end` | string |  |
| `as_of` | string |  |
| `resample` | string |  |
| `convert_to` | string |  |
| `fx` | string/object | Snapshot id of a rate, or {asset snapshot id: rate snapshot id}. |
| `allow_incompatible` | boolean | Proceed despite mismatched currency/adjustment/frequency; the mismatch is recorded as a warning. |
| `limit` | integer |  |

### `snapshots_list`

List stored snapshots with provenance. _read-only._

| Argument | Type | Notes |
|---|---|---|
| `provider` | string |  |
| `symbol` | string |  |
| `query` | string |  |
| `limit` | integer |  |
| `cursor` | integer |  |

### `thesis_create`

Create an investment thesis with a rival hypothesis and rules. _writes._

| Argument | Type | Notes |
|---|---|---|
| `title` * | string |  |
| `claim` * | string | One or two falsifiable sentences. |
| `rival` * | string | Forced rival hypothesis: the best alternative explanation of the same facts. |
| `as_of` | string | Knowledge cutoff (default today). Evidence metrics never use data after it. |
| `horizon` | string | 6m, 12m, 2y or a date. |
| `assets` | array | ["AAPL"], snapshot ids ("snp_...") or [{"symbol","provider","snapshot_id","alias"}]; snapshot ids are stored with the snapshot symbol and provider |
| `assumptions` | array |  |
| `rules` | array | Invalidation rules, true = invalidated: "close(AAPL) < 150", "yoy(CPIAUCSL) > 4", "drawdown(^GSPC) < -20", "sma(x,50) < sma(x,200)". |
| `notes` | string |  |

### `thesis_get`

Get a thesis with evidence, rules and last check state. _read-only._

| Argument | Type | Notes |
|---|---|---|
| `id` * | string |  |
| `history` | boolean | Also list the previous checks. |

### `thesis_list`

List theses by status or text. _read-only._

| Argument | Type | Notes |
|---|---|---|
| `status` | string |  |
| `query` | string |  |
| `limit` | integer |  |
| `cursor` | integer |  |

### `thesis_update`

Edit a thesis (claim, rules, status...) or delete it (action=delete, confirm=true). _changes or deletes data._

| Argument | Type | Notes |
|---|---|---|
| `id` * | string |  |
| `action` | string |  |
| `confirm` | boolean | Required with action=delete. |
| `title` | string |  |
| `claim` | string |  |
| `rival` | string |  |
| `horizon` | string |  |
| `status` | string |  |
| `assets` | array |  |
| `assumptions` | array |  |
| `rules` | array | Replaces all rules (same syntax as thesis_create). |
| `notes` | string |  |

### `thesis_evidence_add`

Add evidence for/against a thesis (metric, url, note, hoard:// ref) or delete one. _changes or deletes data._

| Argument | Type | Notes |
|---|---|---|
| `thesis_id` * | string |  |
| `action` | string |  |
| `confirm` | boolean |  |
| `evidence_id` | string | For action=delete. |
| `side` | string |  |
| `kind` | string |  |
| `ref` | string | url, hoard://app/kind/id reference, or a snapshot id. |
| `title` | string |  |
| `quote` | string | Short quote or locator. |
| `snapshot_id` | string | snapshot_metric: the snapshot the metric comes from. |
| `expr` | string | snapshot_metric: rule-language number, e.g. yoy(CPIAUCSL); default the last close. |

### `thesis_check`

Check a thesis's invalidation rules on fresh data; may set it invalidated. _writes._

| Argument | Type | Notes |
|---|---|---|
| `id` * | string |  |
| `as_of` | string | Check date (default today); never before the thesis as_of. |
| `refresh` | boolean | Re-fetch the needed series first; falls back to the latest stored snapshot with a warning. |

### `strategy_validate`

Validate a strategy spec (JSON, no code) with fixable errors. _read-only._

| Argument | Type | Notes |
|---|---|---|
| `spec` * | object | Declarative strategy spec (JSON, no code). See the example in this description. |

### `strategy_save`

Save a validated strategy by name, or delete one (action=delete, confirm=true). _changes or deletes data._

| Argument | Type | Notes |
|---|---|---|
| `action` | string |  |
| `confirm` | boolean |  |
| `spec` | object |  |
| `id` | string | For action=delete: strategy id or name. |
| `note` | string |  |

### `strategies_list`

List saved strategies with the variants tried, or fetch one spec. _read-only._

| Argument | Type | Notes |
|---|---|---|
| `id` | string | One strategy (id or name) with its full spec. |

### `backtest_run`

Backtest a strategy: metrics, equity, trades, benchmark, variants tried. _writes._

| Argument | Type | Notes |
|---|---|---|
| `spec` | object |  |
| `strategy` | string | A saved strategy (id or name) instead of spec. |
| `as_of` | string | Data cutoff: nothing after it is used. |
| `holdout_fraction` | number | Seal this share of the latest bars as an untouched holdout. |
| `split_date` | string | Seal everything from this date instead of a fraction. |
| `label` | string |  |

### `backtest_validate`

Stress a backtest: walk-forward, permutation test, bootstrap Sharpe CI, multiple testing. _writes._

| Argument | Type | Notes |
|---|---|---|
| `run_id` * | string |  |
| `methods` | array |  |
| `windows` | integer |  |
| `grid` | object | Walk-forward parameter grid, e.g. {"indicators.fast.window": [10, 20, 50]}. Each candidate is logged as a variant. |
| `n` | integer | Monte Carlo / bootstrap resamples. |
| `block` | integer |  |
| `seed` | integer |  |
| `reveal_holdout` | boolean | Open the sealed holdout (counted in the log). Only when the spec is final. |

### `experiments_list`

List every backtest variant ever run, failures included, or one run in detail. _read-only._

| Argument | Type | Notes |
|---|---|---|
| `run_id` | string | One run in detail (metrics, equity, trades, validations). |
| `family` | string |  |
| `kind` | string |  |
| `status` | string |  |
| `limit` | integer |  |
| `cursor` | integer |  |

### `committee_run`

Run the committee on a thesis: advocates, risk reviewer, arbiter, number ledger. _writes._

| Argument | Type | Notes |
|---|---|---|
| `thesis_id` * | string |  |
| `use_model` | boolean | False returns the evidence pack and deterministic metrics without asking a model. |

### `portfolio_set`

Set a portfolio's holdings (list or CSV) or delete it (action=delete, confirm=true). _changes or deletes data._

| Argument | Type | Notes |
|---|---|---|
| `name` | string |  |
| `action` | string |  |
| `confirm` | boolean |  |
| `holdings` | array | [{"symbol": "AAPL", "quantity": 10, "currency": "USD", "cost_basis": 150, "snapshot_id": "snp_..."}] |
| `csv_text` | string | CSV with header: symbol,quantity[,snapshot_id,currency,cost_basis,label] |
| `path` | string |  |
| `currency` | string | Base currency of the portfolio (default EUR). |
| `replace` | boolean |  |

### `portfolio_analyze`

Value and analyse a portfolio: allocation, currency exposure, vol, drawdown, correlation. _read-only._

| Argument | Type | Notes |
|---|---|---|
| `name` | string | Omit to list the portfolios. |
| `date` | string | Valuation date (prices on or before it); default the latest common date. |
| `currency` | string |  |
| `fx` | string/object | A rate snapshot id (the pair is read from it and reported in `warnings`) or {"USD": "<snapshot id of the USD/base rate>"} |

### `report_export`

Export a thesis, run or portfolio as Markdown/JSON with provenance for every number. _writes._

| Argument | Type | Notes |
|---|---|---|
| `kind` * | string |  |
| `id` * | string |  |
| `format` | string |  |
| `save` | boolean | Also write the file under data/reports/. |
| `cursor` | integer | Character offset when the Markdown is longer than one page. |
| `page_chars` | integer |  |

## Error codes

`provider_unavailable` (502), `symbol_not_found` (404), `rate_limited` (429), `not_found` (404), `no_data`, `confirm_required`, `invalid_request`, `declaration_required`, `invalid_spec`, `bad_rule`, `incompatible_series`, `integrity_error`, `rival_required`.

## Rule language

`close open high low volume value`, `sma ema rsi roc ret change lag vol zscore rmax rmin bb_upper bb_lower bb_mid pctb drawdown yoy abs`, `cross_above cross_below`, arithmetic, comparisons, `and or not`. Percent-valued indicators return percent (`yoy(x) > 4` means 4 %). A true rule means the thesis is invalidated. States: `tripped`, `clear`, `no_data`, `error`.

## Events

`midas.snapshot.created`, `midas.thesis.created`, `midas.thesis.invalidated`, `midas.backtest.finished`, each with ids and short titles only.
