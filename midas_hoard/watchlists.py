"""Saved research sets over immutable snapshots, with per-entry refresh."""
from __future__ import annotations

import json
import math
import uuid
from typing import Any

import pandas as pd

from .errors import MidasError
from .snapshots import provenance


class Watchlists:
    def __init__(self, db, store, fetch, clock):
        self.db, self.store, self.fetch, self.clock = db, store, fetch, clock

    @staticmethod
    def _record(row):
        result = dict(row)
        result['entries'] = json.loads(result['entries'])
        return result

    def get(self, id):
        row = self.db.one('SELECT * FROM watchlists WHERE id=?', (id,))
        if row is None:
            raise MidasError('not_found', f"Unknown watchlist '{id}'.", 'Use watchlists_list.')
        return self._record(row)

    @staticmethod
    def summary(record):
        return {k: record[k] for k in ('id', 'name', 'created_ts', 'updated_ts')} | {'entries_count': len(record['entries'])}

    def list(self, limit=30, cursor=0):
        total = self.db.one('SELECT COUNT(*) AS n FROM watchlists')['n']
        rows = self.db.query('SELECT * FROM watchlists ORDER BY updated_ts DESC,id LIMIT ? OFFSET ?', (limit, cursor))
        items = [self.summary(self._record(r)) for r in rows]
        return {'watchlists': items, 'total': total, 'next_cursor': cursor+limit if cursor+limit < total else None}

    def _entries(self, items):
        result, seen = [], set()
        for item in items:
            entry = dict(item)
            if entry.get('snapshot_id'):
                meta = self.store.meta(entry['snapshot_id'])
                entry.update(provider=meta['provider'], symbol=meta['symbol'])
            elif not entry.get('provider') or not entry.get('symbol', '').strip():
                raise MidasError('invalid_request', 'Each entry needs a snapshot_id or provider and symbol.', 'Use snapshots_list.')
            else:
                entry['provider'] = entry['provider'].strip().lower()
                entry['symbol'] = entry['symbol'].strip()
                if not entry['provider']:
                    raise MidasError('invalid_request', 'The provider cannot be blank.', 'Use market_providers.')
                meta = self.store.latest_for_symbol(entry['symbol'], entry['provider'])
                entry['snapshot_id'] = meta['id'] if meta else None
            key = (entry['provider'], entry['symbol'].casefold())
            if key not in seen:
                seen.add(key)
                result.append({k: entry.get(k) for k in ('provider', 'symbol', 'snapshot_id')})
        return result

    def save(self, *, id=None, name=None, notes=None, entries=None, add_entries=None, remove_entries=None):
        normalized = self._entries(entries) if entries is not None else None
        additions = self._entries(add_entries) if add_entries else []
        removals = self._entries(remove_entries) if remove_entries else []
        if name is not None and not name.strip():
            raise MidasError('invalid_request', 'A watchlist needs a name.', 'Enter a descriptive name.')
        with self.db.transaction() as conn:
            if id:
                row = conn.execute('SELECT * FROM watchlists WHERE id=?', (id,)).fetchone()
                if row is None:
                    raise MidasError('not_found', f"Unknown watchlist '{id}'.", 'Use watchlists_list.')
                current = normalized if normalized is not None else json.loads(row['entries'])
                removed = {(e['provider'], e['symbol'].casefold()) for e in removals}
                current = [e for e in current if (e['provider'], e['symbol'].casefold()) not in removed]
                for added in additions:
                    index = next((i for i,e in enumerate(current) if e['provider']==added['provider'] and e['symbol'].casefold()==added['symbol'].casefold()), None)
                    if index is None:
                        current.append(added)
                    else:
                        current[index] = added
                conn.execute('UPDATE watchlists SET name=?,notes=?,entries=?,updated_ts=? WHERE id=?',
                    (name.strip() if name is not None else row['name'], notes if notes is not None else row['notes'],
                     json.dumps(current), self.clock(), id))
            else:
                if not name:
                    raise MidasError('invalid_request', 'A new watchlist needs a name.', 'Enter a descriptive name.')
                id = 'wls_' + uuid.uuid4().hex[:10]
                conn.execute('INSERT INTO watchlists VALUES (?,?,?,?,?,?)',
                    (id, name.strip(), notes or '', json.dumps(self._entries((normalized or [])+additions)), self.clock(), self.clock()))
        return self.get(id)

    def _row(self, entry, lookback, as_of):
        row = {**entry, 'status': 'no_data'}
        if not entry.get('snapshot_id'):
            return row
        try:
            snapshot = self.store.load(entry['snapshot_id'], as_of=as_of)
            values = pd.to_numeric(snapshot.column('close'), errors='coerce').dropna()
            values = values[values.map(math.isfinite)].tail(lookback+1)
            row['snapshot'] = provenance(snapshot.meta)
            if values.empty:
                return row
            first, last = float(values.iloc[0]), float(values.iloc[-1])
            row.update(status='ok', first_date=values.index[0].strftime('%Y-%m-%d'),
                       last_date=values.index[-1].strftime('%Y-%m-%d'), observations=len(values),
                       intervals=len(values)-1, last=last, change=last-first,
                       change_pct=(last/first-1)*100 if first > 0 and len(values)>1 else None,
                       drawdown_pct=float((values/values.cummax()-1).min()*100) if (values>0).all() else None)
            unavailable = []
            for metric in ('change', 'change_pct', 'drawdown_pct'):
                if row[metric] is not None and not math.isfinite(row[metric]):
                    row[metric] = None
                    unavailable.append(metric)
            if unavailable:
                row['unavailable_metrics'] = unavailable
                row['metric_note'] = 'Derived metrics exceed the finite numeric range; source values remain available.'
        except MidasError as error:
            row.update(status='error', error=str(error), code=error.code)
        return row

    def view(self, id, *, lookback=20, as_of=None, query='', currency=None, min_change_pct=None,
             max_drawdown_pct=None, sort_by='change_pct', descending=True, limit=30, cursor=0):
        if as_of:
            try:
                pd.Timestamp(as_of)
            except (ValueError, TypeError):
                raise MidasError('invalid_request', 'Invalid as_of date.', 'Use YYYY-MM-DD.') from None
        watchlist = self.get(id)
        rows = [self._row(entry, lookback, as_of) for entry in watchlist['entries']]
        if query:
            rows = [r for r in rows if query.casefold() in (r['symbol']+' '+r['provider']).casefold()]
        if currency:
            rows = [r for r in rows if r.get('snapshot', {}).get('currency') == currency.upper()]
        if min_change_pct is not None:
            rows = [r for r in rows if r.get('change_pct') is not None and r['change_pct'] >= min_change_pct]
        if max_drawdown_pct is not None:
            rows = [r for r in rows if r.get('drawdown_pct') is not None and -r['drawdown_pct'] <= max_drawdown_pct]
        rows.sort(key=lambda r: (r['provider'], r['symbol'].casefold()))
        available = [r for r in rows if r.get(sort_by) is not None]
        missing = [r for r in rows if r.get(sort_by) is None]
        available.sort(key=lambda r: r[sort_by], reverse=descending)
        rows = available + missing
        return {'watchlist': self.summary(watchlist) | {'notes': watchlist['notes']}, 'items': rows[cursor:cursor+limit], 'total': len(rows),
                'entries_total': len(watchlist['entries']), 'lookback': lookback, 'as_of': as_of,
                'next_cursor': cursor+limit if cursor+limit < len(rows) else None}

    def refresh(self, id, *, start=None, end=None):
        captured = self.get(id)
        outcomes, replacements = [], {}
        for entry in captured['entries']:
            key = (entry['provider'], entry['symbol'].casefold(), entry.get('snapshot_id'))
            try:
                if entry['provider'] == 'csv':
                    outcomes.append({**entry, 'status': 'kept', 'hint': 'Import the revised CSV and replace this entry explicitly.'})
                    continue
                meta = self.store.meta(entry['snapshot_id']) if entry.get('snapshot_id') else {}
                result = self.fetch(provider=entry['provider'], symbol=entry['symbol'],
                                    start=start if start is not None else meta.get('req_start') or None, end=end,
                                    interval={'D':'d','W':'w','M':'m','Q':'q','A':'y'}.get(meta.get('frequency'), 'd'),
                                    currency=meta.get('currency') or None, unit=meta.get('unit') or None)
                sid = result['snapshot']['id']
                replacements[key] = sid
                outcomes.append({**entry, 'status': 'ok', 'snapshot_id': sid, 'created': result['created']})
            except MidasError as error:
                outcomes.append({**entry, 'status': 'error', 'error': str(error), 'code': error.code})
        # Publish only entries that still have the captured identity/source.
        # A rename, new entry or replacement made during fetch remains intact.
        with self.db.transaction() as conn:
            row = conn.execute('SELECT entries FROM watchlists WHERE id=?', (id,)).fetchone()
            current = json.loads(row['entries'])
            for entry in current:
                key = (entry['provider'], entry['symbol'].casefold(), entry.get('snapshot_id'))
                if key in replacements:
                    entry['snapshot_id'] = replacements[key]
            conn.execute('UPDATE watchlists SET entries=?,updated_ts=? WHERE id=?', (json.dumps(current), self.clock(), id))
        published = self.get(id)
        for outcome in outcomes:
            if outcome['status'] == 'ok':
                outcome['attached'] = any(e['provider']==outcome['provider'] and e['symbol'].casefold()==outcome['symbol'].casefold()
                                         and e.get('snapshot_id')==outcome['snapshot_id'] for e in published['entries'])
        return {'watchlist': self.summary(published), 'results': outcomes, 'errors': sum(r['status']=='error' for r in outcomes)}
