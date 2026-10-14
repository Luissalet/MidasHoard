"""Tools exposed to the assistant. One catalogue drives /api/agent/* and mcp_server.py."""

from __future__ import annotations

import json
from typing import Any, Literal, Optional, Union

from pydantic import BaseModel, Field

from .errors import MidasError
from .hoard_link import agentkit
from .hoard_link.agentkit import MAX_RESULT_BYTES, Empty, Tool, ann as _ann, cap_result, uncapped  # noqa: F401  (uncapped: the web UI's way out of the cap)
from .services import Services
from .strategy import EXAMPLE_SPEC

AGENT_INSTRUCTIONS = """Midas's Hoard is a local financial research lab: frozen market data, theses with evidence and invalidation rules, and an honest strategy backtester. Research, not advice: never say buy or sell; give analyses with their uncertainty.
Flow: market_fetch (a snapshot with provenance) -> thesis_create (needs a rival hypothesis; rules like close(AAPL) < 150 or yoy(CPIAUCSL) > 4) -> thesis_evidence_add -> thesis_check. Labs: strategy_validate -> backtest_run (holdout_fraction seals a holdout) -> backtest_validate -> report_export(kind=run): eight fixed sections, every claim tagged Observed/Inference/Alternative/Experiment, and a decision (reject/investigate/review/adopt) computed by rules, never by a model. data_health checks the series before metrics; experiments_duplicates finds the same idea under another name. Every variant is logged; quote the effective variants count with any metric. A missing value is "no data", never 0.
Quote numbers only from tool results. Never merge series of different currency or frequency: convert or resample explicitly. Write tools (fetch, create, update, check, save, run, validate, set) only when the user asks; deletes need confirm=true."""


def _confirm(action: str, confirm: bool, what: str) -> None:
    if action == "delete" and not confirm:
        raise MidasError("confirm_required", f"Deleting {what} is permanent.", "Repeat the call with confirm=true if the user asked for it.")


# ------------------------------------------------------------------ market
class MarketSearchArgs(BaseModel):
    query: str = Field(..., min_length=1, max_length=120)
    provider: Optional[str] = Field(None, max_length=20)
    remote: bool = Field(False, description="Also ask providers that offer a live search (CoinGecko) — needs network.")
    limit: int = Field(20, ge=1, le=50)


class MarketFetchArgs(BaseModel):
    provider: str = Field(..., max_length=20, description="yahoo | fred | ecb | coingecko | alphavantage | tiingo | csv | fake (stooq is blocked by a browser check)")
    symbol: str = Field(..., min_length=1, max_length=120, description="yahoo AAPL / ^GSPC / ^IBEX / SAN.MC / EURUSD=X / BTC-EUR; alphavantage IBM; tiingo aapl; fred CPIAUCSL; ecb EXR/D.USD.EUR.SP00.A; coingecko bitcoin:eur; csv any name.")
    start: Optional[str] = Field(None, max_length=10, description="YYYY-MM-DD")
    end: Optional[str] = Field(None, max_length=10, description="YYYY-MM-DD")
    interval: str = Field("d", pattern="^(d|w|m|q|y)$")
    currency: Optional[str] = Field(None, max_length=3, description="Override / declare the currency (required for csv unless unit is given).")
    unit: Optional[str] = Field(None, max_length=60)
    label: str = Field("", max_length=80)
    path: Optional[str] = Field(None, max_length=1000, description="csv provider: a local .csv file.")
    csv_text: Optional[str] = Field(None, max_length=5_000_000, description="csv provider: the CSV content itself.")
    mapping: Optional[dict[str, str]] = Field(None, description='csv provider: {"date": "Fecha", "close": "Cierre", "open": ..., "high": ..., "low": ..., "volume": ...}')
    dayfirst: bool = False
    delimiter: Optional[str] = Field(None, max_length=2)
    decimal: Optional[str] = Field(None, max_length=1)
    frequency: Optional[str] = Field(None, pattern="^(D|W|M|Q|A)$", description="csv provider: declare instead of inferring.")
    adjusted: bool = Field(False, description="csv provider: are the prices adjusted for splits/dividends?")


class SeriesArgs(BaseModel):
    snapshot_id: str = Field(..., max_length=40)
    field: str = Field("close", max_length=10)
    start: Optional[str] = Field(None, max_length=10)
    end: Optional[str] = Field(None, max_length=10)
    as_of: Optional[str] = Field(None, max_length=10, description="Point-in-time cutoff: nothing after it is used.")
    returns: Optional[Literal["simple", "log"]] = None
    vol_window: Optional[int] = Field(None, ge=2, le=1000, description="Rolling annualised volatility window (bars).")
    drawdown: bool = False
    resample: Optional[Literal["W", "M", "Q", "A"]] = Field(None, description="Downsample only; recorded in the result.")
    rebase: bool = Field(False, description="Rebase to 100 at the first point.")
    convert_to: Optional[str] = Field(None, max_length=3, description="Convert prices to this currency with fx.")
    fx: Optional[str] = Field(None, max_length=40, description="Snapshot id of the exchange rate to use for convert_to.")
    limit: int = Field(120, ge=1, le=1000)
    cursor: Optional[int] = Field(None, ge=0)


class CompareArgs(BaseModel):
    snapshot_ids: list[str] = Field(..., min_length=2, max_length=12)
    field: str = Field("close", max_length=10)
    start: Optional[str] = Field(None, max_length=10)
    end: Optional[str] = Field(None, max_length=10)
    as_of: Optional[str] = Field(None, max_length=10)
    resample: Optional[Literal["W", "M", "Q", "A"]] = None
    convert_to: Optional[str] = Field(None, max_length=3)
    fx: Optional[Union[str, dict[str, str]]] = Field(None, description="Snapshot id of a rate, or {asset snapshot id: rate snapshot id}.")
    allow_incompatible: bool = Field(False, description="Proceed despite mismatched currency/adjustment/frequency; the mismatch is recorded as a warning.")
    limit: int = Field(60, ge=5, le=300)


class SnapshotsListArgs(BaseModel):
    provider: Optional[str] = Field(None, max_length=20)
    symbol: Optional[str] = Field(None, max_length=120)
    query: Optional[str] = Field(None, max_length=120)
    limit: int = Field(30, ge=1, le=100)
    cursor: Optional[int] = Field(None, ge=0)


class WatchlistEntry(BaseModel):
    snapshot_id: Optional[str] = None
    provider: Optional[str] = None
    symbol: Optional[str] = None


class WatchlistSaveArgs(BaseModel):
    id: Optional[str] = None
    name: Optional[str] = Field(None, min_length=1, max_length=200)
    notes: Optional[str] = Field(None, max_length=4000)
    entries: Optional[list[WatchlistEntry]] = None
    add_entries: Optional[list[WatchlistEntry]] = None
    remove_entries: Optional[list[WatchlistEntry]] = None


class WatchlistsListArgs(BaseModel):
    limit: int = Field(30, ge=1, le=100)
    cursor: int = Field(0, ge=0)


class WatchlistViewArgs(WatchlistsListArgs):
    id: str
    lookback: int = Field(20, ge=1, description="Number of observation intervals; dates/frequency are returned for each series.")
    as_of: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    query: str = ''
    currency: Optional[str] = None
    min_change_pct: Optional[float] = Field(None, allow_inf_nan=False)
    max_drawdown_pct: Optional[float] = Field(None, ge=0, allow_inf_nan=False, description="Maximum drawdown magnitude in percent over this window.")
    sort_by: Literal['change_pct', 'drawdown_pct', 'symbol'] = 'change_pct'
    descending: bool = True


class WatchlistRefreshArgs(BaseModel):
    id: str
    start: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    end: Optional[str] = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


# ------------------------------------------------------------------ theses
class ThesisCreateArgs(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    claim: str = Field(..., min_length=3, max_length=2000, description="One or two falsifiable sentences.")
    rival: str = Field(..., min_length=12, max_length=2000, description="Forced rival hypothesis: the best alternative explanation of the same facts.")
    as_of: Optional[str] = Field(None, max_length=10, description="Knowledge cutoff (default today). Evidence metrics never use data after it.")
    horizon: str = Field("", max_length=12, description="6m, 12m, 2y or a date.")
    assets: Optional[list[Union[str, dict[str, Any]]]] = Field(None, description='["AAPL"], snapshot ids ("snp_...") or [{"symbol","provider","snapshot_id","alias"}]; snapshot ids are stored with the snapshot symbol and provider')
    assumptions: Optional[list[str]] = None
    rules: Optional[list[Union[str, dict[str, Any]]]] = Field(None, description='Invalidation rules, true = invalidated: "close(AAPL) < 150", "yoy(CPIAUCSL) > 4", "drawdown(^GSPC) < -20", "sma(x,50) < sma(x,200)".')
    notes: str = Field("", max_length=4000)


class ThesisGetArgs(BaseModel):
    id: str = Field(..., max_length=40)
    history: bool = Field(False, description="Also list the previous checks.")


class ThesisListArgs(BaseModel):
    status: Optional[Literal["open", "confirmed", "invalidated", "expired", "archived"]] = None
    query: Optional[str] = Field(None, max_length=120)
    limit: int = Field(30, ge=1, le=100)
    cursor: Optional[int] = Field(None, ge=0)


class ThesisUpdateArgs(BaseModel):
    id: str = Field(..., max_length=40)
    action: Literal["update", "delete"] = "update"
    confirm: bool = Field(False, description="Required with action=delete.")
    title: Optional[str] = Field(None, max_length=200)
    claim: Optional[str] = Field(None, max_length=2000)
    rival: Optional[str] = Field(None, max_length=2000)
    horizon: Optional[str] = Field(None, max_length=12)
    status: Optional[Literal["open", "confirmed", "invalidated", "expired", "archived"]] = None
    assets: Optional[list[Union[str, dict[str, Any]]]] = None
    assumptions: Optional[list[str]] = None
    rules: Optional[list[Union[str, dict[str, Any]]]] = Field(None, description="Replaces all rules (same syntax as thesis_create).")
    notes: Optional[str] = Field(None, max_length=4000)


class EvidenceArgs(BaseModel):
    thesis_id: str = Field(..., max_length=40)
    action: Literal["add", "delete"] = "add"
    confirm: bool = False
    evidence_id: Optional[str] = Field(None, max_length=40, description="For action=delete.")
    side: Optional[Literal["for", "against"]] = None
    kind: Optional[Literal["snapshot_metric", "url", "note", "family_ref"]] = None
    ref: str = Field("", max_length=500, description="url, hoard://app/kind/id reference, or a snapshot id.")
    title: str = Field("", max_length=200)
    quote: str = Field("", max_length=500, description="Short quote or locator.")
    snapshot_id: str = Field("", max_length=40, description="snapshot_metric: the snapshot the metric comes from.")
    expr: str = Field("", max_length=400, description="snapshot_metric: rule-language number, e.g. yoy(CPIAUCSL); default the last close.")


class CheckArgs(BaseModel):
    id: str = Field(..., max_length=40)
    as_of: Optional[str] = Field(None, max_length=10, description="Check date (default today); never before the thesis as_of.")
    refresh: bool = Field(True, description="Re-fetch the needed series first; falls back to the latest stored snapshot with a warning.")


# ---------------------------------------------------------------------- lab
class SpecArgs(BaseModel):
    spec: dict[str, Any] = Field(..., description="Declarative strategy spec (JSON, no code). See the example in this description.")


class StrategySaveArgs(BaseModel):
    action: Literal["save", "delete"] = "save"
    confirm: bool = False
    spec: Optional[dict[str, Any]] = None
    spec_patch: Optional[dict[str, Any]] = Field(None, description="Atomically merges into the saved strategy named by id instead of replacing it: objects merge, arrays replace, null removes a key.")
    id: Optional[str] = Field(None, max_length=80, description="Strategy id or name: for action=delete, and for spec_patch.")
    note: str = Field("", max_length=500)


class StrategiesListArgs(BaseModel):
    id: Optional[str] = Field(None, max_length=80, description="One strategy (id or name) with its full spec.")


class BacktestRunArgs(BaseModel):
    spec: Optional[dict[str, Any]] = None
    strategy: Optional[str] = Field(None, max_length=80, description="A saved strategy (id or name) instead of spec.")
    as_of: Optional[str] = Field(None, max_length=10, description="Data cutoff: nothing after it is used.")
    holdout_fraction: Optional[float] = Field(None, ge=0.1, le=0.5, description="Seal this share of the latest bars as an untouched holdout.")
    split_date: Optional[str] = Field(None, max_length=10, description="Seal everything from this date instead of a fraction.")
    label: str = Field("", max_length=200)


class BacktestValidateArgs(BaseModel):
    run_id: str = Field(..., max_length=40)
    methods: Optional[list[Literal["walk_forward", "permutation", "bootstrap", "multiple_testing"]]] = None
    windows: int = Field(5, ge=2, le=12)
    grid: Optional[dict[str, list[Any]]] = Field(None, description='Walk-forward parameter grid, e.g. {"indicators.fast.window": [10, 20, 50]}. Each candidate is logged as a variant.')
    n: int = Field(1000, ge=100, le=20000, description="Monte Carlo / bootstrap resamples.")
    block: Optional[int] = Field(None, ge=2, le=250)
    seed: int = Field(0, ge=0, le=2**31 - 1)
    reveal_holdout: bool = Field(False, description="Open the sealed holdout (counted in the log). Only when the spec is final.")


class ExperimentsArgs(BaseModel):
    run_id: Optional[str] = Field(None, max_length=40, description="One run in detail (metrics, equity, trades, validations).")
    family: Optional[str] = Field(None, max_length=80)
    kind: Optional[Literal["backtest", "validation", "holdout", "wf_candidate"]] = None
    status: Optional[Literal["ok", "failed", "rejected"]] = None
    limit: int = Field(30, ge=1, le=100)
    cursor: Optional[int] = Field(None, ge=0)


class DataHealthArgs(BaseModel):
    snapshot_ids: Optional[list[str]] = Field(None, max_length=40, description="Snapshots to check (ids from snapshots_list).")
    run_id: Optional[str] = Field(None, max_length=40, description="Check the series a backtest used, on its data window.")
    strategy: Optional[str] = Field(None, max_length=80, description="A saved strategy (id or name): checks its universe and benchmark.")
    spec: Optional[dict[str, Any]] = Field(None, description="A strategy spec (not yet saved): checks its universe and benchmark.")
    start: Optional[str] = Field(None, max_length=10, description="YYYY-MM-DD; default the dates all the series share (or the whole range for one).")
    end: Optional[str] = Field(None, max_length=10, description="YYYY-MM-DD")


class DuplicatesArgs(BaseModel):
    run_id: Optional[str] = Field(None, max_length=40, description="Duplicate risk of this experiment against the other families.")
    strategy: Optional[str] = Field(None, max_length=80, description="A saved strategy (id or name), checked before running it.")
    spec: Optional[dict[str, Any]] = Field(None, description="A spec not yet run, checked before spending a trial on it.")
    min_level: Literal["low", "medium", "high"] = Field("medium", description="With nothing else given: list the experiments at or above this risk.")
    limit: int = Field(20, ge=1, le=100)


class CommitteeArgs(BaseModel):
    thesis_id: str = Field(..., max_length=40)
    use_model: bool = Field(True, description="False returns the evidence pack and deterministic metrics without asking a model.")


# ---------------------------------------------------------------- portfolio
class PortfolioSetArgs(BaseModel):
    name: str = Field("default", min_length=1, max_length=60)
    action: Literal["set", "delete", "patch"] = Field("set", description="patch atomically merges upsert/remove/currency into the existing portfolio, leaving other holdings as they are.")
    confirm: bool = False
    upsert: Optional[list[dict[str, Any]]] = Field(None, description="patch: holdings matched by symbol; only the given fields change (null drops one); unknown symbols are added. Every holding has a stable lot_id (shown by portfolio_analyze and in the results); for a symbol held in several lots pass lot_id to change exactly that lot (other fields changed meanwhile are kept; a lot that no longer exists -> conflict, nothing changes). Legacy alternative: match={quantity, cost_basis, snapshot_id, currency} with the lot's previous values (first equal lot; none -> conflict). new_lot=true appends another lot of an existing symbol.")
    remove: Optional[list[Any]] = Field(None, description="patch: symbols to remove (all their lots), or {lot_id} to remove exactly one lot (gone -> conflict), or {symbol, match} to remove one lot identified by its previous values (none equal -> conflict).")
    holdings: Optional[list[dict[str, Any]]] = Field(None, description='(lot_id optional: keep it to preserve a lot''s identity) [{"symbol": "AAPL", "quantity": 10, "currency": "USD", "cost_basis": 150, "snapshot_id": "snp_..."}]')
    csv_text: Optional[str] = Field(None, max_length=1_000_000, description="CSV with header: symbol,quantity[,snapshot_id,currency,cost_basis,label]")
    path: Optional[str] = Field(None, max_length=1000)
    currency: Optional[str] = Field(None, max_length=3, description="Base currency of the portfolio (default EUR).")
    replace: bool = True


class PortfolioAnalyzeArgs(BaseModel):
    name: Optional[str] = Field(None, max_length=60, description="Omit to list the portfolios.")
    date: Optional[str] = Field(None, max_length=10, description="Valuation date (prices on or before it); default the latest common date.")
    currency: Optional[str] = Field(None, max_length=3)
    fx: Optional[Union[str, dict[str, str]]] = Field(None, description='A rate snapshot id (e.g. the EURUSD=X one; the pair is read from it) or {"USD": "<snapshot id of the USD/base rate>"}')


class ReportArgs(BaseModel):
    kind: Literal["thesis", "run", "portfolio"]
    id: str = Field(..., max_length=60)
    format: Literal["md", "json", "html"] = Field("md", description="html: run reports only (tagged claims and a decision badge).")
    save: bool = Field(True, description="Also write the file under data/reports/.")
    cursor: int = Field(0, ge=0, description="Character offset when the Markdown is longer than one page.")
    page_chars: int = Field(14000, ge=1000, le=16000)


# ------------------------------------------------------------------ handlers
def run_midas_status(svc: Services, args: Empty) -> dict:
    return svc.status()


def run_market_providers(svc: Services, args: Empty) -> dict:
    return {"providers": svc.providers.describe(), "offline": svc.http.offline, "cache": svc.http.cache_stats(),
            "symbol_syntax": {"yahoo": "AAPL, ^GSPC, ^IBEX, SAN.MC, EURUSD=X, BTC-EUR", "alphavantage": "IBM, SAN.MAD (needs a key)", "tiingo": "aapl, spy (needs a key)", "stooq": "blocked: browser check", "fred": "CPIAUCSL, UNRATE, DGS10, FEDFUNDS", "ecb": "EXR/D.USD.EUR.SP00.A",
                              "coingecko": "bitcoin or bitcoin:usd (default eur)", "csv": "any name; needs path or csv_text and currency/unit",
                              "fake": "fake.up, fake.down, fake.flat, fake.vol, fakefx.eurusd, fakemacro.cpi"},
            "credits": "FRED: Federal Reserve Bank of St. Louis. ECB: European Central Bank Data Portal. CoinGecko: data provided by CoinGecko. Yahoo Finance (unofficial). Alpha Vantage and Tiingo with your own free key.",
            "disclaimer": "Free data; read each provider's terms in the snapshot. Historical analysis, not advice."}


def run_market_search(svc: Services, args: MarketSearchArgs) -> dict:
    return svc.market_search(args.query, provider=args.provider, remote=args.remote, limit=args.limit)


def run_market_fetch(svc: Services, args: MarketFetchArgs) -> dict:
    options = {k: v for k, v in {"path": args.path, "csv_text": args.csv_text, "mapping": args.mapping, "dayfirst": args.dayfirst, "delimiter": args.delimiter,
                                 "decimal": args.decimal, "frequency": args.frequency, "adjusted": args.adjusted}.items() if v not in (None, "", False)}
    return svc.market_fetch(provider=args.provider, symbol=args.symbol, start=args.start, end=args.end, interval=args.interval, currency=args.currency,
                            unit=args.unit, label=args.label, options=options)


def run_market_series(svc: Services, args: SeriesArgs) -> dict:
    p = args.model_dump()
    sid, fx = p.pop("snapshot_id"), p.pop("fx")
    p["vol_window"] = p["vol_window"] or None
    return svc.market_series(sid, fx=fx, **p)


def run_market_compare(svc: Services, args: CompareArgs) -> dict:
    p = args.model_dump()
    ids, fx = p.pop("snapshot_ids"), p.pop("fx")
    return svc.market_compare(ids, fx=fx, **p)


def run_snapshots_list(svc: Services, args: SnapshotsListArgs) -> dict:
    res = svc.snapshots_list(provider=args.provider, symbol=args.symbol, query=args.query, limit=args.limit, offset=args.cursor or 0)
    off = args.cursor or 0
    res["next_cursor"] = off + args.limit if off + args.limit < res["total"] else None
    return res


def run_watchlists_list(svc: Services, args: WatchlistsListArgs) -> dict:
    result = svc.watchlists.list(**args.model_dump())
    while len(result['watchlists']) > 1 and len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_RESULT_BYTES-512:
        result['watchlists'].pop()
    offset = args.cursor + len(result['watchlists'])
    result['next_cursor'] = offset if offset < result['total'] else None
    return result


def run_watchlist_save(svc: Services, args: WatchlistSaveArgs) -> dict:
    return svc.watchlists.summary(svc.watchlists.save(**args.model_dump(exclude_none=True)))


def run_watchlist_view(svc: Services, args: WatchlistViewArgs) -> dict:
    result = svc.watchlists.view(**args.model_dump())
    # Keep the shared result budget without skipping rows when a page is large.
    while len(result['items']) > 1 and len(json.dumps(result, ensure_ascii=False).encode('utf-8')) > MAX_RESULT_BYTES-512:
        result['items'].pop()
    offset = args.cursor + len(result['items'])
    result['next_cursor'] = offset if offset < result['total'] else None
    return result


def run_watchlist_refresh(svc: Services, args: WatchlistRefreshArgs) -> dict:
    return svc.watchlists.refresh(**args.model_dump())


def run_thesis_create(svc: Services, args: ThesisCreateArgs) -> dict:
    return svc.theses.create(title=args.title, claim=args.claim, rival=args.rival, as_of=args.as_of, horizon=args.horizon, assets=args.assets,
                             assumptions=args.assumptions, rules=args.rules, notes=args.notes)


def run_thesis_get(svc: Services, args: ThesisGetArgs) -> dict:
    t = svc.theses.get(args.id)
    if args.history:
        t["checks"] = svc.theses.checks(args.id)
    t["committee"] = svc.committee.history(args.id, limit=5)
    return t


def run_thesis_list(svc: Services, args: ThesisListArgs) -> dict:
    return svc.theses.list(status=args.status, query=args.query, limit=args.limit, cursor=args.cursor)


def run_thesis_update(svc: Services, args: ThesisUpdateArgs) -> dict:
    if args.action == "delete":
        _confirm("delete", args.confirm, f"thesis {args.id} with its evidence, checks and committee runs")
        return svc.theses.delete(args.id)
    patch = {k: v for k, v in args.model_dump().items() if k not in ("id", "action", "confirm") and v is not None}
    return svc.theses.update(args.id, patch)


def run_thesis_evidence(svc: Services, args: EvidenceArgs) -> dict:
    if args.action == "delete":
        _confirm("delete", args.confirm, f"evidence {args.evidence_id}")
        if not args.evidence_id:
            raise MidasError("invalid_request", "action=delete needs evidence_id.", "Get the thesis to see its evidence ids.")
        return svc.theses.evidence_delete(args.thesis_id, args.evidence_id)
    if not args.side or not args.kind:
        raise MidasError("invalid_request", "Adding evidence needs side (for|against) and kind.", "kind: snapshot_metric | url | note | family_ref.")
    return svc.theses.evidence_add(args.thesis_id, side=args.side, kind=args.kind, ref=args.ref, title=args.title, quote=args.quote,
                                   snapshot_id=args.snapshot_id, expr=args.expr)


def run_thesis_check(svc: Services, args: CheckArgs) -> dict:
    return svc.theses.check(args.id, as_of=args.as_of, refresh=args.refresh)


def run_strategy_validate(svc: Services, args: SpecArgs) -> dict:
    return svc.lab.validate(args.spec)


def run_strategy_save(svc: Services, args: StrategySaveArgs) -> dict:
    if args.action == "delete":
        _confirm("delete", args.confirm, f"strategy {args.id}")
        if not args.id:
            raise MidasError("invalid_request", "action=delete needs id (strategy id or name).", "List them with strategies_list.")
        return svc.lab.delete_strategy(args.id)
    if args.spec_patch is not None:
        if not args.id:
            raise MidasError("invalid_request", "spec_patch needs id (strategy id or name).", "List them with strategies_list.")
        return svc.lab.patch_strategy(args.id, args.spec_patch, args.note or None)
    if args.spec is None:
        raise MidasError("invalid_request", "action=save needs a spec.", "Validate it first with strategy_validate.")
    return svc.lab.save_strategy(args.spec, args.note)


def run_strategies_list(svc: Services, args: StrategiesListArgs) -> dict:
    if args.id:
        return svc.lab.strategy(args.id)
    items = svc.lab.strategies()
    for i in items:
        i.pop("spec", None)
    return {"strategies": items, "count": len(items)}


def run_backtest_run(svc: Services, args: BacktestRunArgs) -> dict:
    return svc.lab.run(spec=args.spec, strategy=args.strategy, as_of=args.as_of, holdout_fraction=args.holdout_fraction,
    split_date=args.split_date, label=args.label)


def run_backtest_validate(svc: Services, args: BacktestValidateArgs) -> dict:
    return svc.lab.validate_run(args.run_id, methods=args.methods, windows=args.windows, grid=args.grid, n=args.n, block=args.block,
    seed=args.seed, reveal_holdout=args.reveal_holdout)


def run_experiments_list(svc: Services, args: ExperimentsArgs) -> dict:
    if args.run_id:
        return svc.lab.run_get(args.run_id, cursor=args.cursor)
    return svc.lab.experiments(family=args.family, kind=args.kind, status=args.status, limit=args.limit, cursor=args.cursor)


def run_data_health(svc: Services, args: DataHealthArgs) -> dict:
    return svc.lab.data_health(snapshot_ids=args.snapshot_ids, run_id=args.run_id, strategy=args.strategy, spec=args.spec, start=args.start, end=args.end)


def run_experiments_duplicates(svc: Services, args: DuplicatesArgs) -> dict:
    return svc.lab.duplicates(run_id=args.run_id, spec=args.spec, strategy=args.strategy, min_level=args.min_level, limit=args.limit)


def run_committee(svc: Services, args: CommitteeArgs) -> dict:
    return svc.committee.run(args.thesis_id, use_model=args.use_model)


def run_portfolio_set(svc: Services, args: PortfolioSetArgs) -> dict:
    if args.action == "delete":
        _confirm("delete", args.confirm, f"portfolio '{args.name}'")
        return svc.portfolios.delete(args.name)
    if args.action == "patch":
        return svc.portfolios.patch(args.name, currency=args.currency, upsert=args.upsert, remove=args.remove)
    return svc.portfolios.set(args.name, holdings=args.holdings, csv_text=args.csv_text, path=args.path, currency=args.currency, replace=args.replace)


def run_portfolio_analyze(svc: Services, args: PortfolioAnalyzeArgs) -> dict:
    if not args.name:
        return {"portfolios": svc.portfolios.list()}
    return svc.portfolios.analyze(args.name, date=args.date, currency=args.currency, fx=args.fx)


def run_report_export(svc: Services, args: ReportArgs) -> dict:
    res = svc.report_export(args.kind, args.id, args.format, args.save)
    md = res["markdown"]
    page = md[args.cursor: args.cursor + args.page_chars]
    res["markdown"] = page
    res["total_chars"] = len(md)
    res["next_cursor"] = args.cursor + args.page_chars if args.cursor + args.page_chars < len(md) else None
    if res.get("json") is not None:
        data = res["json"]
        if len(json.dumps(data, default=str)) < MAX_RESULT_BYTES:
            res["json"] = cap_result({"data": data})["data"]
        else:  # too large to inline; a run's report contract (the part an assistant reads) always stays
            res["json"] = {"note": "JSON saved to `path`; too large to inline.", **({"report": data["report"]} if isinstance(data, dict) and "report" in data else {})}
    return res


_SPEC_EXAMPLE = json.dumps(EXAMPLE_SPEC, separators=(",", ":"))

TOOLS: list[Tool] = [
    Tool("midas_status",
         "Health: providers, data counts, models, disk. Estado del laboratorio financiero.\n"
         "Counts of snapshots, theses, strategies and experiments; provider availability; model resolution for the committee.\n"
         "Sinónimos: estado, salud, proveedores disponibles, modelos, cuántos datos hay.",
         Empty, _ann(True), run_midas_status),
    Tool("market_providers",
         "List data providers with terms, delay and licence. Proveedores de datos de mercado.\n"
         "yahoo (unofficial, default), fred, ecb, coingecko, alphavantage and tiingo (need a free key), stooq (blocked by a browser check), csv import, fake. Shows status (ok / needs_key / blocked), symbol syntax, cache and offline state.\n"
         "Sinónimos: fuentes de datos, términos de uso, retraso, licencia, yahoo, FRED, BCE, clave API.",
         Empty, _ann(True), run_market_providers),
    Tool("market_search",
         "Search symbols (indices, FX, stocks, macro series, crypto). Buscar símbolos y series.\n"
         "Local catalogue (seed list plus everything fetched before); remote=true also asks CoinGecko.\n"
         "Sinónimos: buscar activo, ticker, índice, divisa, serie macro, inflación, tipos de interés, bitcoin.",
         MarketSearchArgs, _ann(True, open_world=True), run_market_search),
    Tool("market_fetch",
         "Fetch a series and freeze it as a provenance-tagged snapshot. Descargar datos de mercado.\n"
         "Stores provider, symbol, fetch time, requested/actual range, frequency, currency, adjusted flag, unit, rows and sha256. Identical bytes reuse the snapshot. "
         "csv: pass path or csv_text, mapping and currency/unit.\n"
         "Sinónimos: descargar cotizaciones, precios históricos, importar csv, serie macro, tipo de cambio, snapshot.",
         MarketFetchArgs, _ann(False, False, True, open_world=True), run_market_fetch),
    Tool("market_series",
         "View a snapshot: points, returns, volatility, drawdown, resample, rebase. Ver una serie.\n"
         "Paginated (limit, cursor); as_of cuts the data point-in-time; convert_to + fx converts currency with the conversion recorded.\n"
         "Sinónimos: gráfico, rentabilidad, volatilidad, drawdown, caída máxima, rebasar a 100, semanal, mensual.",
         SeriesArgs, _ann(True), run_market_series),
    Tool("market_compare",
         "Compare 2-12 snapshots: correlation, relative performance, aligned dates. Comparar series.\n"
         "Refuses different currency, adjustment or frequency unless converted/resampled or allow_incompatible=true (recorded).\n"
         "Sinónimos: correlación, rendimiento relativo, comparar activos, cartera, divisas.",
         CompareArgs, _ann(True), run_market_compare),
    Tool("snapshots_list",
         "List stored snapshots with provenance. Listar datos congelados.\n"
         "Filter by provider, symbol or text; newest first; paginated.\n"
         "Sinónimos: snapshots, datos guardados, procedencia, hash sha256, qué series tengo.",
         SnapshotsListArgs, _ann(True), run_snapshots_list),
    Tool("thesis_create",
         "Create an investment thesis with a rival hypothesis and rules. Crear tesis de inversión.\n"
         "Needs title, claim, rival (forced), optional as_of cutoff, horizon, assets, assumptions and machine-checkable invalidation rules "
         "(close(AAPL) < 150, yoy(CPIAUCSL) > 4, drawdown(^GSPC) < -20, sma(x,50) < sma(x,200)); true = invalidated.\n"
         "Sinónimos: tesis, hipótesis, dossier de inversión, regla de invalidación, hipótesis rival.",
         ThesisCreateArgs, _ann(False, False, False), run_thesis_create),
    Tool("thesis_get",
         "Get a thesis with evidence, rules and last check state. Ver una tesis.\n"
         "Evidence for/against with labels E1..En, rule states, last check, committee history.\n"
         "Sinónimos: detalle de tesis, evidencias a favor y en contra, estado de las reglas.",
         ThesisGetArgs, _ann(True), run_thesis_get),
    Tool("thesis_list",
         "List theses by status or text. Listar tesis.\n"
         "Compact rows: status, as_of, evidence counts, rule states; paginated.\n"
         "Sinónimos: mis tesis, tesis abiertas, invalidadas, buscar tesis.",
         ThesisListArgs, _ann(True), run_thesis_list),
    Tool("thesis_update",
         "Edit a thesis (claim, rules, status...) or delete it (action=delete, confirm=true). Editar tesis.\n"
         "Replacing rules validates the syntax with precise errors. The rival hypothesis can be replaced, never emptied.\n"
         "Sinónimos: modificar tesis, cambiar reglas, archivar, confirmar, borrar tesis.",
         ThesisUpdateArgs, _ann(False, True, False), run_thesis_update),
    Tool("thesis_evidence_add",
         "Add evidence for/against a thesis (metric, url, note, hoard:// ref) or delete one. Añadir evidencia.\n"
         "snapshot_metric computes a number on data cut at the thesis as_of (expr e.g. yoy(CPIAUCSL)); note/url take a short quote or locator; "
         "family_ref stores only the ref and title.\n"
         "Sinónimos: evidencia a favor, evidencia en contra, dato, fuente, cita, referencia.",
         EvidenceArgs, _ann(False, True, False), run_thesis_evidence),
    Tool("thesis_check",
         "Check a thesis's invalidation rules on fresh data; may set it invalidated. Comprobar una tesis.\n"
         "Refreshes the needed snapshots (or uses the latest), evaluates each rule point-in-time, stores a check record, emits midas.thesis.invalidated. "
         "Idempotent: same data and rules, no new record.\n"
         "Sinónimos: revisar tesis, vigilar reglas, ¿se ha invalidado?, comprobación diaria.",
         CheckArgs, _ann(False, False, True, open_world=True), run_thesis_check),
    Tool("strategy_validate",
         "Validate a strategy spec (JSON, no code) with fixable errors. Validar estrategia.\n"
         "Universe of snapshot ids, indicators (sma, ema, rsi, roc, returns, vol, zscore, bollinger...), entry/exit rules, sizing "
         "(all_in, fixed_fraction, vol_target), rebalance, costs/slippage bps, benchmark. Example: " + _SPEC_EXAMPLE + "\n"
         "Sinónimos: comprobar estrategia, especificación, errores, sugerencias, backtest previo.",
         SpecArgs, _ann(True), run_strategy_validate),
    Tool("strategy_save",
         "Save a validated strategy by name, or delete one (action=delete, confirm=true). Guardar estrategia.\n"
         "Saving again under the same name updates it; experiments already run stay in the log.\n"
         "Sinónimos: guardar estrategia, borrar estrategia, versión.",
         StrategySaveArgs, _ann(False, True, True), run_strategy_save),
    Tool("strategies_list",
         "List saved strategies with the variants tried, or fetch one spec. Listar estrategias.\n"
         "Sinónimos: estrategias guardadas, especificación, variantes probadas.",
         StrategiesListArgs, _ann(True), run_strategies_list),
    Tool("backtest_run",
         "Backtest a strategy: metrics, equity, trades, benchmark, variants tried. Ejecutar backtest.\n"
         "Signals on bar t act on t+1 (no look-ahead). Reports total return, CAGR, vol, Sharpe, Sortino, max drawdown, Calmar, hit rate, turnover, "
         "exposure, costs. holdout_fraction seals the latest bars. Logs the experiment always; artifacts in data/runs/<run_id>/.\n"
         "Sinónimos: backtest, simulación histórica, rentabilidad, ratio de Sharpe, caída máxima, cartera de prueba.",
         BacktestRunArgs, _ann(False, False, False), run_backtest_run),
    Tool("backtest_validate",
         "Stress a backtest: walk-forward, permutation test, bootstrap Sharpe CI, multiple testing. Validar backtest.\n"
         "Seeded and reproducible. reveal_holdout=true opens the sealed holdout once (counted). Grid candidates count as variants.\n"
         "Sinónimos: validación, sobreajuste, walk-forward, p-valor, intervalo de confianza, datos fuera de muestra, holdout.",
         BacktestValidateArgs, _ann(False, False, False), run_backtest_validate),
    Tool("experiments_list",
         "List every backtest variant ever run, failures included, or one run in detail. Registro de experimentos.\n"
         "Shows variants tried per family and a multiple-testing warning level.\n"
         "Sinónimos: experimentos, historial de pruebas, variantes probadas, resultados negativos.",
         ExperimentsArgs, _ann(True), run_experiments_list),
    Tool("data_health",
         "Data health of series: % nulls, % zeros, coverage by date, 3-sigma outliers, long gaps. Salud de datos.\n"
         "Computed on raw frozen snapshots before any metric, per series and window, with the thresholds used; nulls are reported, never filled. "
         "Accepts snapshot_ids, a run_id, a saved strategy or a spec. Coverage under 90 % caps a report decision at 'investigate'.\n"
         "Sinónimos: calidad de datos, huecos, nulos, ceros, atípicos, cobertura, comprobar datos antes de interpretar.",
         DataHealthArgs, _ann(True), run_data_health),
    Tool("experiments_duplicates",
         "Find the same idea under another name: shared indicators, rules, universe, description. Duplicados.\n"
         "duplicate_risk low/medium/high with the similar experiments and why (Jaccard overlap of mechanism; embeddings or TF-IDF for the text). "
         "Never blocks; high-risk variants count in family_stats and the multiple-testing correction. Check a run_id, a strategy or a spec before running it.\n"
         "Sinónimos: duplicados, misma idea con otro nombre, variantes repetidas, solape de estrategias, riesgo de duplicado.",
         DuplicatesArgs, _ann(True), run_experiments_duplicates),
    Tool("committee_run",
         "Run the committee on a thesis: advocates, risk reviewer, arbiter, number ledger. Comité de inversión.\n"
         "Model-backed (Hoard Link); advocates cite only evidence ids; the arbiter's verdict (strengthen|weaken|inconclusive, confidence) is not a vote average; "
         "unmatched numbers are flagged. Without a model it returns the evidence pack as material.\n"
         "Sinónimos: comité, abogado del diablo, a favor y en contra, riesgos, veredicto, auditoría de cifras.",
         CommitteeArgs, _ann(False, False, False), run_committee),
    Tool("portfolio_set",
         "Set a portfolio's holdings (list or CSV) or delete it (action=delete, confirm=true). Definir cartera.\n"
         "Holdings: symbol, quantity, optional snapshot_id, currency, cost_basis, label; each holding gets a stable lot_id (keep it when re-sending holdings; patch addresses a lot by it). Read-only analysis: no orders, no broker.\n"
         "Sinónimos: cartera, posiciones, importar csv de posiciones, acciones que tengo.",
         PortfolioSetArgs, _ann(False, True, True), run_portfolio_set),
    Tool("portfolio_analyze",
         "Value and analyse a portfolio: allocation, currency exposure, vol, drawdown, correlation. Analizar cartera.\n"
         "Valuation at a date; other currencies need an exchange-rate snapshot (recorded). Omit name to list portfolios.\n"
         "Sinónimos: valoración, asignación, exposición a divisas, riesgo, concentración, correlación.",
         PortfolioAnalyzeArgs, _ann(True), run_portfolio_analyze),
    Tool("report_export",
         "Export a thesis, run or portfolio as Markdown/JSON with provenance for every number. Exportar informe.\n"
         "Snapshot ids, dates, providers, sha256, terms. Paginated by characters; also saved under data/reports/.\n"
         "Sinónimos: informe, exportar a markdown, memoria, resumen de tesis, informe de backtest.",
         ReportArgs, _ann(False, False, True), run_report_export),
]

TOOLS.extend([
    Tool('watchlists_list', 'List saved market watchlists and their pinned snapshot entries. Listas de seguimiento.\nSinónimos: listas guardadas, conjuntos de activos, seguimiento.',
         WatchlistsListArgs, _ann(True), run_watchlists_list),
    Tool('watchlist_save', 'Create or partially update a saved watchlist. Guardar lista de seguimiento.\nUnspecified fields are preserved; entries explicitly replaces the set. Use snapshot_id or provider/symbol.\nSinónimos: guardar selección, renombrar lista, activos observados.',
         WatchlistSaveArgs, _ann(False, False, True), run_watchlist_save),
    Tool('watchlist_view', 'Explore and filter a saved watchlist with dated metrics. Filtrar lista de seguimiento.\nCloses, percentage change and drawdown over observation intervals; currency, sort and pagination. Each row has snapshot SHA, dates and units. No network or LLM.\nSinónimos: explorar activos, variación, caída, screener.',
         WatchlistViewArgs, _ann(True), run_watchlist_view),
    Tool('watchlist_refresh', 'Fetch new snapshots for watchlist entries. Actualizar lista de seguimiento.\nExisting providers, partial errors, CSV sources kept and concurrent list edits preserved.\nSinónimos: refrescar activos, actualizar cotizaciones, nuevos datos.',
         WatchlistRefreshArgs, _ann(False, False, True, open_world=True), run_watchlist_refresh),
])

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def tool_catalog() -> list[dict]:
    return agentkit.tool_catalog(TOOLS)


def call_tool(services: Services, name: str, arguments: dict | None, *, cap: bool = True) -> Any:
    """Run one tool by name. The result is capped to ~20 KB (the shared Hoard Link cap) unless ``cap=False`` or inside
    ``with uncapped():`` (the web UI). ``KeyError`` (an ``UnknownTool``) for an unknown name."""
    return agentkit.call_tool(TOOLS_BY_NAME, services, name, arguments, cap=cap)
