import hashlib, json

REVIEW_ROLE = '复核'
SEED_HOLDER = '复核-甲'
SEED_GRANTED_AT = '2026-11-17T09:00:00+08:00'

def make_chain_id(run_id, revision, signers):
    basis = f"{run_id}|r{revision}|{','.join(sorted(signers))}"
    return 'chain-' + hashlib.sha256(basis.encode()).hexdigest()[:12]

def active_holders(roster, role=REVIEW_ROLE):
    return {r['holder'] for r in roster if r['role'] == role and r['revoked_at'] is None}

def _loads(value):
    return json.loads(value) if isinstance(value, str) else value

def adjudicate(run_id, balance, revision, signatures, freezes, roster):
    """读取时统一推导：签署有效性、当前有效链、当前可用冻结与阻断原因。

    signatures/freezes 调用方按新到旧排序；roster 为授权名册全量（含已撤销）。
    历史签署与历史冻结只增不删，有效性永远按当前名册推导。
    """
    holders = active_holders(roster)
    sigs = []
    for s in signatures:
        reasons = []
        if s['signer'] not in holders:
            reasons.append('角色授权已撤销（换班）')
        if s['revision'] != revision:
            reasons.append('批次版本已更新')
        sigs.append({
            'signer': s['signer'], 'role': s['role'], 'revision': s['revision'],
            'signed_at': s['signed_at'], 'auth_snapshot': _loads(s['auth_snapshot']),
            'valid': not reasons, 'validity': '已失效' if reasons else '有效',
            'reason': '；'.join(reasons) or None,
        })
    active_signers = sorted({s['signer'] for s in sigs if s['valid']})
    chain_id = make_chain_id(run_id, revision, active_signers) if active_signers else None
    current, freeze_history = None, []
    for f in freezes:
        signers = _loads(f['signers'])
        entry = {'chain_id': f['chain_id'], 'revision': f['revision'], 'signers': signers,
                 'seal': f['seal'], 'created_at': f['created_at']}
        chain_ok = bool(signers) and f['revision'] == revision and all(x in holders for x in signers)
        if chain_ok and current is None:
            entry.update(state='当前可用', reason=None)
            current = entry
        else:
            if not signers or not all(x in holders for x in signers):
                why = '签署链已失效（换班撤销）'
            elif f['revision'] != revision:
                why = '签署对应旧版本'
            else:
                why = '已被新链替换'
            entry.update(state='历史留存', reason=why)
            freeze_history.append(entry)
    blocks = []
    if not active_signers:
        blocks.append('复核链已失效：原签署授权已撤销，新复核人尚未完成签署' if sigs else '尚未复核签署')
    if balance['status'] != '可锁库':
        blocks.append(f"证据不全：阻断项 {balance['blocked']} 项，继续阻断")
    return {
        'revision': revision,
        'signatures': sigs,
        'active': {'chain_id': chain_id, 'signers': active_signers,
                   'releasable': bool(active_signers) and balance['status'] == '可锁库',
                   'blocks': blocks},
        'freeze': current,
        'freeze_history': freeze_history,
        'roster': roster,
        'run_status': '已复核' if active_signers else '待复核',
    }
