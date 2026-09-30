"""Tools exposed to the assistant. One catalogue drives /api/agent/* and mcp_server.py."""

from __future__ import annotations

import contextlib
import contextvars
import json
from dataclasses import dataclass
from typing import Any, Callable, Literal, Optional, Union

from pydantic import BaseModel, Field

from .errors import MidasError
from .services import Services
from .strategy import EXAMPLE_SPEC

MAX_RESULT_BYTES = 20_000

AGENT_INSTRUCTIONS = """Midas's Hoard is a local financial research lab: frozen market data, theses with evidence and invalidation rules, and an honest strategy backtester. Research, not advice: never say buy or sell; give analyses with their uncertainty.
Flow: market_fetch (a snapshot with provenance) -> thesis_create (needs a rival hypothesis; rules like close(AAPL) < 150 or yoy(CPIAUCSL) > 4) -> thesis_evidence_add -> thesis_check. Labs: strategy_validate -> backtest_run (holdout_fraction seals a holdout) -> backtest_validate. Every variant is logged; quote the variants count with any metric.
Quote numbers only from tool results. Never merge series of different currency or frequency: convert or resample explicitly. Write tools (fetch, create, update, check, save, run, validate, set) only when the user asks; deletes need confirm=true."""


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    input_model: type[BaseModel]
    annotations: dict[str, bool]
    run: Callable[[Services, Any], Any]


def _ann(read_only: bool, destructive: bool = False, idempotent: Optional[bool] = None, open_world: bool = False) -> dict[str, bool]:
    return {"readOnlyHint": read_only, "destructiveHint": destructive, "idempotentHint": read_only if idempotent is None else idempotent, "openWorldHint": open_world}


_UNCAPPED: contextvars.ContextVar[bool] = contextvars.ContextVar("midas_uncapped", default=False)


@contextlib.contextmanager
def uncapped():
    """The web UI shares the tool handlers but is not bound by the assistant's context budget."""
    token = _UNCAPPED.set(True)
    try:
        yield
    finally:
        _UNCAPPED.reset(token)


def cap_result(data: dict[str, Any], limit: int = MAX_RESULT_BYTES) -> dict[str, Any]:
    """Keep a result under ~20 KB: halve the largest list until it fits, and say what was cut."""
    if _UNCAPPED.get():
        return data
    def size(d: Any) -> int:
        return len(json.dumps(d, default=str, ensure_ascii=False).encode("utf-8"))

    if size(data) <= limit:
        return data
    data = dict(data)
    truncated: dict[str, Any] = {}
    for _ in range(40):
        if size(data) <= limit - 300:
            break
        candidates = [(k, v) for k, v in data.items() if isinstance(v, list) and len(v) > 1]
        nested = []
        for k, v in data.items():
            if isinstance(v, dict):
                nested += [((k, k2), v2) for k2, v2 in v.items() if isinstance(v2, list) and len(v2) > 1]
        if not candidates and not nested:
            break
        best_top = max(candidates, key=lambda kv: size(kv[1]), default=None)
        best_nested = max(nested, key=lambda kv: size(kv[1]), default=None)
        if best_top and (not best_nested or size(best_top[1]) >= size(best_nested[1])):
            key, value = best_top
            truncated.setdefault(key, len(value))
            data[key] = value[: max(1, len(value) // 2)]
        else:
            (k1, k2), value = best_nested
            truncated.setdefault(f"{k1}.{k2}", len(value))
            data[k1] = {**data[k1], k2: value[: max(1, len(value) // 2)]}
    data["truncated"] = {"reason": f"result capped at ~{limit // 1000} KB", "original_lengths": {str(k): v for k, v in truncated.items()},
                         "hint": "Use limit/cursor (or narrower ranges) to page through the rest."}
    return data


def _confirm(action: str, confirm: bool, what: str) -> None:
    if action == "delete" and not confirm:
        raise MidasError("confirm_required", f"Deleting {what} is permanent.", "Repeat the call with confirm=true if the user asked for it.")


class Empty(BaseModel):
    pass


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


# ------------------------------------------------------------------ theses
class ThesisCreateArgs(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    claim: str = Field(..., min_length=3, max_length=2000, description="One or two falsifiable sentences.")
    rival: str = Field(..., min_length=12, max_length=2000, description="Forced rival hypothesis: the best alternative explanation of the same facts.")
    as_of: Optional[str] = Field(None, max_length=10, description="Knowledge cutoff (default today). Evidence metrics never use data after it.")
    horizon: str = Field("", max_length=12, description="6m, 12m, 2y or a date.")
    assets: Optional[list[Union[str, dict[str, Any]]]] = Field(None, description='["AAPL"] or [{"symbol","provider","snapshot_id","alias"}]')
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
    id: Optional[str] = Field(None, max_length=80, description="For action=delete: strategy id or name.")
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


class CommitteeArgs(BaseModel):
    thesis_id: str = Field(..., max_length=40)
    use_model: bool = Field(True, description="False returns the evidence pack and deterministic metrics without asking a model.")


# ---------------------------------------------------------------- portfolio
class PortfolioSetArgs(BaseModel):
    name: str = Field("default", min_length=1, max_length=60)
    action: Literal["set", "delete"] = "set"
    confirm: bool = False
    holdings: Optional[list[dict[str, Any]]] = Field(None, description='[{"symbol": "AAPL", "quantity": 10, "currency": "USD", "cost_basis": 150, "snapshot_id": "snp_..."}]')
    csv_text: Optional[str] = Field(None, max_length=1_000_000, description="CSV with header: symbol,quantity[,snapshot_id,currency,cost_basis,label]")
    path: Optional[str] = Field(None, max_length=1000)
    currency: Optional[str] = Field(None, max_length=3, description="Base currency of the portfolio (default EUR).")
    replace: bool = True


class PortfolioAnalyzeArgs(BaseModel):
    name: Optional[str] = Field(None, max_length=60, description="Omit to list the portfolios.")
    date: Optional[str] = Field(None, max_length=10, description="Valuation date (prices on or before it); default the latest common date.")
    currency: Optional[str] = Field(None, max_length=3)
    fx: Optional[dict[str, str]] = Field(None, description='{"USD": "<snapshot id of the USD/base rate>"}')


class ReportArgs(BaseModel):
    kind: Literal["thesis", "run", "portfolio"]
    id: str = Field(..., max_length=60)
    format: Literal["md", "json"] = "md"
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
    return cap_result(svc.market_series(sid, fx=fx, **p))


def run_market_compare(svc: Services, args: CompareArgs) -> dict:
    p = args.model_dump()
    ids, fx = p.pop("snapshot_ids"), p.pop("fx")
    return cap_result(svc.market_compare(ids, fx=fx, **p))


def run_snapshots_list(svc: Services, args: SnapshotsListArgs) -> dict:
    res = svc.snapshots_list(provider=args.provider, symbol=args.symbol, query=args.query, limit=args.limit, offset=args.cursor or 0)
    off = args.cursor or 0
    res["next_cursor"] = off + args.limit if off + args.limit < res["total"] else None
    return cap_result(res)


def run_thesis_create(svc: Services, args: ThesisCreateArgs) -> dict:
    return svc.theses.create(title=args.title, claim=args.claim, rival=args.rival, as_of=args.as_of, horizon=args.horizon, assets=args.assets,
                             assumptions=args.assumptions, rules=args.rules, notes=args.notes)


def run_thesis_get(svc: Services, args: ThesisGetArgs) -> dict:
    t = svc.theses.get(args.id)
    if args.history:
        t["checks"] = svc.theses.checks(args.id)
    t["committee"] = svc.committee.history(args.id, limit=5)
    return cap_result(t)


def run_thesis_list(svc: Services, args: ThesisListArgs) -> dict:
    return cap_result(svc.theses.list(status=args.status, query=args.query, limit=args.limit, cursor=args.cursor))


def run_thesis_update(svc: Services, args: ThesisUpdateArgs) -> dict:
    if args.action == "delete":
        _confirm("delete", args.confirm, f"thesis {args.id} with its evidence, checks and committee runs")
        return svc.theses.delete(args.id)
    patch = {k: v for k, v in args.model_dump().items() if k not in ("id", "action", "confirm") and v is not None}
    return cap_result(svc.theses.update(args.id, patch))


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
    return cap_result(svc.theses.check(args.id, as_of=args.as_of, refresh=args.refresh))


def run_strategy_validate(svc: Services, args: SpecArgs) -> dict:
    return svc.lab.validate(args.spec)


def run_strategy_save(svc: Services, args: StrategySaveArgs) -> dict:
    if args.action == "delete":
        _confirm("delete", args.confirm, f"strategy {args.id}")
        if not args.id:
            raise MidasError("invalid_request", "action=delete needs id (strategy id or name).", "List them with strategies_list.")
        return svc.lab.delete_strategy(args.id)
    if args.spec is None:
        raise MidasError("invalid_request", "action=save needs a spec.", "Validate it first with strategy_validate.")
    return svc.lab.save_strategy(args.spec, args.note)


def run_strategies_list(svc: Services, args: StrategiesListArgs) -> dict:
    if args.id:
        return svc.lab.strategy(args.id)
    items = svc.lab.strategies()
    for i in items:
        i.pop("spec", None)
    return cap_result({"strategies": items, "count": len(items)})


def run_backtest_run(svc: Services, args: BacktestRunArgs) -> dict:
    return cap_result(svc.lab.run(spec=args.spec, strategy=args.strategy, as_of=args.as_of, holdout_fraction=args.holdout_fraction,
                                  split_date=args.split_date, label=args.label))


def run_backtest_validate(svc: Services, args: BacktestValidateArgs) -> dict:
    return cap_result(svc.lab.validate_run(args.run_id, methods=args.methods, windows=args.windows, grid=args.grid, n=args.n, block=args.block,
                                           seed=args.seed, reveal_holdout=args.reveal_holdout))


def run_experiments_list(svc: Services, args: ExperimentsArgs) -> dict:
    if args.run_id:
        return cap_result(svc.lab.run_get(args.run_id, cursor=args.cursor))
    return cap_result(svc.lab.experiments(family=args.family, kind=args.kind, status=args.status, limit=args.limit, cursor=args.cursor))


def run_committee(svc: Services, args: CommitteeArgs) -> dict:
    return cap_result(svc.committee.run(args.thesis_id, use_model=args.use_model))


def run_portfolio_set(svc: Services, args: PortfolioSetArgs) -> dict:
    if args.action == "delete":
        _confirm("delete", args.confirm, f"portfolio '{args.name}'")
        return svc.portfolios.delete(args.name)
    return svc.portfolios.set(args.name, holdings=args.holdings, csv_text=args.csv_text, path=args.path, currency=args.currency, replace=args.replace)


def run_portfolio_analyze(svc: Services, args: PortfolioAnalyzeArgs) -> dict:
    if not args.name:
        return {"portfolios": svc.portfolios.list()}
    return cap_result(svc.portfolios.analyze(args.name, date=args.date, currency=args.currency, fx=args.fx))


def run_report_export(svc: Services, args: ReportArgs) -> dict:
    res = svc.report_export(args.kind, args.id, args.format, args.save)
    md = res["markdown"]
    page = md[args.cursor: args.cursor + args.page_chars]
    res["markdown"] = page
    res["total_chars"] = len(md)
    res["next_cursor"] = args.cursor + args.page_chars if args.cursor + args.page_chars < len(md) else None
    if res.get("json") is not None:
        res["json"] = cap_result({"data": res["json"]})["data"] if len(json.dumps(res["json"], default=str)) < MAX_RESULT_BYTES else {"note": "JSON saved to `path`; too large to inline."}
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
    Tool("committee_run",
         "Run the committee on a thesis: advocates, risk reviewer, arbiter, number ledger. Comité de inversión.\n"
         "Model-backed (Hoard Link); advocates cite only evidence ids; the arbiter's verdict (strengthen|weaken|inconclusive, confidence) is not a vote average; "
         "unmatched numbers are flagged. Without a model it returns the evidence pack as material.\n"
         "Sinónimos: comité, abogado del diablo, a favor y en contra, riesgos, veredicto, auditoría de cifras.",
         CommitteeArgs, _ann(False, False, False), run_committee),
    Tool("portfolio_set",
         "Set a portfolio's holdings (list or CSV) or delete it (action=delete, confirm=true). Definir cartera.\n"
         "Holdings: symbol, quantity, optional snapshot_id, currency, cost_basis, label. Read-only analysis: no orders, no broker.\n"
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

TOOLS_BY_NAME = {t.name: t for t in TOOLS}


def tool_catalog() -> list[dict]:
    return [
        {"name": t.name, "description": t.description, "annotations": t.annotations,
         "inputSchema": t.input_model.model_json_schema(by_alias=True)}
        for t in TOOLS
    ]


def call_tool(services: Services, name: str, arguments: dict | None) -> Any:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None:
        raise KeyError(f"Unknown tool: {name}")
    args = tool.input_model.model_validate(arguments or {})
    result = tool.run(services, args)
    if not isinstance(result, dict):
        result = {"result": result}
    return result
