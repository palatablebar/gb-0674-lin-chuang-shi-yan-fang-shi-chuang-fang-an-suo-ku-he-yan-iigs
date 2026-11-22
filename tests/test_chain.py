from fastapi.testclient import TestClient
from app import main
import zipfile, io, json

def client(tmp_path,monkeypatch):
    monkeypatch.setattr(main,'DB_PATH',str(tmp_path/'test.sqlite'));return TestClient(main.app)
def make_run(c,dataset='clean'):
    return c.post('/api/runs',json={'dataset':dataset}).json()
def sign(c,run,signer='复核-甲',revision=1):
    return c.post('/api/runs/'+run['manifest']['run_id']+'/review',json={'revision':revision,'signer':signer})

def test_sign_requires_current_grant(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);run=make_run(c)
    r=sign(c,run,signer='路人')
    assert r.status_code==422 and r.json()['code']=='SIGNER_UNAUTHORIZED'
def test_sign_requires_signer(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);run=make_run(c)
    assert c.post('/api/runs/'+run['manifest']['run_id']+'/review',json={'revision':1}).status_code==422

def test_handover_invalidates_old_chain(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);run=make_run(c);rid=run['manifest']['run_id']
    r=sign(c,run).json()
    assert r['status']=='已复核'
    f1=r['chain']['freeze'];assert f1 and f1['state']=='当前可用'
    # 换班：原复核角色权限在签署后撤销，新复核人尚未签署
    assert c.post('/api/roles/shift',json={'from':'复核-甲','to':'复核-乙'}).status_code==200
    cur=c.get('/api/runs/'+rid).json()
    assert cur['status']=='待复核'                 # 失效链不能放行
    assert cur['chain']['freeze'] is None          # 当前无可放行版本
    assert any('新复核人尚未完成签署' in b for b in cur['chain']['active']['blocks'])
    sigs=cur['chain']['signatures']
    assert len(sigs)==1 and sigs[0]['signer']=='复核-甲' and not sigs[0]['valid']
    assert '撤销' in sigs[0]['reason']             # 历史签署保留并标注失效原因
    assert sigs[0]['auth_snapshot']['holder']=='复核-甲'  # 签署当时授权记录留存
    hist=cur['chain']['freeze_history']
    assert len(hist)==1 and hist[0]['chain_id']==f1['chain_id'] and hist[0]['state']=='历史留存'  # 旧版冻结留存回看
    # 新复核人完成签署 → 新链放行，旧冻结仍留存
    r2=sign(c,run,signer='复核-乙').json()
    assert r2['status']=='已复核'
    f2=r2['chain']['freeze'];assert f2 and f2['chain_id']!=f1['chain_id']
    assert len(r2['chain']['freeze_history'])==1
    assert {s['signer'] for s in r2['chain']['signatures']}=={'复核-甲','复核-乙'}

def test_retry_adds_no_signature(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);run=make_run(c)
    sign(c,run);again=sign(c,run).json()
    assert len(again['chain']['signatures'])==1
    rid=run['manifest']['run_id']
    assert len(c.get('/api/runs/'+rid).json()['chain']['signatures'])==1
    assert len(c.get('/api/runs/'+rid).json()['chain']['freeze_history'])==0  # 重试不重复冻结

def test_incomplete_evidence_stays_blocked(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);run=make_run(c,'dirty')
    r=sign(c,run).json()
    assert r['chain']['freeze'] is None
    assert any('证据不全' in b for b in r['chain']['active']['blocks'])
    assert r['chain']['freeze_history']==[]

def test_export_references_same_chain(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);run=make_run(c)
    signed=sign(c,run).json();rid=run['manifest']['run_id']
    z=zipfile.ZipFile(io.BytesIO(c.get('/api/export/'+rid).content))
    m=json.loads(z.read('manifest.json'))
    assert m['chain_id']==signed['chain']['active']['chain_id']
    assert m['freeze']['seal']==signed['chain']['freeze']['seal']
    e=json.loads(z.read('evidence.json'))
    assert e['signatures'][0]['signer']=='复核-甲'

def test_shift_unknown_holder(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);make_run(c)
    assert c.post('/api/roles/shift',json={'from':'不存在','to':'复核-乙'}).status_code==404
    assert c.post('/api/roles/shift',json={'from':'复核-甲','to':'复核-甲'}).status_code==422
    r=c.post('/api/roles/shift',json={'from':'复核-甲','to':'复核-乙'}).json()
    roster=r['roster']
    assert any(x['holder']=='复核-甲' and x['revoked_at'] for x in roster)   # 授权与撤销记录留存
    assert any(x['holder']=='复核-乙' and not x['revoked_at'] for x in roster)
    assert c.post('/api/roles/shift',json={'from':'复核-甲','to':'复核-丙'}).status_code==409

def test_roster_seeded(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);make_run(c)
    r=c.get('/api/roles').json()
    assert r['roster'][0]['holder']=='复核-甲' and r['roster'][0]['revoked_at'] is None

def test_pages_show_chain(tmp_path,monkeypatch):
    c=client(tmp_path,monkeypatch);run=make_run(c);rid=run['manifest']['run_id']
    sign(c,run)
    for key in ('locking','archive'):
        html=c.get('/'+key+'?run_id='+rid).text
        assert '当前可用版本' in html and '签署记录' in html and '复核-甲' in html
    html=c.get('/windows?run_id='+rid).text
    assert 'chain-' in html  # 访视页页脚引用同一签署版本
