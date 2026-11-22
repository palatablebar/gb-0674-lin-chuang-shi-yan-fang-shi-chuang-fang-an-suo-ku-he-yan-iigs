import sqlite3, json
from datetime import datetime, timezone
from pathlib import Path
from app.errors import BusinessError
from app.chain import REVIEW_ROLE, SEED_HOLDER, SEED_GRANTED_AT, make_chain_id, active_holders, adjudicate

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con=sqlite3.connect(path,timeout=15)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute("CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, digest TEXT UNIQUE NOT NULL, result TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT '待复核')")
    con.execute("CREATE TABLE IF NOT EXISTS roles (role TEXT NOT NULL, holder TEXT NOT NULL, granted_at TEXT NOT NULL, revoked_at TEXT)")
    con.execute("CREATE TABLE IF NOT EXISTS signatures (run_id TEXT NOT NULL, revision INTEGER NOT NULL, signer TEXT NOT NULL, role TEXT NOT NULL, signed_at TEXT NOT NULL, auth_snapshot TEXT NOT NULL, UNIQUE(run_id, signer, revision))")
    con.execute("CREATE TABLE IF NOT EXISTS freezes (run_id TEXT NOT NULL, revision INTEGER NOT NULL, chain_id TEXT NOT NULL, signers TEXT NOT NULL, seal TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(run_id, chain_id))")
    if not con.execute('SELECT 1 FROM roles LIMIT 1').fetchone():
        con.execute('INSERT INTO roles(role,holder,granted_at) VALUES(?,?,?)',(REVIEW_ROLE,SEED_HOLDER,SEED_GRANTED_AT))
    return con

def save(path, result):
    run_id=result['manifest']['run_id']
    with connect(path) as con:
        con.execute('INSERT OR IGNORE INTO runs(id,digest,result) VALUES(?,?,?)', (run_id,result['manifest']['sha256'],json.dumps(result,ensure_ascii=False)))
    return get(path,run_id)

def _chain_rows(con, run_id):
    sigs=[dict(r) for r in con.execute('SELECT * FROM signatures WHERE run_id=? ORDER BY rowid DESC',(run_id,))]
    freezes=[dict(r) for r in con.execute('SELECT * FROM freezes WHERE run_id=? ORDER BY rowid DESC',(run_id,))]
    roster=[dict(r) for r in con.execute('SELECT * FROM roles ORDER BY rowid')]
    return sigs, freezes, roster

def get(path, run_id):
    with connect(path) as con:
        row=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
        if not row: raise BusinessError('NOT_FOUND','批次不存在',404)
        sigs, freezes, roster=_chain_rows(con,run_id)
    result=json.loads(row['result'])
    chain=adjudicate(run_id,result['balance'],row['revision'],sigs,freezes,roster)
    result.update(revision=row['revision'],status=chain['run_status'],chain=chain)
    return result

def history(path):
    with connect(path) as con:
        rows=[dict(r) for r in con.execute('SELECT id,revision FROM runs ORDER BY rowid DESC LIMIT 100')]
        holders=active_holders([dict(r) for r in con.execute('SELECT * FROM roles')])
        sigs=[dict(r) for r in con.execute('SELECT run_id,signer,revision FROM signatures')]
    valid={(s['run_id'],s['revision'],s['signer']) for s in sigs if s['signer'] in holders}
    for r in rows:
        r['status']='已复核' if any(v[0]==r['id'] and v[1]==r['revision'] for v in valid) else '待复核'
    return rows

def review(path, run_id, revision, signer, role=REVIEW_ROLE):
    with connect(path) as con:
        row=con.execute('SELECT * FROM runs WHERE id=?',(run_id,)).fetchone()
        if not row: raise BusinessError('NOT_FOUND','批次不存在',404)
        if row['revision']!=revision:
            raise BusinessError('STALE_VERSION','复核版本已更新，请刷新',409)
        grant=con.execute('SELECT * FROM roles WHERE role=? AND holder=? AND revoked_at IS NULL',(role,signer)).fetchone()
        if not grant:
            raise BusinessError('SIGNER_UNAUTHORIZED','签署人未持有当前复核授权，签署被拒绝')
        dup=con.execute('SELECT 1 FROM signatures WHERE run_id=? AND signer=? AND revision=?',(run_id,signer,revision)).fetchone()
        if not dup:
            con.execute('INSERT INTO signatures(run_id,revision,signer,role,signed_at,auth_snapshot) VALUES(?,?,?,?,?,?)',
                        (run_id,revision,signer,role,now_iso(),json.dumps(dict(grant),ensure_ascii=False)))
        _refresh_freeze(con,run_id,revision,json.loads(row['result']))
    return get(path,run_id)

def _refresh_freeze(con, run_id, revision, result):
    holders={r['holder'] for r in con.execute('SELECT holder FROM roles WHERE role=? AND revoked_at IS NULL',(REVIEW_ROLE,))}
    signers=sorted({r['signer'] for r in con.execute('SELECT signer FROM signatures WHERE run_id=? AND revision=?',(run_id,revision)) if r['signer'] in holders})
    if not signers: return  # 无当前有效签署链，继续阻断
    if result['balance']['status']!='可锁库': return  # 证据不全，继续阻断
    con.execute('INSERT OR IGNORE INTO freezes(run_id,revision,chain_id,signers,seal,created_at) VALUES(?,?,?,?,?,?)',
                (run_id,revision,make_chain_id(run_id,revision,signers),json.dumps(signers,ensure_ascii=False),result['archive']['seal'],now_iso()))

def roster(path):
    with connect(path) as con:
        return [dict(r) for r in con.execute('SELECT * FROM roles ORDER BY rowid')]

def shift(path, role, old, new):
    with connect(path) as con:
        cur=con.execute('UPDATE roles SET revoked_at=? WHERE role=? AND holder=? AND revoked_at IS NULL',(now_iso(),role,old))
        if cur.rowcount!=1:
            if not con.execute('SELECT 1 FROM roles WHERE role=? AND holder=?',(role,old)).fetchone():
                raise BusinessError('HOLDER_NOT_FOUND','原持有人无授权记录',404)
            raise BusinessError('ALREADY_REVOKED','原持有人授权已撤销',409)
        if not con.execute('SELECT 1 FROM roles WHERE role=? AND holder=? AND revoked_at IS NULL',(role,new)).fetchone():
            con.execute('INSERT INTO roles(role,holder,granted_at) VALUES(?,?,?)',(role,new,now_iso()))
    return roster(path)
