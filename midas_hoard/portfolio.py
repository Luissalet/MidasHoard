"""A light, read-only portfolio: holdings in, valuation / allocation / exposure / risk out.

Nothing is executed or recommended. Valuation is at a date from snapshot prices (last close on or before it); mixed
currencies are converted with an exchange-rate snapshot whose choice is recorded (explicit via ``fx``, or the latest
matching pair, flagged ``auto``) — with none available the analysis refuses instead of adding dollars to euros.
"""

from __future__ import annotations

import csv
import io
import json
import math
import time
from pathlib import Path
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from . import indicators as ind
from .db import Database
from .errors import MidasError
from .series import _fx_pair, check_compatible, convert_frame
from .snapshots import Snapshot, SnapshotStore, provenance
from .theses import STALE_DAYS

MAX_HOLDINGS = 100


def parse_csv(text: str) -> list[dict[str, Any]]:
    """CSV with a header: symbol, quantity and optionally snapshot_id, currency, cost_basis, label."""
    reader = csv.DictReader(io.StringIO(text, newline=""), delimiter=";" if text.split("\n", 1)[0].count(";") > text.split("\n", 1)[0].count(",") else ",")
    rows = []
    for raw in reader:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        if not any(row.values()):
            continue
        rows.append(row)
    return rows


class Portfolios:
    def __init__(self, db: Database, store: SnapshotStore, clock: Callable[[], float] = time.time):
        self.db, self.store, self.clock = db, store, clock

    # --------------------------------------------------------------- storage
    def _clean(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not rows:
            raise MidasError("invalid_request", "A portfolio needs at least one holding.", "Pass holdings=[{symbol, quantity}] or csv_text with a header.")
        if len(rows) > MAX_HOLDINGS:
            raise MidasError("invalid_request", f"At most {MAX_HOLDINGS} holdings.", "Split the portfolio.")
        out = []
        for i, r in enumerate(rows):
            sym = str(r.get("symbol", "")).strip()
            sid = str(r.get("snapshot_id", "") or "").strip()
            if not sym and not sid:
                raise MidasError("invalid_request", f"Holding {i + 1} has neither symbol nor snapshot_id.", "Each holding needs one of them.")
            try:
                qty = float(str(r.get("quantity", "")).replace(",", "."))
            except ValueError:
                raise MidasError("invalid_request", f"Holding {i + 1} ({sym or sid}): quantity '{r.get('quantity')}' is not a number.", "Use 12 or 0.5.") from None
            if qty == 0 or math.isnan(qty):
                raise MidasError("invalid_request", f"Holding {i + 1} ({sym or sid}): quantity is zero.", "Remove the holding instead.")
            item: dict[str, Any] = {"symbol": sym, "quantity": qty}
            if sid:
                meta = self.store.meta(sid)
                item["snapshot_id"] = sid
                item["symbol"] = sym or meta["symbol"]
            if r.get("currency"):
                item["currency"] = str(r["currency"]).strip().upper()
            if str(r.get("cost_basis", "") or "").strip():
                try:
                    item["cost_basis"] = float(str(r["cost_basis"]).replace(",", "."))
                except ValueError:
                    raise MidasError("invalid_request", f"Holding {i + 1}: cost_basis '{r['cost_basis']}' is not a number.", "Cost per unit, in the holding's currency.") from None
            if r.get("label"):
                item["label"] = str(r["label"])[:80]
            out.append(item)
        return out

    def set(self, name: str, *, holdings: Optional[list[dict[str, Any]]] = None, csv_text: Optional[str] = None, path: Optional[str] = None,
            currency: Optional[str] = None, replace: bool = True) -> dict[str, Any]:
        name = name.strip() or "default"
        if not (1 <= len(name) <= 60):
            raise MidasError("invalid_request", "Portfolio name must be 1-60 characters.", "Use e.g. 'default'.")
        rows: list[dict[str, Any]] = list(holdings or [])
        if path:
            file = Path(path).expanduser()
            if not file.is_file():
                raise MidasError("not_found", f"CSV file not found: {file}", "Give an absolute path.")
            csv_text = file.read_text(encoding="utf-8-sig")
        if csv_text:
            rows.extend(parse_csv(csv_text))
        clean = self._clean(rows)
        existing = self.db.one("SELECT * FROM portfolios WHERE name = ?", (name,))
        if existing and not replace:
            clean = json.loads(existing["holdings"]) + clean
        ccy = (currency or (existing["currency"] if existing else "EUR")).upper()
        if len(ccy) != 3:
            raise MidasError("invalid_request", f"Currency '{ccy}' is not a 3-letter code.", "Use EUR, USD...")
        self.db.execute("INSERT INTO portfolios(name, currency, holdings, updated_ts) VALUES (?,?,?,?) "
                        "ON CONFLICT(name) DO UPDATE SET currency=excluded.currency, holdings=excluded.holdings, updated_ts=excluded.updated_ts",
                        (name, ccy, json.dumps(clean), self.clock()))
        return {"name": name, "currency": ccy, "holdings": clean, "count": len(clean), "replaced": bool(existing and replace)}

    def get(self, name: str) -> dict[str, Any]:
        row = self.db.one("SELECT * FROM portfolios WHERE name = ?", (name,))
        if row is None:
            raise MidasError("not_found", f"Unknown portfolio '{name}'.", "Create it with portfolio_set.")
        return {"name": row["name"], "currency": row["currency"], "holdings": json.loads(row["holdings"]), "updated_at": row["updated_ts"]}

    def list(self) -> list[dict[str, Any]]:
        return [{"name": r["name"], "currency": r["currency"], "holdings": len(json.loads(r["holdings"])), "updated_at": r["updated_ts"]}
                for r in self.db.query("SELECT * FROM portfolios ORDER BY name")]

    def delete(self, name: str) -> dict[str, Any]:
        self.get(name)
        self.db.execute("DELETE FROM portfolios WHERE name = ?", (name,))
        return {"deleted": name}

    # -------------------------------------------------------------- analysis
    def _fx_for(self, source: str, target: str, explicit: dict[str, str], as_of: Optional[str]) -> tuple[Snapshot, bool]:
        if source in explicit:
            return self.store.load(explicit[source]), False
        for row in self.db.query("SELECT id FROM snapshots WHERE extra LIKE '%\"base\"%' ORDER BY fetched_at DESC, rowid DESC"):
            snap = self.store.load(row["id"])
            try:
                base, quote = _fx_pair(snap)
            except MidasError:
                continue
            if {base, quote} == {source, target}:
                return snap, True
        raise MidasError("incompatible_series", f"A holding is in {source} but the portfolio is in {target}, and no {source}/{target} exchange-rate snapshot exists.",
                         f"Fetch the rate (e.g. market_fetch eurusd) and pass fx={{\"{source}\": \"<snapshot id>\"}}, or set the portfolio currency to {source}.")

    def analyze(self, name: str, *, date: Optional[str] = None, currency: Optional[str] = None, fx: Any = None) -> dict[str, Any]:
        pf = self.get(name)
        base = (currency or pf["currency"]).upper()
        warnings: list[str] = []
        if isinstance(fx, str) and fx.strip():
            rate_snap = self.store.load(fx.strip())
            b, q = _fx_pair(rate_snap)
            if base not in (b, q):
                raise MidasError("incompatible_series", f"Rate snapshot {rate_snap.id} is {b}/{q}, which does not involve the portfolio currency {base}.",
                                 "Pass a rate that has the portfolio currency on one side, or set currency.")
            other = q if base == b else b
            fx = {other: rate_snap.id}
            warnings.append(f"fx {rate_snap.id} ({rate_snap.meta['symbol']}, {b}/{q}) applied to convert {other} holdings to {base}.")
        explicit = {k.upper(): v for k, v in (fx or {}).items()}
        conversions: list[dict[str, Any]] = []
        closes: dict[str, pd.Series] = {}
        rows: list[dict[str, Any]] = []
        metas: list[dict[str, Any]] = []
        snaps: list[Snapshot] = []
        missing = []
        for h in pf["holdings"]:
            meta = self.store.meta(h["snapshot_id"]) if h.get("snapshot_id") else (self.store.latest_for_symbol(h["symbol"]))
            if meta is None:
                missing.append(h["symbol"])
                continue
            snap = self.store.load(meta["id"], as_of=date)
            if snap.df.empty:
                missing.append(f"{h['symbol']} (no prices on or before {date})")
                continue
            ccy = (h.get("currency") or meta["currency"] or "").upper()
            if not ccy:
                raise MidasError("incompatible_series", f"{h['symbol']} ({meta['id']}) has no currency (an index or rate cannot be valued).",
                                 "Set currency on the holding, or hold a priced instrument.")
            df = snap.df
            m = dict(meta, currency=ccy)
            rec = None
            if ccy != base:
                rate, auto = self._fx_for(ccy, base, explicit, date)
                rate_cut = Snapshot(rate.meta, rate.df if not date else rate.df[rate.df.index <= pd.Timestamp(date)])
                df, rec = convert_frame(df, m, rate_cut, base, as_of=date)
                rec = {**rec, "auto": auto, "holding": h["symbol"]}
                conversions.append(rec)
                if auto:
                    warnings.append(f"{h['symbol']}: converted {ccy}->{base} with the latest matching rate snapshot {rate.id} (auto-selected; pass fx to choose).")
            if df.empty:
                missing.append(f"{h['symbol']} (no overlapping exchange rate)")
                continue
            last_date = df.index[-1]
            closes[h["symbol"] + "|" + meta["id"]] = df["close"]
            snaps.append(Snapshot(dict(m, currency=base), df))
            metas.append(meta)
            rows.append({"symbol": h["symbol"], "label": h.get("label", ""), "snapshot": meta["id"], "currency": ccy, "quantity": h["quantity"],
                         "price_date": last_date.strftime("%Y-%m-%d"), "price_native": float(snap.df["close"].iloc[-1]),
                         "price_base": float(df["close"].iloc[-1]), "cost_basis": h.get("cost_basis"), "frequency": meta["frequency"],
                         "_key": h["symbol"] + "|" + meta["id"]})
        if missing:
            raise MidasError("no_data", "No prices for: " + ", ".join(missing) + ".", "market_fetch each symbol first (snapshot_id on the holding pins a specific snapshot).",
                             missing=missing)
        warnings.extend(check_compatible(snaps, allow_incompatible=True, need_same_frequency=False))
        valuation_date = date or min(r["price_date"] for r in rows)
        total = 0.0
        for r in rows:
            r["value_base"] = r["quantity"] * r["price_base"]
            total += r["value_base"]
            gap = (pd.Timestamp(valuation_date) - pd.Timestamp(r["price_date"])).days
            if gap > STALE_DAYS.get(r["frequency"], 10):
                warnings.append(f"{r['symbol']}: last price {r['price_date']} is {gap} days before the valuation date.")
            if r["quantity"] < 0:
                warnings.append(f"{r['symbol']}: short position (negative quantity); weights can exceed 100%.")
        gross = sum(abs(r["value_base"]) for r in rows) or 1.0
        for r in rows:
            r["weight"] = r["value_base"] / total if total else None
            if r["cost_basis"] is not None:
                r["unrealised_pnl_native"] = (r["price_native"] - r["cost_basis"]) * r["quantity"]
                r["unrealised_pnl_pct"] = r["price_native"] / r["cost_basis"] - 1.0 if r["cost_basis"] else None
        exposure: dict[str, float] = {}
        for r in rows:
            exposure[r["currency"]] = exposure.get(r["currency"], 0.0) + r["value_base"] / gross
        weights = np.array([abs(r["value_base"]) / gross for r in rows])
        risk: dict[str, Any] = {"note": "History is the window all holdings share; constant quantities (no rebalancing)."}
        frame = pd.DataFrame({r["_key"]: closes[r["_key"]] for r in rows}).dropna()
        qty = pd.Series({r["_key"]: r["quantity"] for r in rows})
        series_points: list[dict[str, Any]] = []
        if len(frame) >= 30:
            value = (frame * qty).sum(axis=1)
            ppy = float(metas[0].get("periods_per_year") or 252.0)
            if len({m["frequency"] for m in metas}) > 1:
                warnings.append("holdings have different frequencies; risk figures use the common dates and may understate volatility.")
            rets = value.pct_change().dropna()
            dd = ind.drawdown(value) / 100.0
            risk.update({"observations": int(len(value)), "start": value.index[0].strftime("%Y-%m-%d"), "end": value.index[-1].strftime("%Y-%m-%d"),
                         "ann_vol": float(rets.std() * math.sqrt(ppy)), "max_drawdown": float(dd.min()), "current_drawdown": float(dd.iloc[-1]),
                         "total_return_same_quantities": float(value.iloc[-1] / value.iloc[0] - 1.0)})
            hret = frame.pct_change().dropna()
            corr = hret.corr()
            labels = {r["_key"]: r["symbol"] for r in rows}
            risk["correlation"] = {labels[a]: {labels[b]: (None if pd.isna(corr.loc[a, b]) else round(float(corr.loc[a, b]), 4)) for b in corr.columns} for a in corr.index}
            if len(rows) > 1 and rets.var() > 0:
                w = (frame.iloc[-1] * qty) / (frame.iloc[-1] * qty).sum()
                cov = hret.cov() * ppy
                port_var = float(w.values @ cov.values @ w.values)
                contrib = (w.values * (cov.values @ w.values)) / port_var if port_var > 0 else np.zeros(len(w))
                risk["risk_contribution"] = {labels[k]: round(float(c), 4) for k, c in zip(w.index, contrib)}
            step = max(1, math.ceil(len(value) / 120))
            sampled = value.iloc[::step]
            if sampled.index[-1] != value.index[-1]:
                sampled = pd.concat([sampled, value.iloc[[-1]]])
            series_points = [{"date": ts.strftime("%Y-%m-%d"), "value": round(float(v), 2)} for ts, v in sampled.items()]
        else:
            risk["note"] = f"Only {len(frame)} shared observations: no volatility/correlation."
        for r in rows:
            r.pop("_key", None)
        hhi = float(np.sum(weights ** 2))
        return {
            "portfolio": name, "currency": base, "valuation_date": valuation_date, "total_value": total, "holdings": rows,
            "allocation": sorted([{"symbol": r["symbol"], "weight": r["weight"]} for r in rows], key=lambda x: -abs(x["weight"] or 0)),
            "currency_exposure": {k: round(v, 6) for k, v in exposure.items()}, "concentration": {"top_weight": float(weights.max()), "hhi": hhi,
                                                                                               "effective_holdings": float(1 / hhi) if hhi else None},
            "risk": risk, "conversions": conversions, "snapshots": [provenance(m) for m in metas], "points": series_points, "warnings": warnings,
            "disclaimer": "Historical analysis of the holdings as entered; not advice.",
        }
