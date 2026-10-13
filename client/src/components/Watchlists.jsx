import React, { useCallback, useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import { useApp } from '../App.jsx';
import { ErrorBox, Field, Provenance } from './ui.jsx';
import { num, signClass } from '../format.js';
import { useContextProvider, usePublishView, useRefreshOnTurnEnd } from '../assistant-hooks.js';

export default function Watchlists({ selected, onOpen, onUpdated }) {
  const { lang, t } = useApp();
  const es = lang !== 'en';
  const say = (a, b) => es ? a : b;
  const [lists, setLists] = useState([]);
  const [id, setId] = useState('');
  const [name, setName] = useState('');
  const [nameDirty, setNameDirty] = useState(false);
  const [view, setView] = useState(null);
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [outcome, setOutcome] = useState(null);
  const [filters, setFilters] = useState({ lookback: 20, as_of: '', query: '', currency: '', min_change_pct: '', max_drawdown_pct: '', sort_by: 'change_pct' });
  const [cursor, setCursor] = useState(0);
  const [previous, setPrevious] = useState([]);
  const request = useRef(0);
  const loadLists = useCallback(async () => {
    let items = [], next = 0;
    do {
      const result = await api.watchlists({ cursor: next, limit: 100 });
      items.push(...result.watchlists); next = result.next_cursor;
    } while (next !== null);
    setLists(items);
  }, []);
  useEffect(() => { loadLists().catch(setError); }, [loadLists]);
  useEffect(() => { if (!nameDirty && id) setName(lists.find((list) => list.id === id)?.name || ''); }, [lists, id, nameDirty]);
  const loadView = useCallback(async () => {
    const sequence = ++request.current;
    if (!id) { setView(null); setLoading(false); return; }
    setLoading(true);
    const body = { ...filters, limit: 20, cursor };
    for (const key of ['as_of', 'currency', 'min_change_pct', 'max_drawdown_pct']) {
      if (body[key] === '') delete body[key];
      else if (key.endsWith('_pct')) body[key] = Number(body[key]);
    }
    try {
      const result = await api.watchlistView(id, body);
      if (sequence === request.current) { setView(result); setError(null); }
    } catch (err) { if (sequence === request.current) setError(err); }
    finally { if (sequence === request.current) setLoading(false); }
  }, [id, filters, cursor]);
  useEffect(() => { loadView(); }, [loadView]);
  useRefreshOnTurnEnd(() => { loadLists().catch(setError); loadView(); });
  usePublishView('watchlist', { id, filters });
  useContextProvider('watchlist:editor', () => ({ kind: 'watchlist-form', draft: true, dirty: nameDirty,
    fields: { id: id || null, name, selected_snapshot_ids: selected, filters } }));
  const updateFilter = (key, value) => { setCursor(0); setPrevious([]); setFilters((old) => ({ ...old, [key]: value })); };
  const choose = (value) => {
    setId(value); setName(lists.find((list) => list.id === value)?.name || '');
    setNameDirty(false); setCursor(0); setPrevious([]); setView(null); setOutcome(null);
  };
  async function save(mode) {
    setBusy(true); setError(null);
    try {
      const body = mode === 'rename' ? { id, name } : { name, entries: selected.map((snapshot_id) => ({ snapshot_id })) };
      const saved = await api.watchlistSave(body);
      await loadLists(); setId(saved.id); setName(saved.name); setNameDirty(false); setCursor(0);
      setOutcome(say('Lista guardada.', 'Watchlist saved.'));
      if (mode === 'rename') await loadView();
    } catch (err) { setError(err); } finally { setBusy(false); }
  }
  async function changeEntries(body) {
    setBusy(true); setError(null);
    try {
      await api.watchlistSave({ id, ...body });
      await loadLists(); await loadView();
    } catch (err) { setError(err); } finally { setBusy(false); }
  }
  async function refresh() {
    setBusy(true); setError(null);
    try {
      const result = await api.watchlistRefresh(id, {});
      setOutcome(say(`Actualización terminada: ${result.errors} fallos.`, `Refresh finished: ${result.errors} errors.`));
      await loadLists(); await loadView(); await onUpdated();
    } catch (err) { setError(err); } finally { setBusy(false); }
  }
  return <section className="panel space-y-3" aria-labelledby="watchlists-heading" aria-busy={loading || busy}>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <h2 id="watchlists-heading">{say('Listas de seguimiento', 'Watchlists')}</h2>
      {id && <button type="button" className="btn btn-sm" disabled={busy} onClick={refresh}>{say('Actualizar datos', 'Refresh data')}</button>}
    </div>
    <div className="grid gap-3 sm:grid-cols-2">
      <Field label={say('Lista guardada', 'Saved watchlist')}><select className="field" value={id} onChange={(e) => choose(e.target.value)}>
        <option value="">{say('Nueva lista', 'New watchlist')}</option>
        {lists.map((list) => <option key={list.id} value={list.id}>{list.name} · {list.entries_count}</option>)}
      </select></Field>
      <Field label={say('Nombre de la lista', 'Watchlist name')}><input className="field" value={name} maxLength={200} onChange={(e) => { setName(e.target.value); setNameDirty(true); }} /></Field>
    </div>
    <div className="flex flex-wrap items-center gap-2">
      <button type="button" className="btn btn-primary" disabled={busy || !name.trim() || !selected.length} onClick={() => save('new')}>
        {say('Guardar selección como nueva lista', 'Save selection as new watchlist')}</button>
      {id && <button type="button" className="btn" disabled={busy || !name.trim() || !nameDirty} onClick={() => save('rename')}>{say('Renombrar', 'Rename')}</button>}
      {id && <button type="button" className="btn" disabled={busy || !selected.length} onClick={() => changeEntries({ add_entries: selected.map((snapshot_id) => ({ snapshot_id })) })}>{say('Añadir selección a esta lista', 'Add selection to this watchlist')}</button>}
      <span className="help">{say(`${selected.length} ${selected.length === 1 ? 'serie seleccionada' : 'series seleccionadas'} en Mercado.`, `${selected.length} series selected in Market.`)}</span>
    </div>
    {!lists.length && <p className="help">{say('Selecciona series en Mercado y guárdalas para recuperarlas aquí.', 'Select series in Market and save them to return to them here.')}</p>}
    <ErrorBox error={error} t={t} />
    {outcome && <p className="help" role="status">{outcome}</p>}
    {loading && <p className="help" role="status">{say('Actualizando vista…', 'Updating view…')}</p>}
    {id && <>
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        <Field label={say('Intervalos', 'Intervals')}><input className="field" type="number" min={1} value={filters.lookback} onChange={(e) => updateFilter('lookback', Math.max(1, Number(e.target.value)))} /></Field>
        <Field label={say('Hasta la fecha', 'As of date')}><input className="field" type="date" value={filters.as_of} onChange={(e) => updateFilter('as_of', e.target.value)} /></Field>
        <Field label={say('Buscar símbolo', 'Find symbol')}><input className="field" value={filters.query} onChange={(e) => updateFilter('query', e.target.value)} /></Field>
        <Field label={say('Moneda', 'Currency')}><input className="field" value={filters.currency} maxLength={3} onChange={(e) => updateFilter('currency', e.target.value.toUpperCase())} /></Field>
        <Field label={say('Variación mínima (%)', 'Minimum change (%)')}><input className="field" type="number" step="any" value={filters.min_change_pct} onChange={(e) => updateFilter('min_change_pct', e.target.value)} /></Field>
        <Field label={say('Caída máxima (%)', 'Maximum drawdown (%)')}><input className="field" type="number" min={0} step="any" value={filters.max_drawdown_pct} onChange={(e) => updateFilter('max_drawdown_pct', e.target.value)} /></Field>
      </div>
      <div className="flex flex-wrap items-center gap-2"><label className="flex items-center gap-2"><span className="label">{say('Ordenar', 'Sort')}</span>
        <select className="field" value={filters.sort_by} onChange={(e) => updateFilter('sort_by', e.target.value)}>
          <option value="change_pct">{say('Variación', 'Change')}</option><option value="drawdown_pct">{say('Caída', 'Drawdown')}</option><option value="symbol">{say('Símbolo', 'Symbol')}</option>
        </select></label><span className="help">{view && say(`${view.total} de ${view.entries_total} series.`, `${view.total} of ${view.entries_total} series.`)}</span></div>
      {view && <div className="overflow-x-auto"><table className="num"><thead><tr>
        {[say('Serie', 'Series'), say('Último dato', 'Last value'), say('Variación', 'Change'), say('Caída', 'Drawdown'), say('Periodo', 'Period'), say('Fuente', 'Source')].map((label) => <th key={label}>{label}</th>)}
      </tr></thead><tbody>{view.items.map((row) => <tr key={`${row.provider}:${row.symbol}`}>
        <td><button className="btn btn-sm" type="button" disabled={!row.snapshot} onClick={() => onOpen(row.snapshot)}>{row.symbol}</button><div className="help">{row.provider}</div>
          <button type="button" className="btn btn-sm" disabled={busy} onClick={() => changeEntries({ remove_entries: [{ provider: row.provider, symbol: row.symbol }] })}>{say('Quitar de la lista', 'Remove from watchlist')}</button></td>
        <td>{row.status === 'ok' ? num(row.last, 2, lang) : '—'}<div className="help">{row.snapshot?.currency || row.snapshot?.unit}</div></td>
        <td className={signClass(row.change_pct)}>{row.change_pct == null ? '—' : `${num(row.change_pct, 2, lang)} %`}</td>
        <td>{row.drawdown_pct == null ? '—' : `${num(row.drawdown_pct, 2, lang)} %`}</td>
        <td>{row.first_date || '—'}<br />{row.last_date || '—'}<div className="help">{row.observations ?? 0} · {row.snapshot?.frequency}</div></td>
        <td>{row.snapshot && <Provenance snap={row.snapshot} t={t} />}{row.status !== 'ok' && <span className="help">{row.error || say('Sin datos hasta esa fecha.', 'No data as of that date.')}</span>}
          {row.unavailable_metrics?.length > 0 && <p className="help">{say('Alguna métrica excede el rango numérico; el dato original se conserva.', 'A derived metric exceeds the numeric range; the original value is preserved.')}</p>}</td>
      </tr>)}</tbody></table>{!view.items.length && <p className="help py-3">{say('No hay series que cumplan estos filtros.', 'No series match these filters.')}</p>}</div>}
      <div className="flex items-center gap-2"><button className="btn btn-sm" type="button" disabled={!previous.length} onClick={() => { setCursor(previous[previous.length-1]); setPrevious((old) => old.slice(0,-1)); }}>{say('Anterior', 'Previous')}</button>
        <button className="btn btn-sm" type="button" disabled={view?.next_cursor == null} onClick={() => { setPrevious((old) => [...old,cursor]); setCursor(view.next_cursor); }}>{say('Siguiente', 'Next')}</button></div>
      <p className="help">{say('Cada fila conserva su moneda, frecuencia y fechas. La variación usa hasta el número de intervalos elegido; los porcentajes sin una base positiva quedan sin calcular.', 'Each row keeps its currency, frequency and dates. Change uses up to the chosen observation intervals; percentages without a positive base remain uncomputed.')}</p>
    </>}
  </section>;
}
