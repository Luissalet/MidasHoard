import hashlib
import json

import pandas as pd
import pytest

from conftest import tool
from midas_hoard.errors import MidasError
from midas_hoard.providers.base import FetchResult
from midas_hoard.providers.fake import FakeProvider


def snapshot(svc, symbol, values, currency='EUR', frequency='D'):
    frame = pd.DataFrame({'close': [float(v) for v in values]}, index=pd.date_range('2024-01-01', periods=len(values)))
    result = FetchResult(df=frame, provider_symbol=symbol, currency=currency, unit=currency, frequency=frequency)
    return svc.store.save(result, FakeProvider(svc.http), symbol, req_start=None, req_end=None)[0]


def save(svc, name, metas):
    return tool(svc, 'watchlist_save', name=name, entries=[{'snapshot_id': m['id']} for m in metas])


def test_saved_list_partial_updates_membership_and_immutable_sources(svc):
    a=snapshot(svc,'A',[10,12]);b=snapshot(svc,'B',[20,18]);c=snapshot(svc,'C',[4,5])
    before={m['id']:hashlib.sha256((svc.store.dir/(m['id']+'.csv')).read_bytes()).hexdigest() for m in (a,b,c)}
    watch=save(svc,'Observados',[a,b,a])
    assert watch['entries_count']==2
    tool(svc,'watchlist_save',id=watch['id'],name='Renombrada',notes='Una nota')
    tool(svc,'watchlist_save',id=watch['id'],add_entries=[{'snapshot_id':c['id']}],remove_entries=[{'provider':'fake','symbol':'A'}])
    current=svc.watchlists.get(watch['id'])
    assert current['name']=='Renombrada' and current['notes']=='Una nota'
    assert [e['symbol'] for e in current['entries']]==['B','C']
    assert before=={m['id']:hashlib.sha256((svc.store.dir/(m['id']+'.csv')).read_bytes()).hexdigest() for m in (a,b,c)}
    assert tool(svc,'watchlists_list')['watchlists'][0]['entries_count']==2


def test_metrics_filters_cutoff_and_unknown_entries(svc):
    a=snapshot(svc,'A',[100,120,90,110]);b=snapshot(svc,'B',[100,80,60,50],'USD')
    watch=save(svc,'Conjunto',[a,b])
    tool(svc,'watchlist_save',id=watch['id'],add_entries=[{'provider':'fake','symbol':'not-fetched'}])
    view=tool(svc,'watchlist_view',id=watch['id'],lookback=3)
    first=view['items'][0]
    assert first['symbol']=='A' and first['change_pct']==pytest.approx(10)
    assert first['drawdown_pct']==pytest.approx(-25) and first['intervals']==3
    assert first['snapshot']['sha256']==a['sha256']
    assert view['items'][-1]['status']=='no_data'
    filtered=tool(svc,'watchlist_view',id=watch['id'],min_change_pct=0,max_drawdown_pct=30,currency='eur')
    assert [r['symbol'] for r in filtered['items']]==['A']
    cut=tool(svc,'watchlist_view',id=watch['id'],as_of='2024-01-02',query='A')
    assert cut['items'][0]['last']==120 and cut['items'][0]['last_date']=='2024-01-02'
    early=tool(svc,'watchlist_view',id=watch['id'],as_of='2023-12-31')
    assert all(r['status']=='no_data' for r in early['items'])


def test_zero_base_and_nonfinite_values_never_produce_json_nan(svc):
    a=snapshot(svc,'ZERO',[0,float('nan'),float('inf'),2]);b=snapshot(svc,'SINGLE',[5])
    watch=save(svc,'Bases',[a,b])
    rows=tool(svc,'watchlist_view',id=watch['id'])['items']
    assert all(r['change_pct'] is None for r in rows)
    assert next(r for r in rows if r['symbol']=='ZERO')['drawdown_pct'] is None
    json.dumps(rows,allow_nan=False)


def test_integrity_failure_stays_visible_and_does_not_hide_other_assets(svc):
    a=snapshot(svc,'A',[10,12]);b=snapshot(svc,'B',[20,18]);watch=save(svc,'Integridad',[a,b])
    (svc.store.dir/(a['id']+'.csv')).write_text('broken',encoding='utf-8')
    view=tool(svc,'watchlist_view',id=watch['id'])
    assert len(view['items'])==2
    assert next(r for r in view['items'] if r['symbol']=='A')['code']=='integrity_error'
    assert next(r for r in view['items'] if r['symbol']=='B')['status']=='ok'


def test_large_mcp_pages_do_not_skip_rows_after_budget_trimming(svc):
    metas=[snapshot(svc,f'S{i:02}',[10,11]) for i in range(36)]
    svc.db.execute('UPDATE snapshots SET terms=?',('Large provider attribution ' * 110,))
    watch=save(svc,'Muchos',[*metas])
    cursor, ids=0,[]
    while cursor is not None:
        result=tool(svc,'watchlist_view',id=watch['id'],limit=100,cursor=cursor)
        assert not result.get('truncated')
        ids.extend(r['snapshot_id'] for r in result['items'])
        cursor=result['next_cursor']
    assert len(ids)==len(set(ids))==36


def test_refresh_partial_failures_and_concurrent_rename_and_addition(svc,monkeypatch):
    a=snapshot(svc,'A',[10,12]);b=snapshot(svc,'B',[20,18]);c=snapshot(svc,'C',[4,5])
    replacement=snapshot(svc,'A',[10,13]);watch=save(svc,'Antes',[a,b])
    source=(svc.store.dir/(a['id']+'.csv')).read_bytes()
    def fetch(**kwargs):
        if kwargs['symbol']=='A':
            tool(svc,'watchlist_save',id=watch['id'],name='Mientras actualiza',add_entries=[{'snapshot_id':c['id']}])
            return {'snapshot':{'id':replacement['id']},'created':True}
        raise MidasError('provider_unavailable','Provider temporarily unavailable.')
    monkeypatch.setattr(svc.watchlists,'fetch',fetch)
    result=tool(svc,'watchlist_refresh',id=watch['id'])
    current=svc.watchlists.get(watch['id'])
    assert result['errors']==1 and result['results'][0]['attached']
    assert current['name']=='Mientras actualiza'
    assert {e['symbol']:e['snapshot_id'] for e in current['entries']}=={'A':replacement['id'],'B':b['id'],'C':c['id']}
    assert (svc.store.dir/(a['id']+'.csv')).read_bytes()==source


def test_refresh_keeps_concurrent_source_replacement_and_csv(svc,monkeypatch):
    a=snapshot(svc,'A',[10,12]);new=snapshot(svc,'A',[10,14]);rendered=snapshot(svc,'A',[10,13])
    csv=svc.market_fetch(provider='csv',symbol='CSV',currency='EUR',options={'csv_text':'date,close\n2024-01-01,4\n2024-01-02,5\n'})['snapshot']
    watch=save(svc,'Preservar',[a,csv])
    def fetch(**kwargs):
        tool(svc,'watchlist_save',id=watch['id'],add_entries=[{'snapshot_id':new['id']}])
        return {'snapshot':{'id':rendered['id']},'created':True}
    monkeypatch.setattr(svc.watchlists,'fetch',fetch)
    result=tool(svc,'watchlist_refresh',id=watch['id'])
    assert result['results'][0]['attached'] is False
    assert result['results'][1]['status']=='kept'
    assert svc.watchlists.get(watch['id'])['entries'][0]['snapshot_id']==new['id']


def test_api_and_native_agent_share_watchlist_workflow(client):
    meta=client.svc.market_fetch(provider='fake',symbol='fake.up')['snapshot']
    response=client.post('/api/watchlists',json={'name':'Desde interfaz','entries':[{'snapshot_id':meta['id']}]})
    assert response.status_code==200,response.text
    id=response.json()['id']
    assert client.get('/api/watchlists').json()['watchlists'][0]['id']==id
    view=client.post(f'/api/watchlists/{id}/view',json={'lookback':8}).json()
    agent=client.post('/api/agent/call',headers=client.bearer,json={'name':'watchlist_view','arguments':{'id':id,'lookback':8}})
    assert agent.status_code==200,agent.text
    assert agent.json()['items']==view['items']
    assert client.post(f'/api/watchlists/{id}/refresh',json={}).status_code==200


@pytest.mark.parametrize('changes',[{'name':' '},{'entries':[{}]},{'entries':[{'snapshot_id':'missing'}]}])
def test_invalid_save_does_not_destroy_previous_list(svc,changes):
    a=snapshot(svc,'A',[1,2]);watch=save(svc,'Válida',[a]);before=svc.watchlists.get(watch['id'])
    with pytest.raises(MidasError):tool(svc,'watchlist_save',id=watch['id'],**changes)
    assert svc.watchlists.get(watch['id'])==before
