# Midas's Hoard

A local market-research lab for one person. It freezes market data into snapshots with provenance, keeps falsifiable investment theses with rules that invalidate them, runs an honest strategy backtester with a sealed holdout and overfitting checks, and audits every model-written number against the evidence. Assistants reach it over MCP; you reach it in the browser.

**Research, not trading.** There is no broker, no order, no buy or sell advice. Results are historical analyses, not advice; past performance does not guarantee future results.

## What it does

- **Market** - search symbols, fetch a series and freeze it as a snapshot (canonical CSV, sha256, provider, fetch time, currency, adjustment flag). Compare series with correlation and relative performance; mixed currency, adjustment or frequency is refused unless you allow it, and an exchange-rate conversion is recorded step by step. Charts are hand-drawn SVG.
- **Theses** - a claim, a forced rival hypothesis, a knowledge cutoff (`as_of`), a horizon, evidence for and against, and invalidation rules written in a small language with no `eval` (`close(AAPL) < 150`, `yoy(CPIAUCSL) > 4`, `drawdown(^GSPC) < -20`, `sma(x,50) < sma(x,200)`). A true rule means the thesis is invalidated. Evidence metrics never use data after the cutoff. Checks are idempotent and an invalidation emits one event.
- **Lab** - declarative strategy specs (JSON, no code). A signal on bar t only affects the position earning bar t+1 (a test proves it). Costs, sizing, rebalancing, long/short, benchmark, sealed holdout, anchored walk-forward with an optional parameter grid, block-permutation test, block-bootstrap Sharpe interval, Bonferroni haircut and deflated Sharpe. Every variant is logged, failures included, with a multiple-testing warning.
- **Committee** - two advocates, an independent risk reviewer and an arbiter over a fixed evidence pack. Citations are validated and every number goes through the number ledger (observed, derived, cited, proposed or unmatched). Without a model it returns the evidence pack and deterministic metrics.
- **Portfolio** - holdings from a table or CSV, valuation at a date, allocation, currency exposure, concentration, volatility, drawdown, correlation, with every FX conversion recorded.
- **Reports** - Markdown or JSON for a thesis, a run or a portfolio, with provenance for every number.

## Data providers

| Provider | Data | Notes |
|---|---|---|
| Yahoo Finance (default) | daily prices for equities, ETFs, indices, FX and crypto: `AAPL`, `^GSPC`, `^IBEX`, `SAN.MC`, `EURUSD=X`, `BTC-EUR` | no key; unofficial public chart endpoint, personal and research use only; closes adjusted for splits and dividends, events recorded; may change or throttle without notice |
| FRED | macro series | Source: Federal Reserve Bank of St. Louis; values are as last revised, not vintages |
| European Central Bank | euro reference rates and statistics (SDMX) | Source: ECB Data Portal |
| CoinGecko | crypto prices, up to 365 days on the free tier | data provided by CoinGecko; throttled |
| Alpha Vantage | daily prices | optional, off until you add a free key (`MIDAS_ALPHAVANTAGE_KEY` or Settings); raw bars, small daily allowance |
| Tiingo | daily adjusted prices, mostly US | optional, off until you add a free key (`MIDAS_TIINGO_KEY` or Settings) |
| Stooq | end-of-day prices | currently blocked: it answers scripts with a JavaScript browser check, which Midas does not try to get around; fetches fail with `provider_unavailable` and the source is marked blocked |
| CSV | your own files | you declare currency or unit; nothing is guessed |
| fake | deterministic synthetic series | for tests, demos and offline use |

API keys entered in Settings are write-only: they are stored locally and the API only reports `configured` and the last four characters. Providers show as `ok`, `needs_key` or `blocked` in the Sources page and in `market_providers`.

Responses are cached on disk by the shared Hoard Link JSON API client (identifying User-Agent, one retry on network errors and server errors, a stale copy when the provider is down, `Retry-After` on rate limits, public addresses only); `MIDAS_OFFLINE=1` (or the Settings switch) answers only from the cache. Read each provider's terms, shown with every snapshot.

## Run it

```
pip install -r requirements.txt
python -m midas_hoard            # http://127.0.0.1:5192
```

The client is built and committed under `midas_hoard/static`; rebuild it with `npm install && npm run build`. `python scripts/launch.py` picks a free port and opens the browser; `python scripts/dev.py` runs the API with reload plus the Vite dev server.

Settings come from the environment: `MIDAS_DATA_DIR` (default `./data`), `MIDAS_PORT` (5192), `PORT_STRICT`, `MIDAS_ALLOWED_HOSTS`, `MIDAS_HTTP_TIMEOUT_S`, `MIDAS_CACHE_TTL_S`, `MIDAS_OFFLINE`. Data lives in `data/midas.db` (SQLite, WAL) plus `data/snapshots`, `data/runs` and `data/reports`; files are written atomically. The launcher, request guard, error envelope, stable `mcp-token`, SQLite layer, agent catalogue (20 KB result cap) and MCP bridge are the shared commons of Hoard Link (`midas_hoard/hoard_link/`); a second `python -m midas_hoard` on the same port exits instead of starting another copy.

## Assistant tools (MCP)

`mcp_server.py` is the shared Hoard Link stdio bridge. It never opens the database: it proxies every call to `POST /api/agent/call` with the token in `data/mcp-token`, reads the tool list from `GET /api/agent/tools`, and starts the app if nothing answers (`MIDAS_BRIDGE_AUTOSTART=0` disables that). Variables: `MIDAS_URL`, `MIDAS_TOKEN_FILE`, `MIDAS_DATA_DIR`. Errors keep their `code` and `hint`. Deletes need `confirm: true` on the owning tool. The 23 tools:

### Market data

| Tool | What it does |
|---|---|
| `midas_status` | Health: providers, data counts, models, disk |
| `market_providers` | List data providers with terms, delay and licence |
| `market_search` | Search symbols (indices, FX, stocks, macro series, crypto) |
| `market_fetch` | Fetch a series and freeze it as a provenance-tagged snapshot |
| `market_series` | View a snapshot: points, returns, volatility, drawdown, resample, rebase |
| `market_compare` | Compare 2-12 snapshots: correlation, relative performance, aligned dates |
| `snapshots_list` | List stored snapshots with provenance |

### Theses

| Tool | What it does |
|---|---|
| `thesis_create` | Create an investment thesis with a rival hypothesis and rules |
| `thesis_get` | Get a thesis with evidence, rules and last check state |
| `thesis_list` | List theses by status or text |
| `thesis_update` | Edit a thesis (claim, rules, status...) or delete it (action=delete, confirm=true) |
| `thesis_evidence_add` | Add evidence for/against a thesis (metric, url, note, hoard:// ref) or delete one |
| `thesis_check` | Check a thesis's invalidation rules on fresh data; may set it invalidated |
| `committee_run` | Run the committee on a thesis: advocates, risk reviewer, arbiter, number ledger |

### Strategy lab

| Tool | What it does |
|---|---|
| `strategy_validate` | Validate a strategy spec (JSON, no code) with fixable errors |
| `strategy_save` | Save a validated strategy by name, or delete one (action=delete, confirm=true) |
| `strategies_list` | List saved strategies with the variants tried, or fetch one spec |
| `backtest_run` | Backtest a strategy: metrics, equity, trades, benchmark, variants tried |
| `backtest_validate` | Stress a backtest: walk-forward, permutation test, bootstrap Sharpe CI, multiple testing |
| `experiments_list` | List every backtest variant ever run, failures included, or one run in detail |

### Portfolio and reports

| Tool | What it does |
|---|---|
| `portfolio_set` | Set a portfolio's holdings (list or CSV) or delete it (action=delete, confirm=true) |
| `portfolio_analyze` | Value and analyse a portfolio: allocation, currency exposure, vol, drawdown, correlation |
| `report_export` | Export a thesis, run or portfolio as Markdown/JSON with provenance for every number |

Full reference: [docs/API.md](docs/API.md).

## Method and limits

- Snapshots are immutable and verified by hash on load; identical bytes reuse the snapshot.
- `as_of` makes analyses point-in-time for the data you hold. FRED and some other providers publish revised values, so revisions can still leak into past dates; this is stated in each snapshot.
- Variant counts are per strategy family (distinct spec hashes). The deflated Sharpe needs at least 3 variants.
- Backtests are daily-bar simulations with proportional costs: no intraday fills, no liquidity model, no taxes, no survivorship correction for delisted names.
- The committee can only be as good as the evidence you give it; unmatched numbers are flagged, not removed.
- CoinGecko history is limited to 365 days on the free tier.

## Tests

`python -m pytest -q` (offline: a fake provider, mocked HTTP transports and a scripted model). CI runs on Ubuntu and Windows.

## Licence

MIT (c) Luis María Salete Cuartero. Data remains subject to each provider's terms.
